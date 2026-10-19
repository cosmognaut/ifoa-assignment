import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import type { Server } from 'node:http';
import { MongoClient } from 'mongodb';
import type { Db } from 'mongodb';
import { isRecord, loadConfig } from '../db.ts';
import type {
  ChangeRow,
  FeatureCollectionResponse,
  FeatureDoc,
  FeatureProperties,
  GeoJsonFeature,
  ValidationIssueDoc
} from '../db.ts';
import { createApp } from '../server.ts';

const config = loadConfig();
const responseBodies: string[] = [];

let client: MongoClient | undefined;
let db: Db | undefined;
let server: Server | undefined;
let baseUrl = '';

let totalFeatures = 0;
let nullGeometryCount = 0;
let airspaceCount = 0;
let specialUseAreaCount = 0;
let changedRow: ChangeRow | null = null;
let nullKeyDoc: Pick<FeatureDoc, 'naturalKey'> | null = null;
let issueCount = 0;
let issueWithKey: Pick<ValidationIssueDoc, 'featureKey' | 'ruleId'> | null = null;

interface JsonResponse {
  status: number;
  text: string;
  body: unknown;
}

async function request(pathname: string): Promise<JsonResponse> {
  const response = await fetch(`${baseUrl}${pathname}`);
  const text = await response.text();
  responseBodies.push(text);
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch (err) {
    body = undefined;
  }
  return { status: response.status, text, body };
}

function expectRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) assert.fail('expected a JSON object');
  return value;
}

function expectArray(value: unknown): unknown[] {
  if (!Array.isArray(value)) assert.fail('expected an array');
  return value;
}

function expectString(value: unknown): string {
  if (typeof value !== 'string') assert.fail('expected a string');
  return value;
}

function expectNumber(value: unknown): number {
  if (typeof value !== 'number') assert.fail('expected a number');
  return value;
}

function asGeometry(value: unknown): FeatureDoc['geometry'] {
  if (value === null) return null;
  const record = expectRecord(value);
  const type = expectString(record.type);
  if (!Object.prototype.hasOwnProperty.call(record, 'coordinates')) {
    assert.fail('geometry is missing coordinates');
  }
  return { type, coordinates: record.coordinates };
}

function asFeatureProperties(value: unknown): FeatureProperties {
  const record = expectRecord(value);
  const effectiveTo = record.effectiveTo === null ? null : expectString(record.effectiveTo);
  const parentKey = record.parentKey === null ? null : expectString(record.parentKey);
  return {
    naturalKey: expectString(record.naturalKey),
    parentKey,
    featureType: expectString(record.featureType),
    publicationId: expectString(record.publicationId),
    effectiveFrom: expectString(record.effectiveFrom),
    effectiveTo,
    sourceClass: expectString(record.sourceClass),
    temporality: expectString(record.temporality),
    contentHash: expectString(record.contentHash),
    fields: expectRecord(record.fields)
  };
}

function asFeature(value: unknown): GeoJsonFeature {
  const record = expectRecord(value);
  assert.equal(record.type, 'Feature');
  return {
    type: 'Feature',
    geometry: asGeometry(record.geometry),
    properties: asFeatureProperties(record.properties)
  };
}

function asFeatureCollection(value: unknown): FeatureCollectionResponse {
  const record = expectRecord(value);
  assert.equal(record.type, 'FeatureCollection');
  return {
    type: 'FeatureCollection',
    features: expectArray(record.features).map((feature) => asFeature(feature))
  };
}

function asChangeRow(value: unknown): ChangeRow {
  const record = expectRecord(value);
  return {
    ...record,
    fromCycle: expectString(record.fromCycle),
    toCycle: expectString(record.toCycle)
  };
}

function pointCoordinates(geometry: FeatureDoc['geometry']): [number, number] | null {
  if (geometry === null || geometry.type !== 'Point') return null;
  const coordinates = expectArray(geometry.coordinates);
  return [expectNumber(coordinates[0]), expectNumber(coordinates[1])];
}

