"""
Reconciliation behaviour of `MongoLoader.save_features`.

Upsert-only persistence left orphan rows behind whenever a natural key changed, so
these tests pin the contract: re-saving a smaller set removes the missing keys, an
identical reload deletes nothing, and one publication can never delete another's
rows. They use a throwaway publication id and skip when MongoDB is unavailable.
"""
import pytest
from pymongo.errors import PyMongoError

from pipeline.load.load import MongoLoader
from pipeline.models.feature import Feature, FeatureType, SourceClass, Temporality

TEST_CYCLE = "test-reconcile-cycle"
OTHER_CYCLE = "test-reconcile-other"


def _loader() -> MongoLoader:
    try:
        loader = MongoLoader()
        loader.client.admin.command("ping")
    except PyMongoError as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"MongoDB not available: {exc}")
    return loader


def _feature(natural_key: str, publication_id: str, feature_type: FeatureType = FeatureType.AERODROME) -> Feature:
    return Feature(
        feature_type=feature_type,
        natural_key=natural_key,
        parent_key=None,
        publication_id=publication_id,
        effective_from="2026-10-01",
        effective_to=None,
        source_class=SourceClass.AIP,
        temporality=Temporality.PERMANENT,
        geometry=None,
        content_hash=f"hash-{natural_key}",
        fields={},
    )


def _keys(loader: MongoLoader, publication_id: str) -> list:
    return sorted(d["naturalKey"] for d in loader.features.find({"publicationId": publication_id}, {"naturalKey": 1, "_id": 0}))


@pytest.fixture()
def loader():
    instance = _loader()
    instance.features.delete_many({"publicationId": {"$in": [TEST_CYCLE, OTHER_CYCLE]}})
    yield instance
    instance.features.delete_many({"publicationId": {"$in": [TEST_CYCLE, OTHER_CYCLE]}})


def test_saving_a_smaller_set_removes_the_missing_key(loader):
    three = [_feature("EBBR", TEST_CYCLE), _feature("EBBR/RWY/01", TEST_CYCLE), _feature("EBBR/RWY/19", TEST_CYCLE)]

    assert loader.save_features(three) == 0
    assert _keys(loader, TEST_CYCLE) == ["EBBR", "EBBR/RWY/01", "EBBR/RWY/19"]

    deleted = loader.save_features(three[:2])

    assert deleted == 1
    assert _keys(loader, TEST_CYCLE) == ["EBBR", "EBBR/RWY/01"]
    assert loader.features.count_documents({"publicationId": TEST_CYCLE}) == 2


def test_reloading_the_same_set_removes_nothing(loader):
    three = [_feature("EBBR", TEST_CYCLE), _feature("EBBR/RWY/01", TEST_CYCLE), _feature("EBBR/RWY/19", TEST_CYCLE)]

    assert loader.save_features(three) == 0
    assert loader.save_features(three) == 0  # idempotent
    assert loader.features.count_documents({"publicationId": TEST_CYCLE}) == 3


def test_one_publication_never_deletes_another(loader):
    other_rows = [_feature("AFI", OTHER_CYCLE, FeatureType.NAVAID), _feature("BUB", OTHER_CYCLE, FeatureType.NAVAID)]
    loader.save_features(other_rows)
    assert _keys(loader, OTHER_CYCLE) == ["AFI", "BUB"]

    mine = [_feature("EBBR", TEST_CYCLE), _feature("EBBR/RWY/01", TEST_CYCLE)]
    loader.save_features(mine)
    deleted = loader.save_features(mine[:1])

    assert deleted == 1
    assert _keys(loader, TEST_CYCLE) == ["EBBR"]
    # The other publication is untouched by this call.
    assert _keys(loader, OTHER_CYCLE) == ["AFI", "BUB"]


def test_a_single_call_across_publications_only_prunes_each_own_set(loader):
    rows = [
        _feature("EBBR", TEST_CYCLE),
        _feature("AFI", OTHER_CYCLE, FeatureType.NAVAID),
        _feature("BUB", OTHER_CYCLE, FeatureType.NAVAID),
    ]
    loader.save_features(rows)

    # Re-send only EBBR and AFI: BUB is stale *for its own publication*.
    deleted = loader.save_features([rows[0], rows[1]])

    assert deleted == 1
    assert _keys(loader, TEST_CYCLE) == ["EBBR"]
    assert _keys(loader, OTHER_CYCLE) == ["AFI"]


def test_empty_list_is_a_no_op_without_an_explicit_publication(loader):
    loader.save_features([_feature("EBBR", TEST_CYCLE), _feature("EBBR/RWY/01", TEST_CYCLE)])

    assert loader.save_features([]) == 0
    assert loader.features.count_documents({"publicationId": TEST_CYCLE}) == 2


def test_empty_list_with_an_explicit_publication_clears_that_cycle_only(loader):
    loader.save_features([_feature("EBBR", TEST_CYCLE), _feature("EBBR/RWY/01", TEST_CYCLE)])
    loader.save_features([_feature("AFI", OTHER_CYCLE, FeatureType.NAVAID)])

    assert loader.save_features([], publication_id=TEST_CYCLE) == 2
    assert loader.features.count_documents({"publicationId": TEST_CYCLE}) == 0
    assert _keys(loader, OTHER_CYCLE) == ["AFI"]
