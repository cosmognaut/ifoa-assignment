"""
Database persistence layer for this project.
It connects to MongoDB using config/database.yaml, creates indices (2dsphere on geometry, compound unique index on (publicationId, naturalKey)) and then performs atomic upserts using by_alias=True so that MongoDB stores camelCase keys matching the contract. Marks publication as staged, promoted or rejected.
"""
import os
from typing import List, Dict, Any, Optional
import pymongo
from pymongo import MongoClient, ASCENDING, GEOSPHERE

from pipeline.models.publication import Publication, PublicationStatus
from pipeline.models.feature import Feature
from pipeline.models.validation_issue import ValidationIssue
from pipeline.config import load_database_config

class MongoLoader:
    """This class models the MongoDB loader interface"""
    def __init__(self, uri: Optional[str] = None):
        """
        Initialise the database loader using the config present at config/database.yaml
        Note that you can add an env var that overrides the preference for config YAML default.
        """
        db_cfg = load_database_config()

        # environment variable override takes precedence over config YAML default
        mongo_uri = os.getenv("MONGO_URI") or uri or db_cfg["uri"]

        self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db_name = db_cfg.get("name", "aero_pipeline")
        self.db = self.client[db_name]

        collections_cfg = db_cfg["collections"]
        self.publications = self.db[collections_cfg["publications"]]
        self.features = self.db[collections_cfg["features"]]
        self.validation_issues = self.db[collections_cfg["validation_issues"]]

        self._ensure_indexes()

    def _ensure_indexes(self):
        # publications: unique cycleId
        self.publications.create_index([("cycleId", ASCENDING)], unique=True)

        # features: 2dsphere on geometry, unique on (publicationId, naturalKey)
        self.features.create_index([("geometry", GEOSPHERE)])
        self.features.create_index([("publicationId", ASCENDING), ("naturalKey", ASCENDING)], unique=True)
        self.features.create_index([("publicationId", ASCENDING), ("featureType", ASCENDING)])
        self.features.create_index([("naturalKey", ASCENDING)])

        # validationIssues: publicationId + severity
        self.validation_issues.create_index([("publicationId", ASCENDING), ("severity", ASCENDING)])

    def save_publication(self, pub: Publication):
        doc = pub.model_dump(mode="json", by_alias=True)
        self.publications.replace_one({"cycleId": pub.cycle_id}, doc, upsert=True)

    def save_features(self, features: List[Feature]):
        if not features:
            return
        ops = []
        for feat in features:
            doc = feat.model_dump(mode="json", by_alias=True)
            ops.append(
                pymongo.ReplaceOne(
                    {"publicationId": feat.publication_id, "naturalKey": feat.natural_key},
                    doc,
                    upsert=True
                )
            )
        if ops:
            self.features.bulk_write(ops, ordered=False)

    def save_validation_issues(self, publication_id: str, issues: List[ValidationIssue]):
        self.validation_issues.delete_many({"publicationId": publication_id})
        if issues:
            docs = [i.model_dump(mode="json", by_alias=True) for i in issues]
            self.validation_issues.insert_many(docs)

    def promote_publication(self, cycle_id: str):
        self.publications.update_one(
            {"cycleId": cycle_id},
            {"$set": {"status": PublicationStatus.PROMOTED.value}}
        )

    def get_promoted_publication(self) -> Optional[Dict[str, Any]]:
        return self.publications.find_one({"status": PublicationStatus.PROMOTED.value}, sort=[("effectiveDate", -1)])

    def get_features(self, cycle_id: str, feature_type: Optional[str] = None) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"publicationId": cycle_id}
        if feature_type:
            query["featureType"] = feature_type
        return list(self.features.find(query, {"_id": 0}))

    def get_issues(self, cycle_id: str) -> List[Dict[str, Any]]:
        return list(self.validation_issues.find({"publicationId": cycle_id}, {"_id": 0}))