function walkKeys(value: unknown, visit: (key: string) => void): void {
  if (Array.isArray(value)) {
    for (const item of value) walkKeys(item, visit);
  } else if (isRecord(value)) {
    for (const [key, child] of Object.entries(value)) {
      visit(key);
      walkKeys(child, visit);
    }
  }
}

function assertNoIdKey(body: unknown): void {
  if (body === undefined) return;
  walkKeys(body, (key) => assert.notEqual(key, '_id', 'response leaked an _id key'));
}

before(async () => {
  client = new MongoClient(config.uri);
  await client.connect();
  db = client.db(config.dbName);

  const features = db.collection<FeatureDoc>(config.collections.features);
  totalFeatures = await features.countDocuments({});
  nullGeometryCount = await features.countDocuments({ geometry: null });
  airspaceCount = await features.countDocuments({ featureType: 'airspace' });
  specialUseAreaCount = await features.countDocuments({ featureType: 'specialUseArea' });
  changedRow = await db.collection<ChangeRow>(config.collections.changes).findOne({});
  nullKeyDoc = await features.findOne({ geometry: null }, { projection: { _id: 0, naturalKey: 1 } });

  const issues = db.collection<ValidationIssueDoc>(config.collections.validationIssues);
  issueCount = await issues.countDocuments({});
  issueWithKey = await issues.findOne(
    { featureKey: { $ne: null } },
    { projection: { _id: 0, featureKey: 1, ruleId: 1 } }
  );

  const app = createApp({ db, config });
  const listening = await new Promise<Server>((resolve) => {
    const pending = app.listen(0, '127.0.0.1', () => resolve(pending));
  });
  server = listening;

  const address = listening.address();
  if (address === null || typeof address === 'string') {
    throw new Error('expected the test server to listen on a TCP port');
  }
  baseUrl = `http://127.0.0.1:${address.port}`;
});

after(async () => {
  const currentServer = server;
  if (currentServer) {
    await new Promise<void>((resolve, reject) => {
      currentServer.close((err) => {
        if (err) reject(err);
        else resolve();
      });
    });
  }
  const currentClient = client;
  if (currentClient) await currentClient.close();
});

test('1. health reports the real feature count', async () => {
  const { status, body } = await request('/api/health');
  assert.equal(status, 200);
  const record = expectRecord(body);
  assert.equal(record.ok, true);
  assert.equal(record.features, totalFeatures);
  expectArray(record.collections);
});

test('2. publications are newest first with a promoted cycle', async () => {
  const { status, body } = await request('/api/publications');
  assert.equal(status, 200);
  const publications = expectArray(body);
  assert.ok(publications.length > 0);
  const first = expectRecord(publications[0]);
  assert.equal(first.status, 'promoted');
  expectString(first.cycleId);
  assert.ok(expectString(first.cycleId).length > 0);
});

test('3. feature type counts sum to the real total', async () => {
  const { status, body } = await request('/api/feature-types');
  assert.equal(status, 200);
  const rows = expectArray(body);
  assert.ok(rows.length > 0);
  const sum = rows
    .map((row) => expectNumber(expectRecord(row).count))
    .reduce((acc, count) => acc + count, 0);
  assert.equal(sum, totalFeatures);
  for (const row of rows) expectString(expectRecord(row).featureType);
});

test('4. default features include the whole collection, null geometry included', async () => {
  const { status, body } = await request('/api/features');
  assert.equal(status, 200);
  const collection = asFeatureCollection(body);
  assert.equal(collection.features.length, totalFeatures);
  assert.ok(collection.features.some((feature) => feature.geometry === null));
});

test('5. withGeometry=true returns exactly the mappable features', async () => {
  const { status, body } = await request('/api/features?withGeometry=true');
  assert.equal(status, 200);
  const collection = asFeatureCollection(body);
  assert.equal(collection.features.length, totalFeatures - nullGeometryCount);
  for (const feature of collection.features) {
    assert.equal(feature.type, 'Feature');
    assert.notEqual(feature.geometry, null);
  }
});

