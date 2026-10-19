"""
This is the safety and integrity engine.. it inspects each parsed Feature object and tests them against rules defined in config/validation.yaml. Some rules are:
    - absolute coordinate ranges ([-90..90, [-180...180]])
    - operational bounding box
    - declared distance logic
    - promotion gate threshold evaluator
"""
import re
from pipeline.config import load_validation_config
from typing import List, Dict, Any, Optional, Tuple
from pipeline.models.feature import Feature, FeatureType
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity

class ValidationEngine:
    def __init__(self, validation_cfg: Optional[Dict[str, Any]] = None):
        self.cfg = validation_cfg if validation_cfg is not None else load_validation_config()

        # 1. Absolute Geographical Bounds
        self.coord_bounds = self.cfg["coordinate_bounds"]

        # 2. Operational FIR Bounding Box & Configured Severity
        self.bbox_cfg = self.cfg["operational_bounding_box"]
        margin = float(self.bbox_cfg["margin_degrees"])
        self.bbox_min_lat = float(self.bbox_cfg["min_lat"]) - margin
        self.bbox_max_lat = float(self.bbox_cfg["max_lat"]) + margin
        self.bbox_min_lon = float(self.bbox_cfg["min_lon"]) - margin
        self.bbox_max_lon = float(self.bbox_cfg["max_lon"]) + margin
        # DYNAMIC SEVERITY: reads directly from YAML ("warning" vs "error")
        self.bbox_severity = IssueSeverity(self.bbox_cfg["severity"].lower())

        # 3. Format Constraints from Config
        self.icao_regex = re.compile(self.cfg["icao_format"])
        self.rwy_regex = re.compile(self.cfg["runway_designator_format"])

        # 4. Declared Distance Rule Selection Flags
        self.dist_cfg = self.cfg["declared_distances"]
        self.require_tora_le_toda = bool(self.dist_cfg["require_tora_le_toda"])
        self.require_asda_ge_tora = bool(self.dist_cfg["require_asda_ge_tora"])
        self.require_lda_positive = bool(self.dist_cfg["require_lda_positive"])

        # 5. Promotion Gate Thresholds
        self.gate_cfg = self.cfg["promotion_gate"]
        self.max_error_issues = int(self.gate_cfg["max_error_issues"])
        self.max_warning_issues = int(self.gate_cfg["max_warning_issues"])
        self.max_disappeared_runways_pct = float(self.gate_cfg["max_disappeared_runways_pct"])
        self.max_disappeared_features_pct = float(self.gate_cfg["max_disappeared_features_pct"])

    def validate_feature(self, feature: Feature, publication_id: str) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []

        # 1. Natural Key format check using configured regex
        parts = feature.natural_key.split("/")
        icao_part = parts[0] if parts else ""

        if not self.icao_regex.match(icao_part):
            issues.append(ValidationIssue(
                publication_id=publication_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_ICAO_FORMAT",
                feature_key=feature.natural_key,
                section=feature.feature_type.value,
                message=f"Invalid ICAO code format: '{icao_part}'",
                raw_snippet=icao_part
            ))

        if feature.feature_type == FeatureType.RUNWAY_DIRECTION:
            desig_part = parts[-1] if len(parts) >= 3 else ""
            if not self.rwy_regex.match(desig_part):
                issues.append(ValidationIssue(
                    publication_id=publication_id,
                    severity=IssueSeverity.ERROR,
                    rule_id="RULE_NATURAL_KEY_FORMAT",
                    feature_key=feature.natural_key,
                    section="AD-2.12",
                    message=f"Invalid natural_key format for runwayDirection: '{feature.natural_key}'",
                    raw_snippet=feature.natural_key
                ))
        elif feature.feature_type == FeatureType.RUNWAY:
            pair_part = parts[-1] if len(parts) >= 3 else ""
            pair_tokens = pair_part.split("-")
            if len(pair_tokens) != 2 or not all(self.rwy_regex.match(p) for p in pair_tokens):
                issues.append(ValidationIssue(
                    publication_id=publication_id,
                    severity=IssueSeverity.ERROR,
                    rule_id="RULE_NATURAL_KEY_FORMAT",
                    feature_key=feature.natural_key,
                    section="AD-2.12",
                    message=f"Invalid natural_key format for runway physical pair: '{feature.natural_key}'",
                    raw_snippet=feature.natural_key
                ))

        # 2. Geometry coordinate validation
        if feature.geometry and feature.geometry.coordinates:
            coords_to_check = []
            if feature.geometry.type == "Point":
                coords_to_check.append(feature.geometry.coordinates)
            elif feature.geometry.type == "LineString":
                coords_to_check.extend(feature.geometry.coordinates)
            elif feature.geometry.type == "Polygon":
                for ring in feature.geometry.coordinates:
                    coords_to_check.extend(ring)

            for pt in coords_to_check:
                if len(pt) >= 2:
                    lon, lat = pt[0], pt[1]
                    # Hard errors: Absolute geographical bounds
                    if not (self.coord_bounds["latitude_min"] <= lat <= self.coord_bounds["latitude_max"]) or \
                       not (self.coord_bounds["longitude_min"] <= lon <= self.coord_bounds["longitude_max"]):
                        issues.append(ValidationIssue(
                            publication_id=publication_id,
                            severity=IssueSeverity.ERROR,
                            rule_id="RULE_COORD_RANGE",
                            feature_key=feature.natural_key,
                            section="AD-2.12",
                            message=f"Coordinates out of absolute bounds: [{lon}, {lat}]",
                            raw_snippet=f"[{lon}, {lat}]"
                        ))
                    # Configured Severity: Operational bounding box check
                    elif not (self.bbox_min_lat <= lat <= self.bbox_max_lat and self.bbox_min_lon <= lon <= self.bbox_max_lon):
                        issues.append(ValidationIssue(
                            publication_id=publication_id,
                            severity=self.bbox_severity,
                            rule_id="RULE_OPERATIONAL_BBOX",
                            feature_key=feature.natural_key,
                            section="AD-2.12",
                            message=f"Coordinates outside operational FIR bounding box: [{lon}, {lat}]",
                            raw_snippet=f"[{lon}, {lat}]"
                        ))

        # 3. Declared distance integrity relations (governed by require_* flags in validation.yaml)
        if feature.feature_type == FeatureType.RUNWAY_DIRECTION:
            tora_fld = feature.fields.get("tora")
            toda_fld = feature.fields.get("toda")
            asda_fld = feature.fields.get("asda")
            lda_fld = feature.fields.get("lda")

            tora = tora_fld.value if tora_fld else None
            toda = toda_fld.value if toda_fld else None
            asda = asda_fld.value if asda_fld else None
            lda = lda_fld.value if lda_fld else None

            # TORA <= TODA (conditional on require_tora_le_toda)
            if self.require_tora_le_toda and tora is not None and toda is not None:
                if tora > toda:
                    issues.append(ValidationIssue(
                        publication_id=publication_id,
                        severity=IssueSeverity.ERROR,
                        rule_id="RULE_DECLARED_DIST_TORA_LE_TODA",
                        feature_key=feature.natural_key,
                        section="AD-2.13",
                        message=f"Declared distance violation: TORA ({tora}m) exceeds TODA ({toda}m)",
                        raw_snippet=f"TORA: {tora}, TODA: {toda}"
                    ))

            # ASDA >= TORA (conditional on require_asda_ge_tora)
            if self.require_asda_ge_tora and asda is not None and tora is not None:
                if asda < tora:
                    issues.append(ValidationIssue(
                        publication_id=publication_id,
                        severity=IssueSeverity.ERROR,
                        rule_id="RULE_DECLARED_DIST_ASDA_GE_TORA",
                        feature_key=feature.natural_key,
                        section="AD-2.13",
                        message=f"Declared distance violation: ASDA ({asda}m) is less than TORA ({tora}m)",
                        raw_snippet=f"ASDA: {asda}, TORA: {tora}"
                    ))

            # LDA > 0 (conditional on require_lda_positive)
            if self.require_lda_positive and lda is not None:
                if lda <= 0:
                    issues.append(ValidationIssue(
                        publication_id=publication_id,
                        severity=IssueSeverity.ERROR,
                        rule_id="RULE_DECLARED_DIST_LDA_POSITIVE",
                        feature_key=feature.natural_key,
                        section="AD-2.13",
                        message=f"Declared distance violation: LDA ({lda}m) must be greater than zero",
                        raw_snippet=f"LDA: {lda}"
                    ))

        return issues

    def validate_features(self, features: List[Feature], publication_id: str) -> List[ValidationIssue]:
        all_issues = []
        for feat in features:
            all_issues.extend(self.validate_feature(feat, publication_id))
        return all_issues

    def evaluate_promotion_gate(self, issues: List[ValidationIssue]) -> Tuple[bool, str]:
        """
        Evaluates whether the publication passes the promotion gate based on
        declarative thresholds configured in validation.yaml: promotion_gate.
        """
        error_count = sum(1 for i in issues if i.severity == IssueSeverity.ERROR)
        warning_count = sum(1 for i in issues if i.severity == IssueSeverity.WARNING)

        if error_count > self.max_error_issues:
            return False, f"Hard error count ({error_count}) exceeds allowed maximum ({self.max_error_issues})"
        if warning_count > self.max_warning_issues:
            return False, f"Warning count ({warning_count}) exceeds allowed maximum ({self.max_warning_issues})"

        return True, "All promotion gate thresholds satisfied."
