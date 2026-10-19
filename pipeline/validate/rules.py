import re
from typing import List, Dict, Any, Optional, Tuple, Set

from pipeline.models.feature import Feature, FeatureType
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.config import load_validation_config

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
        self.bbox_severity = IssueSeverity(self.bbox_cfg["severity"].lower())

        # 3. Dynamic Natural Key Format Mapping from Config (Zero hardcoded types in Python)
        raw_key_formats = self.cfg.get("natural_key_formats", {})
        self.natural_key_regexes: Dict[str, re.Pattern] = {
            ft: re.compile(pat) for ft, pat in raw_key_formats.items()
        }

        # 4. Declared Distance Rule Selection Flags
        self.dist_cfg = self.cfg["declared_distances"]
        self.require_tora_le_toda = bool(self.dist_cfg["require_tora_le_toda"])
        self.require_asda_ge_tora = bool(self.dist_cfg["require_asda_ge_tora"])
        self.require_lda_positive = bool(self.dist_cfg["require_lda_positive"])

        # 5. Semantic Integrity Rule Selection Flags
        rules_cfg = self.cfg.get("rules", {})
        self.require_resolved_waypoints = bool(rules_cfg.get("require_resolved_waypoints", True))
        self.require_unique_natural_keys = bool(rules_cfg.get("require_unique_natural_keys", True))
        self.require_limit_units = bool(rules_cfg.get("require_limit_units", True))

        # 6. Promotion Gate Thresholds
        self.gate_cfg = self.cfg["promotion_gate"]
        self.max_error_issues = int(self.gate_cfg["max_error_issues"])
        self.max_warning_issues = int(self.gate_cfg["max_warning_issues"])
        self.max_disappeared_runways_pct = float(self.gate_cfg["max_disappeared_runways_pct"])
        self.max_disappeared_features_pct = float(self.gate_cfg["max_disappeared_features_pct"])

    def validate_feature(self, feature: Feature, publication_id: str) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []

        # 1. Natural Key format check iterating the config mapping dynamically
        ft_name = feature.feature_type.value
        fmt_regex = self.natural_key_regexes.get(ft_name)
        if fmt_regex and not fmt_regex.match(feature.natural_key):
            issues.append(ValidationIssue(
                publication_id=publication_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_NATURAL_KEY_FORMAT",
                feature_key=feature.natural_key,
                section=ft_name,
                message=f"Natural key '{feature.natural_key}' violates format pattern for {ft_name}",
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
                            section="coordinates",
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
                            section="coordinates",
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

            if self.require_tora_le_toda and tora is not None and toda is not None:
                if tora > toda:
                    issues.append(ValidationIssue(
                        publication_id=publication_id,
                        severity=IssueSeverity.ERROR,
                        rule_id="RULE_DECLARED_DIST_TORA_LE_TODA",
                        feature_key=feature.natural_key,
                        section="AD 2.13",
                        message=f"Declared distance violation: TORA ({tora}m) exceeds TODA ({toda}m)",
                        raw_snippet=f"TORA: {tora}, TODA: {toda}"
                    ))

            if self.require_asda_ge_tora and asda is not None and tora is not None:
                if asda < tora:
                    issues.append(ValidationIssue(
                        publication_id=publication_id,
                        severity=IssueSeverity.ERROR,
                        rule_id="RULE_DECLARED_DIST_ASDA_GE_TORA",
                        feature_key=feature.natural_key,
                        section="AD 2.13",
                        message=f"Declared distance violation: ASDA ({asda}m) is less than TORA ({tora}m)",
                        raw_snippet=f"ASDA: {asda}, TORA: {tora}"
                    ))

            if self.require_lda_positive and lda is not None:
                if lda <= 0:
                    issues.append(ValidationIssue(
                        publication_id=publication_id,
                        severity=IssueSeverity.ERROR,
                        rule_id="RULE_DECLARED_DIST_LDA_POSITIVE",
                        feature_key=feature.natural_key,
                        section="AD 2.13",
                        message=f"Declared distance violation: LDA ({lda}m) must be greater than zero",
                        raw_snippet=f"LDA: {lda}"
                    ))

        # 4. Vertical limit unit and reference check (RULE_LIMIT_UNIT_MISSING)
        if self.require_limit_units:
            for lim_field in ("upperLimit", "lowerLimit"):
                fld = feature.fields.get(lim_field)
                if fld and isinstance(fld.value, dict):
                    val = fld.value.get("value")
                    unit = fld.value.get("unit")
                    ref = fld.value.get("reference")
                    if val is not None and (unit is None or ref is None):
                        issues.append(ValidationIssue(
                            publication_id=publication_id,
                            severity=IssueSeverity.WARNING,
                            rule_id="RULE_LIMIT_UNIT_MISSING",
                            feature_key=feature.natural_key,
                            section=fld.section,
                            message=f"Vertical limit in {lim_field} has value {val} but missing unit or reference",
                            raw_snippet=str(fld.value)
                        ))

        return issues

    def validate_features(self, features: List[Feature], publication_id: str) -> List[ValidationIssue]:
        all_issues = []

        # Duplicate naturalKey check within cycle (RULE_DUPLICATE_NATURAL_KEY)
        if self.require_unique_natural_keys:
            seen_keys: Set[str] = set()
            for feat in features:
                if feat.natural_key in seen_keys:
                    all_issues.append(ValidationIssue(
                        publication_id=publication_id,
                        severity=IssueSeverity.ERROR,
                        rule_id="RULE_DUPLICATE_NATURAL_KEY",
                        feature_key=feat.natural_key,
                        section="integrity",
                        message=f"Duplicate naturalKey '{feat.natural_key}' detected in cycle",
                        raw_snippet=feat.natural_key
                    ))
                seen_keys.add(feat.natural_key)

        for feat in features:
            all_issues.extend(self.validate_feature(feat, publication_id))

        return all_issues

    def evaluate_promotion_gate(
        self,
        issues: List[ValidationIssue],
        disappeared_runways_pct: float = 0.0,
        disappeared_features_pct: float = 0.0
    ) -> Tuple[bool, str]:
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
        if disappeared_runways_pct > self.max_disappeared_runways_pct:
            return False, f"Disappeared runways ({disappeared_runways_pct}%) exceeds allowed threshold ({self.max_disappeared_runways_pct}%)"
        if disappeared_features_pct > self.max_disappeared_features_pct:
            return False, f"Disappeared features ({disappeared_features_pct}%) exceeds allowed threshold ({self.max_disappeared_features_pct}%)"

        return True, "All promotion gate thresholds satisfied."
