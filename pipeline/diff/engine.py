"""
Deterministic cycle diff engine.
Compares features by natural_key and content_hash, producing field-level diffs
and calculating disappeared record percentages for the promotion gate.
"""
from typing import List, Dict, Any, Tuple
from pydantic import BaseModel, Field

from pipeline.models.feature import Feature, FeatureType
from pipeline.models.change import Change, ChangeStatus, FieldChange
from pipeline.utils.hash_utils import normalize_value

class DiffReport(BaseModel):
    from_cycle: str
    to_cycle: str
    changes: List[Change] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)
    disappeared_features_pct: float = 0.0
    disappeared_runways_pct: float = 0.0

class DiffEngine:
    @staticmethod
    def compare_features(
        from_cycle: str,
        to_cycle: str,
        from_features: List[Feature],
        to_features: List[Feature]
    ) -> DiffReport:
        from_map = {f.natural_key: f for f in from_features}
        to_map = {f.natural_key: f for f in to_features}

        changes: List[Change] = []
        new_count = 0
        modified_count = 0
        deleted_count = 0
        unchanged_count = 0

        # 1. Detect NEW and MODIFIED features
        for key, to_feat in to_map.items():
            if key not in from_map:
                new_count += 1
                changes.append(Change(
                    from_cycle=from_cycle,
                    to_cycle=to_cycle,
                    feature_key=key,
                    feature_type=to_feat.feature_type,
                    status=ChangeStatus.NEW,
                    field_changes=[],
                    source={"to_publication_id": to_cycle}
                ))
            else:
                from_feat = from_map[key]
                # Compare semantic content_hash
                if from_feat.content_hash == to_feat.content_hash:
                    unchanged_count += 1
                else:
                    modified_count += 1
                    field_diffs: List[FieldChange] = []

                    # Find all fields that differ
                    all_field_names = sorted(set(from_feat.fields.keys()) | set(to_feat.fields.keys()))
                    for f_name in all_field_names:
                        prev_fld = from_feat.fields.get(f_name)
                        new_fld = to_feat.fields.get(f_name)

                        prev_val = normalize_value(prev_fld.value) if prev_fld else None
                        new_val = normalize_value(new_fld.value) if new_fld else None

                        if prev_val != new_val:
                            fld_sec = new_fld.section if new_fld else (prev_fld.section if prev_fld else None)
                            field_diffs.append(FieldChange(
                                field=f_name,
                                previous=prev_val,
                                new=new_val,
                                section=fld_sec
                            ))

                    # Check geometry changes
                    prev_geom = from_feat.geometry.coordinates if from_feat.geometry else None
                    new_geom = to_feat.geometry.coordinates if to_feat.geometry else None
                    if normalize_value(prev_geom) != normalize_value(new_geom):
                        field_diffs.append(FieldChange(
                            field="geometry.coordinates",
                            previous=normalize_value(prev_geom),
                            new=normalize_value(new_geom)
                        ))

                    changes.append(Change(
                        from_cycle=from_cycle,
                        to_cycle=to_cycle,
                        feature_key=key,
                        feature_type=to_feat.feature_type,
                        status=ChangeStatus.MODIFIED,
                        field_changes=field_diffs,
                        source={"from_publication_id": from_cycle, "to_publication_id": to_cycle}
                    ))

        # 2. Detect DELETED features
        deleted_runways = 0
        total_from_runways = sum(1 for f in from_features if f.feature_type in (FeatureType.RUNWAY, FeatureType.RUNWAY_DIRECTION))

        for key, from_feat in from_map.items():
            if key not in to_map:
                deleted_count += 1
                if from_feat.feature_type in (FeatureType.RUNWAY, FeatureType.RUNWAY_DIRECTION):
                    deleted_runways += 1

                changes.append(Change(
                    from_cycle=from_cycle,
                    to_cycle=to_cycle,
                    feature_key=key,
                    feature_type=from_feat.feature_type,
                    status=ChangeStatus.DELETED,
                    field_changes=[],
                    source={"from_publication_id": from_cycle}
                ))

        # Calculate disappearance percentages
        total_from = len(from_features)
        disappeared_features_pct = round((deleted_count / total_from) * 100.0, 2) if total_from > 0 else 0.0
        disappeared_runways_pct = round((deleted_runways / total_from_runways) * 100.0, 2) if total_from_runways > 0 else 0.0

        return DiffReport(
            from_cycle=from_cycle,
            to_cycle=to_cycle,
            changes=changes,
            counts={
                "new": new_count,
                "modified": modified_count,
                "deleted": deleted_count,
                "unchanged": unchanged_count,
            },
            disappeared_features_pct=disappeared_features_pct,
            disappeared_runways_pct=disappeared_runways_pct
        )
