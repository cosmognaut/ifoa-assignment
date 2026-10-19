import path from 'node:path';
import type { Server } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import express from 'express';
import type {
  Express,
  NextFunction,
  Request,
  RequestHandler,
  Response
} from 'express';
import type { Db, Filter, MongoClient } from 'mongodb';
import { connect, isRecord, loadConfig } from './db.ts';
import type {
  ApiConfig,
  ChangeRow,
  FeatureCollectionResponse,
  FeatureDoc,
  GeoJsonFeature,
  PublicationDoc
} from './db.ts';

const FEATURE_TYPE = 'featureType' as const;
const TYPE_AERODROME = 'aerodrome' as const;
const TYPE_RUNWAY = 'runway' as const;
const TYPE_RUNWAY_DIRECTION = 'runwayDirection' as const;
const MAX_FEATURES = 5000;
const API_ROOT = path.dirname(fileURLToPath(import.meta.url));

type AsyncRoute = (req: Request, res: Response, next: NextFunction) => Promise<unknown>;

interface CreateAppOptions {
  db: Db;
  config: ApiConfig;
}

interface AirportResponse {
  aerodrome: GeoJsonFeature | null;
  runwayDirections: GeoJsonFeature[];
  runways: GeoJsonFeature[];
}

function asyncHandler(handler: AsyncRoute): RequestHandler {
  return (req, res, next) => {
    Promise.resolve(handler(req, res, next)).catch(next);
  };
}