test('6. airspace filter returns the live airspace count, all without geometry', async () => {
  const { status, body } = await request('/api/features?featureType=airspace');
  assert.equal(status, 200);
  assert.ok(airspaceCount > 0);
  const collection = asFeatureCollection(body);
  assert.equal(collection.features.length, airspaceCount);
  for (const feature of collection.features) {
    assert.equal(feature.properties.featureType, 'airspace');
    assert.equal(feature.geometry, null);
  }
});

test('7. specialUseArea filter returns the live count, all without geometry', async () => {
  const { status, body } = await request('/api/features?featureType=specialUseArea');
  assert.equal(status, 200);
  assert.ok(specialUseAreaCount > 0);
  const collection = asFeatureCollection(body);
  assert.equal(collection.features.length, specialUseAreaCount);
  for (const feature of collection.features) {
    assert.equal(feature.properties.featureType, 'specialUseArea');
    assert.equal(feature.geometry, null);
  }
});

test('8. withGeometry=true combined with a null-only type returns zero', async () => {
  const { status, body } = await request('/api/features?featureType=airspace&withGeometry=true');
  assert.equal(status, 200);
  const collection = asFeatureCollection(body);
  assert.equal(collection.features.length, 0);
});

test('9. unknown feature type returns 400', async () => {
  const { status, body } = await request('/api/features?featureType=notarealthing');
  assert.equal(status, 400);
  expectString(expectRecord(body).error);
});

test('10. malformed bbox returns 400', async () => {
  const { status, body } = await request('/api/features?bbox=not,numbers');
  assert.equal(status, 400);
  expectString(expectRecord(body).error);
});

test('11. bbox returns only geometries inside the box', async () => {
  const minLon = 4.3;
  const minLat = 50.8;
  const maxLon = 4.7;
  const maxLat = 51.0;
  const { status, body } = await request(
    `/api/features?bbox=${minLon},${minLat},${maxLon},${maxLat}`
  );
  assert.equal(status, 200);
  const collection = asFeatureCollection(body);
  assert.ok(collection.features.length > 0);
  assert.ok(collection.features.length < totalFeatures - nullGeometryCount);
  for (const feature of collection.features) {
    assert.notEqual(feature.geometry, null);
    const coordinates = pointCoordinates(feature.geometry);
    if (coordinates === null) continue;
    const [lon, lat] = coordinates;
    assert.ok(lon >= minLon && lon <= maxLon, `longitude ${lon} outside bbox`);
    assert.ok(lat >= minLat && lat <= maxLat, `latitude ${lat} outside bbox`);
  }
});

test('12. slash-containing natural key returns byte-identical fields', async () => {
  const { status, body } = await request('/api/features/EBBR/RWY/25L');
  assert.equal(status, 200);
  const feature = asFeature(body);
  assert.equal(feature.properties.naturalKey, 'EBBR/RWY/25L');
  const lda = expectRecord(feature.properties.fields.lda);
  assert.equal(lda.value, 3210);
  assert.equal(lda.unit, 'M');
  assert.equal(lda.section, 'EBBR AD 2.13');
  assert.ok(expectString(lda.effective_date).length > 0);
});

test('13. missing natural key returns a JSON 404', async () => {
  const { status, body, text } = await request('/api/features/DOES/NOT/EXIST');
  assert.equal(status, 404);
  expectString(expectRecord(body).error);
  assert.ok(!text.includes('<!doctype'));
});

test('14. route fields pass through real data', async () => {
  const { status, body } = await request('/api/features/RTE/L179');
  assert.equal(status, 200);
  const feature = asFeature(body);
  const controllingUnit = expectRecord(feature.properties.fields.controllingUnit);
  assert.ok(expectString(controllingUnit.value).includes('Brussels ACC'));
});

