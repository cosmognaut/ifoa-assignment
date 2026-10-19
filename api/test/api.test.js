'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { MongoClient } = require('mongodb');
const { loadConfig } = require('../db');
const { createApp } = require('../server');

const config = loadConfig();
const totals = {};

let client;
let db;
let server;
let baseUrl;
const responseBodies = [];

async function request(pathname) {
  const response = await fetch(`${baseUrl}${pathname}`);
  const text = await response.text();
  responseBodies.push(text);
  let body;
  try {
    body = JSON.parse(text);
  } catch (err) {
    body = undefined;
  }
  return { status: response.status, text, body };
}

function walkKeys(value, visit) {
  if (Array.isArray(value)) {
    for (const item of value) walkKeys(item, visit);
  } else if (value !== null && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      visit(key);
      walkKeys(child, visit);
    }
  }
}

function assertNoIdKey(body) {
  if (body === undefined) return;
  walkKeys(body, (key) => assert.notEqual(key, '_id', 'response leaked an _id key'));
}

before(async () => {
  client = new MongoClient(config.uri);
  await client.connect();
  db = client.db(config.dbName);

  const features = db.collection(config.collections.features);
  totals.total = await features.countDocuments({});
  totals.nullGeometry = await features.countDocuments({ geometry: null });
  totals.airspace = await features.countDocuments({ featureType: 'airspace' });
  totals.specialUseArea = await features.countDocuments({ featureType: 'specialUseArea' });
  totals.featureTypes = await features.distinct('featureType');
  totals.changed = await db.collection(config.collections.changes).findOne({});
  totals.nullKey = await features.findOne({ geometry: null }, { projection: { _id: 0, naturalKey: 1 } });

  const app = createApp({ db, config });
  server = await new Promise((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  if (server) await new Promise((resolve) => server.close(resolve));
  if (client) await client.close();
});

test('1. health reports the real feature count', async () => {
  const { status, body } = await request('/api/health');
  assert.equal(status, 200);
  assert.equal(body.ok, true);
  assert.equal(body.features, totals.total);
  assert.ok(Array.isArray(body.collections));
});

test('2. publications are newest first with a promoted cycle', async () => {
  const { status, body } = await request('/api/publications');
  assert.equal(status, 200);
  assert.ok(Array.isArray(body));
  assert.ok(body.length > 0);
  assert.equal(body[0].status, 'promoted');
  assert.equal(typeof body[0].cycleId, 'string');
  assert.ok(body[0].cycleId.length > 0);
});

test('3. feature type counts sum to the real total', async () => {
  const { status, body } = await request('/api/feature-types');
  assert.equal(status, 200);
  assert.ok(Array.isArray(body));
  assert.ok(body.length > 0);
  const sum = body.reduce((acc, row) => acc + row.count, 0);
  assert.equal(sum, totals.total);
  for (const row of body) assert.equal(typeof row.featureType, 'string');
});

test('4. default features include the whole collection, null geometry included', async () => {
  const { status, body } = await request('/api/features');
  assert.equal(status, 200);
  assert.equal(body.type, 'FeatureCollection');
  assert.ok(Array.isArray(body.features));
  assert.equal(body.features.length, totals.total);
  assert.ok(body.features.some((feature) => feature.geometry === null));
});

test('5. withGeometry=true returns exactly the mappable features', async () => {
  const { status, body } = await request('/api/features?withGeometry=true');
  assert.equal(status, 200);
  assert.equal(body.features.length, totals.total - totals.nullGeometry);
  for (const feature of body.features) {
    assert.equal(feature.type, 'Feature');
    assert.notEqual(feature.geometry, null);
  }
});

test('6. airspace filter returns the live airspace count, all without geometry', async () => {
  const { status, body } = await request('/api/features?featureType=airspace');
  assert.equal(status, 200);
  assert.ok(totals.airspace > 0);
  assert.equal(body.features.length, totals.airspace);
  for (const feature of body.features) {
    assert.equal(feature.properties.featureType, 'airspace');
    assert.equal(feature.geometry, null);
  }
});

test('7. specialUseArea filter returns the live count, all without geometry', async () => {
  const { status, body } = await request('/api/features?featureType=specialUseArea');
  assert.equal(status, 200);
  assert.ok(totals.specialUseArea > 0);
  assert.equal(body.features.length, totals.specialUseArea);
  for (const feature of body.features) {
    assert.equal(feature.properties.featureType, 'specialUseArea');
    assert.equal(feature.geometry, null);
  }
});

test('8. withGeometry=true combined with a null-only type returns zero', async () => {
  const { status, body } = await request('/api/features?featureType=airspace&withGeometry=true');
  assert.equal(status, 200);
  assert.equal(body.features.length, 0);
});

test('9. unknown feature type returns 400', async () => {
  const { status, body } = await request('/api/features?featureType=notarealthing');
  assert.equal(status, 400);
  assert.equal(typeof body.error, 'string');
});

test('10. malformed bbox returns 400', async () => {
  const { status, body } = await request('/api/features?bbox=not,numbers');
  assert.equal(status, 400);
  assert.equal(typeof body.error, 'string');
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
  assert.ok(body.features.length > 0);
  assert.ok(body.features.length < totals.total - totals.nullGeometry);
  for (const feature of body.features) {
    assert.notEqual(feature.geometry, null);
    if (feature.geometry.type === 'Point') {
      const [lon, lat] = feature.geometry.coordinates;
      assert.ok(lon >= minLon && lon <= maxLon, `longitude ${lon} outside bbox`);
      assert.ok(lat >= minLat && lat <= maxLat, `latitude ${lat} outside bbox`);
    }
  }
});

test('12. slash-containing natural key returns byte-identical fields', async () => {
  const { status, body } = await request('/api/features/EBBR/RWY/25L');
  assert.equal(status, 200);
  assert.equal(body.properties.naturalKey, 'EBBR/RWY/25L');
  const lda = body.properties.fields.lda;
  assert.equal(lda.value, 3210);
  assert.equal(lda.unit, 'M');
  assert.equal(lda.section, 'EBBR AD 2.13');
  assert.equal(typeof lda.effective_date, 'string');
  assert.ok(lda.effective_date.length > 0);
});

test('13. missing natural key returns a JSON 404', async () => {
  const { status, body, text } = await request('/api/features/DOES/NOT/EXIST');
  assert.equal(status, 404);
  assert.equal(typeof body.error, 'string');
  assert.ok(!text.includes('<!doctype'));
});

test('14. route fields pass through real data', async () => {
  const { status, body } = await request('/api/features/RTE/L179');
  assert.equal(status, 200);
  assert.ok(body.properties.fields.controllingUnit.value.includes('Brussels ACC'));
});

test('15. airport endpoint returns aerodrome, runway directions and runways', async () => {
  const { status, body } = await request('/api/airports/EBBR');
  assert.equal(status, 200);
  assert.notEqual(body.aerodrome, null);
  assert.equal(body.aerodrome.properties.naturalKey, 'EBBR');
  assert.equal(body.runwayDirections.length, 6);
  assert.equal(body.runways.length, 3);
  for (const feature of body.runwayDirections) {
    assert.equal(feature.properties.featureType, 'runwayDirection');
  }
  for (const feature of body.runways) {
    assert.equal(feature.properties.featureType, 'runway');
  }
});

test('16. unknown airport returns 404', async () => {
  const { status, body } = await request('/api/airports/ZZZZ');
  assert.equal(status, 404);
  assert.equal(typeof body.error, 'string');
});

test('17. lowercase airport identifier is uppercased', async () => {
  const { status, body } = await request('/api/airports/ebbr');
  assert.equal(status, 200);
  assert.equal(body.aerodrome.properties.naturalKey, 'EBBR');
});

test('18. airport identifier failing the configured regex returns 400', async () => {
  const { status, body } = await request('/api/airports/EB');
  assert.equal(status, 400);
  assert.equal(typeof body.error, 'string');
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
  assert.equal(typeof missing.body.error, 'string');

  const { fromCycle, toCycle } = totals.changed;
  const { status, body } = await request(
    `/api/changes?from=${encodeURIComponent(fromCycle)}&to=${encodeURIComponent(toCycle)}`
  );
  assert.equal(status, 200);
  assert.ok(Array.isArray(body));
  assert.ok(body.length > 0);
  for (const row of body) {
    assert.equal(row.fromCycle, fromCycle);
    assert.equal(row.toCycle, toCycle);
  }
  assertNoIdKey(body);
});

test('21. encoded limits pass through unchanged', async () => {
  const { status, body } = await request('/api/features/SUA/EBR01');
  assert.equal(status, 200);
  assert.deepEqual(body.properties.fields.upperLimit.value, {
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
  assert.equal(typeof JSON.parse(text).error, 'string');
});

test('23. no response contains an _id object key', () => {
  for (const text of responseBodies) {
    let body;
    try {
      body = JSON.parse(text);
    } catch (err) {
      continue;
    }
    assertNoIdKey(body);
  }
});
