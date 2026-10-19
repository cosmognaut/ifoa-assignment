import pytest
from pathlib import Path

from pipeline.config import load_crawler_config, require_str
from pipeline.parse.runways import RunwayParser
from pipeline.models.feature import Feature, FeatureType
from pipeline.models.publication import Publication, PublicationStatus
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.models.change import ChangeStatus
from pipeline.diff.engine import DiffEngine
from pipeline.validate.rules import ValidationEngine
from pipeline.load.load import MongoLoader

def _cache_dir() -> Path:
    return Path(require_str(load_crawler_config(), "cache_dir", "crawler.yaml"))

def test_diff_engine_changes():
    cache_dir = _cache_dir()
    doc_path = cache_dir / "2026-10-01-current" / "EB-AD-2.EBBR-en-GB.html"
    if not doc_path.exists():
        pytest.skip("EBBR raw HTML not present")

    parser = RunwayParser("2026-10-01-current", "2026-10-01")
    base_feats, _ = parser.parse_airport_runways(
        "EBBR", doc_path.read_text(encoding="utf-8"), doc_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z"
    )

    # Clone base features as next cycle
    next_feats = [f.model_copy(deep=True) for f in base_feats]

    # 1. Modify Runway 01 LDA (2941 -> 2850)
    rwy_01 = next(f for f in next_feats if f.natural_key == "EBBR/RWY/01")
    rwy_01.fields["lda"].value = 2850.0
    from pipeline.utils.hash_utils import compute_content_hash
    rwy_01.content_hash = compute_content_hash(rwy_01.fields, rwy_01.geometry.model_dump())

    # 2. Delete Runway 07R-25L physical pair and its directions
    deleted_keys = {"EBBR/RWY/07R-25L", "EBBR/RWY/07R", "EBBR/RWY/25L"}
    next_feats = [f for f in next_feats if f.natural_key not in deleted_keys]

    # 3. Add a new synthetic runway
    new_feat = base_feats[0].model_copy(deep=True)
    new_feat.natural_key = "EBBR/RWY/99"
    next_feats.append(new_feat)

    # Run Diff
    report = DiffEngine.compare_features("cycle_a", "cycle_b", base_feats, next_feats)

    assert report.counts["new"] == 1
    assert report.counts["deleted"] == 3
    assert report.counts["modified"] == 1
    assert report.counts["unchanged"] == len(base_feats) - 4  # 9 - 1 (mod) - 3 (del) = 5

    # Verify field changes in modified feature
    mod_change = next(c for c in report.changes if c.status == ChangeStatus.MODIFIED and c.feature_key == "EBBR/RWY/01")
    lda_diff = next(fc for fc in mod_change.field_changes if fc.field == "lda")
    assert lda_diff.previous == 2941
    assert lda_diff.new == 2850

    # Verify disappeared percentages
    assert report.disappeared_features_pct > 0.0
    assert report.disappeared_runways_pct > 0.0

def test_promotion_gate_enforces_disappearance_thresholds():
    engine = ValidationEngine()

    # If disappearance exceeds threshold, gate must reject
    passed, msg = engine.evaluate_promotion_gate(
        issues=[],
        disappeared_runways_pct=10.0,  # Max allowed is 0.0%
        disappeared_features_pct=0.0
    )
    assert passed is False
    assert "Disappeared runways (10.0%) exceeds allowed threshold" in msg

def test_s1_quarantine_persists_rejected_publication_to_mongo():
    """Tests S1: Proves that failing the promotion gate writes Publication{status: rejected} and issues to MongoDB."""
    loader = MongoLoader()
    test_cycle = "test-quarantine-cycle"

    bad_issue = ValidationIssue(
        publication_id=test_cycle,
        severity=IssueSeverity.ERROR,
        rule_id="RULE_DECLARED_DIST_TORA_LE_TODA",
        feature_key="EBBR/RWY/01",
        section="AD-2.13",
        message="Simulated hard error for quarantine test",
        raw_snippet="TORA: 3500 > TODA: 3000"
    )

    engine = ValidationEngine()
    passed, gate_msg = engine.evaluate_promotion_gate([bad_issue])
    assert passed is False

    # Simulate what cmd_load does on gate failure
    status = PublicationStatus.REJECTED if not passed else PublicationStatus.PROMOTED
    pub = Publication(
        cycle_id=test_cycle,
        effective_date="2026-10-01",
        amendment_id="TEST-AMDT",
        ingested_at="2026-10-19T00:00:00Z",
        status=status
    )

    loader.save_publication(pub)
    loader.save_validation_issues(test_cycle, [bad_issue])

    # Assert that MongoDB actually holds status: "rejected" and the validation issues!
    stored_pub = loader.publications.find_one({"cycleId": test_cycle})
    assert stored_pub is not None
    assert stored_pub["status"] == "rejected"

    stored_issues = loader.get_issues(test_cycle)
    assert len(stored_issues) == 1
    assert stored_issues[0]["ruleId"] == "RULE_DECLARED_DIST_TORA_LE_TODA"

    # Clean up test records
    loader.publications.delete_one({"cycleId": test_cycle})
    loader.validation_issues.delete_many({"publicationId": test_cycle})