test('15. airport endpoint returns aerodrome, runway directions and runways', async () => {
  const { status, body } = await request('/api/airports/EBBR');
  assert.equal(status, 200);
  const record = expectRecord(body);
  assert.notEqual(record.aerodrome, null);
  const aerodrome = asFeature(record.aerodrome);
  assert.equal(aerodrome.properties.naturalKey, 'EBBR');
  const runwayDirections = expectArray(record.runwayDirections).map((feature) => asFeature(feature));
  const runways = expectArray(record.runways).map((feature) => asFeature(feature));
  assert.equal(runwayDirections.length, 6);
  assert.equal(runways.length, 3);
  for (const feature of runwayDirections) {
    assert.equal(feature.properties.featureType, 'runwayDirection');
  }
  for (const feature of runways) {
    assert.equal(feature.properties.featureType, 'runway');
  }
});

test('16. unknown airport returns 404', async () => {
  const { status, body } = await request('/api/airports/ZZZZ');
  assert.equal(status, 404);
  expectString(expectRecord(body).error);
});

test('17. lowercase airport identifier is uppercased', async () => {
  const { status, body } = await request('/api/airports/ebbr');
  assert.equal(status, 200);
  const record = expectRecord(body);
  assert.notEqual(record.aerodrome, null);
  assert.equal(asFeature(record.aerodrome).properties.naturalKey, 'EBBR');
});

test('18. airport identifier failing the configured regex returns 400', async () => {
  const { status, body } = await request('/api/airports/EB');
  assert.equal(status, 400);
  expectString(expectRecord(body).error);
});

test('19. no earlier response body contains _id', () => {
  assert.ok(responseBodies.length >= 18);
  for (const body of responseBodies) {
    assert.ok(!body.includes('_id'), 'response body leaked _id');
  }
});

test('20. changes requires from/to and returns matching rows', async () => {
  const missing = await request('/api/changes');
  assert.equal(missing.status, 400);
  expectString(expectRecord(missing.body).error);

  assert.ok(changedRow);
  const { fromCycle, toCycle } = changedRow;
  const { status, body } = await request(
    `/api/changes?from=${encodeURIComponent(fromCycle)}&to=${encodeURIComponent(toCycle)}`
  );
  assert.equal(status, 200);
  const rows = expectArray(body).map((row) => asChangeRow(row));
  assert.ok(rows.length > 0);
  for (const row of rows) {
    assert.equal(row.fromCycle, fromCycle);
    assert.equal(row.toCycle, toCycle);
  }
  assertNoIdKey(body);
});

test('21. encoded limits pass through unchanged', async () => {
  const { status, body } = await request('/api/features/SUA/EBR01');
  assert.equal(status, 200);
  const feature = asFeature(body);
  const upperLimit = expectRecord(feature.properties.fields.upperLimit);
  assert.deepEqual(expectRecord(upperLimit.value), {
    value: 999,
    unit: 'FL',
    reference: 'UNL'
  });
});

test('22. write methods have no routes', async () => {
  const response = await fetch(`${baseUrl}/api/features`, { method: 'POST' });
  const text = await response.text();
  responseBodies.push(text);
  assert.equal(response.status, 404);
  expectString(expectRecord(JSON.parse(text)).error);
});

test('23. no response contains an _id object key', () => {
  for (const text of responseBodies) {
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch (err) {
      continue;
    }
    assertNoIdKey(body);
  }
});

test('24. issues endpoint returns the whole validation collection', async () => {
  const { status, body } = await request('/api/issues');
  assert.equal(status, 200);
  const rows = expectArray(body);
  assert.equal(rows.length, issueCount);
  for (const row of rows) {
    const record = expectRecord(row);
    expectString(record.ruleId);
    expectString(record.message);
    expectString(record.severity);
  }
});

test('25. issues can be filtered to a single feature', async () => {
  if (issueWithKey === null) return;
  const key = issueWithKey.featureKey ?? '';
  const { status, body } = await request(`/api/issues?featureKey=${encodeURIComponent(key)}`);
  assert.equal(status, 200);
  const rows = expectArray(body);
  assert.ok(rows.length > 0);
  for (const row of rows) {
    assert.equal(expectRecord(row).featureKey, key);
  }
});

test('26. issues for an unknown feature are an empty array', async () => {
  const { status, body } = await request('/api/issues?featureKey=NO/SUCH/KEY');
  assert.equal(status, 200);
  assert.deepEqual(expectArray(body), []);
});