function toFeature(doc: FeatureDoc): GeoJsonFeature {
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

function parseBbox(value: string): { minLon: number; minLat: number; maxLon: number; maxLat: number } | null {
  const parts = value.split(',');
  if (parts.length !== 4) return null;
  const tokens = parts.map((part) => part.trim());
  if (tokens.some((token) => token === '' || !Number.isFinite(Number(token)))) return null;
  const [minLon, minLat, maxLon, maxLat] = tokens.map(Number);
  if (minLon > maxLon || minLat > maxLat) return null;
  return { minLon, minLat, maxLon, maxLat };
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function createApp({ db, config }: CreateAppOptions): Express {
  if (!db) throw new Error('createApp requires a Mongo database handle');
  if (!config) throw new Error('createApp requires config');

  const features = db.collection<FeatureDoc>(config.collections.features);
  const publications = db.collection<PublicationDoc>(config.collections.publications);
  const changes = db.collection<ChangeRow>(config.collections.changes);

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
    const filter: Filter<PublicationDoc> = {};
    const docs = await publications
      .find(filter, { projection: { _id: 0 } })
      .sort({ effectiveDate: -1 })
      .toArray();
    res.json(docs);
  }));

  app.get('/api/feature-types', asyncHandler(async (req, res) => {
    const rows = await features.aggregate<{ _id: string; count: number }>([
      { $group: { _id: `$${FEATURE_TYPE}`, count: { $sum: 1 } } },
      { $sort: { _id: 1 } }
    ]).toArray();
    res.json(rows.map((row) => ({ [FEATURE_TYPE]: row._id, count: row.count })));
  }));

  app.get('/api/features', asyncHandler(async (req, res) => {
    const rawPublicationId: unknown = req.query.publicationId;
    let publicationId: string;

    if (rawPublicationId === undefined) {
      const promotedFilter: Filter<PublicationDoc> = { status: 'promoted' };
      const promoted = await publications
        .find(promotedFilter, { projection: { _id: 0, cycleId: 1 } })
        .sort({ effectiveDate: -1 })
        .limit(1)
        .next();
      if (!promoted) {
        return res.status(404).json({ error: 'No promoted publication found' });
      }
      publicationId = promoted.cycleId;
    } else if (typeof rawPublicationId === 'string') {
      publicationId = rawPublicationId;
    } else {
      return res.status(400).json({ error: 'publicationId must be a single string' });
    }

    const filter: Filter<FeatureDoc> = { publicationId };
    const rawFeatureType: unknown = req.query.featureType;

    if (rawFeatureType !== undefined) {
      if (typeof rawFeatureType !== 'string') {
        return res.status(400).json({ error: 'featureType must be a single string' });
      }
      const knownTypes = await features.distinct(FEATURE_TYPE);
      if (!knownTypes.includes(rawFeatureType)) {
        return res.status(400).json({ error: 'Unknown feature type' });
      }
      filter.featureType = rawFeatureType;
    }

    const rawBbox: unknown = req.query.bbox;
    if (rawBbox !== undefined) {
      if (typeof rawBbox !== 'string') {
        return res.status(400).json({ error: 'bbox must be a single string' });
      }
      const box = parseBbox(rawBbox);
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
    } else if (req.query.withGeometry === 'true') {
      filter.geometry = { $ne: null };
    }

    const docs = await features
      .find(filter, { projection: { _id: 0 } })
      .sort({ naturalKey: 1 })
      .limit(MAX_FEATURES)
      .toArray();

    const response: FeatureCollectionResponse = {
      type: 'FeatureCollection',
      features: docs.map((doc) => toFeature(doc))
    };
    res.json(response);
  }));

  app.get(/^\/api\/features\/(.+)$/, asyncHandler(async (req, res) => {
    let naturalKey: string;
    try {
      naturalKey = decodeURIComponent(req.path.slice('/api/features/'.length));
    } catch (err) {
      return res.status(400).json({ error: 'Invalid feature key' });
    }

    const detailFilter: Filter<FeatureDoc> = { naturalKey };
    const doc = await features.findOne(detailFilter, { projection: { _id: 0 } });
    if (!doc) {
      return res.status(404).json({ error: 'Feature not found' });
    }
    res.json(toFeature(doc));
  }));

  app.get('/api/airports/:icao', asyncHandler(async (req, res) => {
    const rawIcao: unknown = req.params.icao;
    if (typeof rawIcao !== 'string') {
      return res.status(400).json({ error: 'Invalid aerodrome identifier' });
    }
    const icao = rawIcao.toUpperCase();
    config.aerodromePattern.lastIndex = 0;
    if (!config.aerodromePattern.test(icao)) {
      return res.status(400).json({ error: 'Invalid aerodrome identifier' });
    }

    const runwayPrefix = new RegExp(`^${escapeRegex(icao)}/RWY/`);
    const aerodromeFilter: Filter<FeatureDoc> = { featureType: TYPE_AERODROME, naturalKey: icao };
    const runwayFilter: Filter<FeatureDoc> = { featureType: TYPE_RUNWAY, naturalKey: runwayPrefix };
    const runwayDirectionFilter: Filter<FeatureDoc> = {
      featureType: TYPE_RUNWAY_DIRECTION,
      naturalKey: runwayPrefix
    };
    const [aerodromeDoc, runwayDocs, runwayDirectionDocs] = await Promise.all([
      features.findOne(aerodromeFilter, { projection: { _id: 0 } }),
      features
        .find(runwayFilter, { projection: { _id: 0 } })
        .sort({ naturalKey: 1 })
        .toArray(),
      features
        .find(runwayDirectionFilter, { projection: { _id: 0 } })
        .sort({ naturalKey: 1 })
        .toArray()
    ]);

    if (!aerodromeDoc && runwayDocs.length === 0) {
      return res.status(404).json({ error: 'Aerodrome not found' });
    }

    const response: AirportResponse = {
      aerodrome: aerodromeDoc ? toFeature(aerodromeDoc) : null,
      runwayDirections: runwayDirectionDocs.map((doc) => toFeature(doc)),
      runways: runwayDocs.map((doc) => toFeature(doc))
    };
    res.json(response);
  }));

  app.get('/api/changes', asyncHandler(async (req, res) => {
    const { from, to } = req.query;
    if (typeof from !== 'string' || from === '' || typeof to !== 'string' || to === '') {
      return res.status(400).json({ error: 'Query parameters "from" and "to" are required' });
    }

    const changeFilter: Filter<ChangeRow> = { fromCycle: from, toCycle: to };
    const rows = await changes
      .find(changeFilter, { projection: { _id: 0 } })
      .toArray();
    res.json(rows);
  }));

  app.use(express.static(path.join(API_ROOT, 'public')));

  app.use((req: Request, res: Response) => {
    res.status(404).json({ error: 'Not found' });
  });

  app.use((err: unknown, req: Request, res: Response, next: NextFunction): void => {
    if (res.headersSent) {
      next(err);
      return;
    }
    const status = isRecord(err) && typeof err.status === 'number' ? err.status : 500;
    const message = err instanceof Error ? err.message : 'Internal server error';
    res.status(status).json({ error: message });
  });

  return app;
}

export async function start(): Promise<{ server: Server; client: MongoClient; config: ApiConfig }> {
  const config = loadConfig();
  const { client, db } = await connect(config);
  const app = createApp({ db, config });
  const server = app.listen(config.port, () => {
    console.log(`API listening on port ${config.port}`);
  });

  let shuttingDown = false;
  const shutdown = (): void => {
    if (shuttingDown) return;
    shuttingDown = true;
    server.close(() => {
      void client.close().then(
        () => process.exit(0),
        () => process.exit(1)
      );
    });
  };

  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);

  return { server, client, config };
}

const entryPoint = process.argv[1];
if (entryPoint !== undefined && import.meta.url === pathToFileURL(entryPoint).href) {
  start().catch((err: unknown) => {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
