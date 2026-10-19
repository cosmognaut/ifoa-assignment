import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';
import { MongoClient } from 'mongodb';
import type { Db } from 'mongodb';

export interface FeatureDoc {
  featureType: string;
  naturalKey: string;
  parentKey: string | null;
  publicationId: string;
  effectiveFrom: string;
  effectiveTo: string | null;
  sourceClass: string;
  temporality: string;
  geometry: { type: string; coordinates: unknown } | null;
  contentHash: string;
  fields: Record<string, unknown>;
}

export interface FeatureProperties {
  naturalKey: string;
  parentKey: string | null;
  featureType: string;
  publicationId: string;
  effectiveFrom: string;
  effectiveTo: string | null;
  sourceClass: string;
  temporality: string;
  contentHash: string;
  fields: Record<string, unknown>;
}

export interface GeoJsonFeature {
  type: 'Feature';
  geometry: FeatureDoc['geometry'];
  properties: FeatureProperties;
}

export interface FeatureCollectionResponse {
  type: 'FeatureCollection';
  features: GeoJsonFeature[];
}

export interface PublicationDoc {
  cycleId: string;
  effectiveDate: string;
  status: string;
  [key: string]: unknown;
}

export interface ChangeRow {
  fromCycle: string;
  toCycle: string;
  [key: string]: unknown;
}

export interface ValidationIssueDoc {
  publicationId: string;
  severity: string;
  ruleId: string;
  featureKey: string | null;
  section: string;
  message: string;
  rawSnippet: string;
}

export interface ApiConfig {
  uri: string;
  dbName: string;
  collections: {
    publications: string;
    features: string;
    validationIssues: string;
    changes: string;
  };
  aerodromePattern: RegExp;
  port: number;
}

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATABASE_CONFIG = path.join(REPO_ROOT, 'config', 'database.yaml');
const VALIDATION_CONFIG = path.join(REPO_ROOT, 'config', 'validation.yaml');

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function readYaml(file: string): unknown {
  let text: string;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    const code = err instanceof Error && 'code' in err ? String(err.code) : 'unknown error';
    throw new Error(`${path.relative(REPO_ROOT, file)}: cannot read config (${code})`);
  }
  try {
    return yaml.load(text);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`${path.relative(REPO_ROOT, file)}: invalid YAML (${message})`);
  }
}

function required(source: unknown, dottedKey: string, file: string): unknown {
  let value: unknown = source;
  for (const part of dottedKey.split('.')) {
    if (!isRecord(value) || !Object.prototype.hasOwnProperty.call(value, part)) {
      throw new Error(`${file}: missing required key "${dottedKey}"`);
    }
    value = value[part];
  }
  if (value === undefined || value === null || value === '') {
    throw new Error(`${file}: missing required key "${dottedKey}"`);
  }
  return value;
}

function requiredString(source: unknown, dottedKey: string, file: string): string {
  const value = required(source, dottedKey, file);
  if (typeof value !== 'string') {
    throw new Error(`${file}: key "${dottedKey}" must be a string`);
  }
  return value;
}

export function loadConfig(): ApiConfig {
  const databaseFile = path.relative(REPO_ROOT, DATABASE_CONFIG);
  const validationFile = path.relative(REPO_ROOT, VALIDATION_CONFIG);
  const databaseConfig = readYaml(DATABASE_CONFIG);
  const validationConfig = readYaml(VALIDATION_CONFIG);

  const uri = process.env.MONGO_URI || requiredString(databaseConfig, 'database.uri', databaseFile);
  const dbName = requiredString(databaseConfig, 'database.name', databaseFile);

  const collections = {
    publications: requiredString(databaseConfig, 'database.collections.publications', databaseFile),
    features: requiredString(databaseConfig, 'database.collections.features', databaseFile),
    validationIssues: requiredString(databaseConfig, 'database.collections.validation_issues', databaseFile),
    changes: requiredString(databaseConfig, 'database.collections.changes', databaseFile)
  };

  const aerodromeFormat = requiredString(
    validationConfig,
    'validation.natural_key_formats.aerodrome',
    validationFile
  );

  let aerodromePattern: RegExp;
  try {
    aerodromePattern = new RegExp(aerodromeFormat);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`${validationFile}: invalid regex at "validation.natural_key_formats.aerodrome" (${message})`);
  }

  const port = process.env.PORT === undefined ? 3000 : Number(process.env.PORT);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error('environment: PORT must be an integer between 0 and 65535');
  }

  return { uri, dbName, collections, aerodromePattern, port };
}

export async function connect(config: ApiConfig): Promise<{ client: MongoClient; db: Db }> {
  const client = new MongoClient(config.uri);
  await client.connect();
  return { client, db: client.db(config.dbName) };
}
