'use strict';

const path = require('node:path');
const express = require('express');
const { loadConfig, connect } = require('./db');

const FEATURE_TYPE = 'featureType';
const TYPE_AERODROME = 'aerodrome';
const TYPE_RUNWAY = 'runway';
const TYPE_RUNWAY_DIRECTION = 'runwayDirection';
const MAX_FEATURES = 5000;

const asyncHandler = (handler) => (req, res, next) =>
  Promise.resolve(handler(req, res, next)).catch(next);

function toFeature(doc) {
  if (doc === null || doc === undefined) return null;
  return {
    type: 'Feature',
    geometry: doc.geometry ?? null,
    properties: {
      naturalKey: doc.naturalKey,
      parentKey: doc.parentKey,
      [FEATURE_TYPE]: doc.featureType,
      publicationId: doc.publicationId,
      effectiveFrom: doc.effectiveFrom,
      effectiveTo: doc.effectiveTo,
      sourceClass: doc.sourceClass,
      temporality: doc.temporality,
      contentHash: doc.contentHash,
      fields: doc.fields
    }
  };
}

function parseBbox(value) {
  const parts = value.split(',');
  if (parts.length !== 4) return null;
  const tokens = parts.map((part) => part.trim());
  if (tokens.some((token) => token === '' || !Number.isFinite(Number(token)))) return null;
  const [minLon, minLat, maxLon, maxLat] = tokens.map(Number);
  if (minLon > maxLon || minLat > maxLat) return null;
  return { minLon, minLat, maxLon, maxLat };
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function createApp({ db, config }) {
  if (!db) throw new Error('createApp requires a Mongo database handle');
  if (!config) throw new Error('createApp requires config');

  const features = db.collection(config.collections.features);
  const publications = db.collection(config.collections.publications);
  const changes = db.collection(config.collections.changes);

  const app = express();
  app.disable('x-powered-by');

  app.get('/api/health', asyncHandler(async (req, res) => {
    const count = await features.countDocuments({});
    res.json({
      ok: true,
      features: count,
      collections: Object.values(config.collections)
    });
  }));

  app.get('/api/publications', asyncHandler(async (req, res) => {
    const docs = await publications
      .find({}, { projection: { _id: 0 } })
      .sort({ effectiveDate: -1 })
      .toArray();
    res.json(docs);
  }));

  app.get('/api/feature-types', asyncHandler(async (req, res) => {
    const rows = await features.aggregate([
      { $group: { _id: `$${FEATURE_TYPE}`, count: { $sum: 1 } } },
      { $sort: { _id: 1 } }
    ]).toArray();
    res.json(rows.map((row) => ({ [FEATURE_TYPE]: row._id, count: row.count })));
  }));

  app.get('/api/features', asyncHandler(async (req, res) => {
    const query = req.query;
    let publicationId = query.publicationId;

    if (publicationId === undefined) {
      const promoted = await publications
        .find({ status: 'promoted' }, { projection: { _id: 0, cycleId: 1 } })
        .sort({ effectiveDate: -1 })
        .limit(1)
        .next();
      if (!promoted) {
        return res.status(404).json({ error: 'No promoted publication found' });
      }
      publicationId = promoted.cycleId;
    }

    if (typeof publicationId !== 'string') {
      return res.status(400).json({ error: 'publicationId must be a single string' });
    }

    const filter = { publicationId };

    if (query.featureType !== undefined) {
      if (typeof query.featureType !== 'string') {
        return res.status(400).json({ error: 'featureType must be a single string' });
      }
      const knownTypes = await features.distinct(FEATURE_TYPE);
      if (!knownTypes.includes(query.featureType)) {
        return res.status(400).json({ error: 'Unknown feature type' });
      }
      filter[FEATURE_TYPE] = query.featureType;
    }

    if (query.bbox !== undefined) {
      if (typeof query.bbox !== 'string') {
        return res.status(400).json({ error: 'bbox must be a single string' });
      }
      const box = parseBbox(query.bbox);
      if (!box) {
        return res.status(400).json({ error: 'bbox must be four finite numbers: minLon,minLat,maxLon,maxLat' });
      }
      filter.$and = [
        { geometry: { $ne: null } },
        {
          geometry: {
            $geoWithin: {
              $box: [[box.minLon, box.minLat], [box.maxLon, box.maxLat]]
            }
          }
        }
      ];
    } else if (query.withGeometry === 'true') {
      filter.geometry = { $ne: null };
    }

    const docs = await features
      .find(filter, { projection: { _id: 0 } })
      .sort({ naturalKey: 1 })
      .limit(MAX_FEATURES)
      .toArray();

    res.json({
      type: 'FeatureCollection',
      features: docs.map(toFeature)
    });
  }));

  app.get(/^\/api\/features\/(.+)$/, asyncHandler(async (req, res) => {
    let naturalKey;
    try {
      naturalKey = decodeURIComponent(req.path.slice('/api/features/'.length));
    } catch (err) {
      return res.status(400).json({ error: 'Invalid feature key' });
    }

    const doc = await features.findOne({ naturalKey }, { projection: { _id: 0 } });
    if (!doc) {
      return res.status(404).json({ error: 'Feature not found' });
    }
    res.json(toFeature(doc));
  }));

  app.get('/api/airports/:icao', asyncHandler(async (req, res) => {
    const icao = String(req.params.icao).toUpperCase();
    config.aerodromePattern.lastIndex = 0;
    if (!config.aerodromePattern.test(icao)) {
      return res.status(400).json({ error: 'Invalid aerodrome identifier' });
    }

    const runwayPrefix = new RegExp(`^${escapeRegex(icao)}/RWY/`);
    const [aerodromeDoc, runwayDocs, runwayDirectionDocs] = await Promise.all([
      features.findOne({ [FEATURE_TYPE]: TYPE_AERODROME, naturalKey: icao }, { projection: { _id: 0 } }),
      features
        .find({ [FEATURE_TYPE]: TYPE_RUNWAY, naturalKey: runwayPrefix }, { projection: { _id: 0 } })
        .sort({ naturalKey: 1 })
        .toArray(),
      features
        .find({ [FEATURE_TYPE]: TYPE_RUNWAY_DIRECTION, naturalKey: runwayPrefix }, { projection: { _id: 0 } })
        .sort({ naturalKey: 1 })
        .toArray()
    ]);

    if (!aerodromeDoc && runwayDocs.length === 0) {
      return res.status(404).json({ error: 'Aerodrome not found' });
    }

    res.json({
      aerodrome: toFeature(aerodromeDoc),
      runwayDirections: runwayDirectionDocs.map(toFeature),
      runways: runwayDocs.map(toFeature)
    });
  }));

  app.get('/api/changes', asyncHandler(async (req, res) => {
    const { from, to } = req.query;
    if (typeof from !== 'string' || from === '' || typeof to !== 'string' || to === '') {
      return res.status(400).json({ error: 'Query parameters "from" and "to" are required' });
    }

    const rows = await changes
      .find({ fromCycle: from, toCycle: to }, { projection: { _id: 0 } })
      .toArray();
    res.json(rows);
  }));

  app.use(express.static(path.join(__dirname, 'public')));

  app.use((req, res) => {
    res.status(404).json({ error: 'Not found' });
  });

  app.use((err, req, res, next) => {
    if (res.headersSent) return next(err);
    res.status(err.status || 500).json({ error: err.message || 'Internal server error' });
  });

  return app;
}

async function start() {
  const config = loadConfig();
  const { client, db } = await connect(config);
  const app = createApp({ db, config });
  const server = app.listen(config.port, () => {
    console.log(`API listening on port ${config.port}`);
  });

  let shuttingDown = false;
  const shutdown = () => {
    if (shuttingDown) return;
    shuttingDown = true;
    server.close(async () => {
      try {
        await client.close();
        process.exit(0);
      } catch (err) {
        process.exit(1);
      }
    });
  };

  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);

  return { server, client, config };
}

if (require.main === module) {
  start().catch((err) => {
    console.error(err.message);
    process.exit(1);
  });
}

module.exports = { createApp, start };
