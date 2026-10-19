'use strict';

const fs = require('node:fs');
const path = require('node:path');
const yaml = require('js-yaml');
const { MongoClient } = require('mongodb');

const REPO_ROOT = path.resolve(__dirname, '..');
const DATABASE_CONFIG = path.join(REPO_ROOT, 'config', 'database.yaml');
const VALIDATION_CONFIG = path.join(REPO_ROOT, 'config', 'validation.yaml');

function readYaml(file) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    throw new Error(`${path.relative(REPO_ROOT, file)}: cannot read config (${err.code})`);
  }
  try {
    return yaml.load(text);
  } catch (err) {
    throw new Error(`${path.relative(REPO_ROOT, file)}: invalid YAML (${err.message})`);
  }
}

function required(source, dottedKey, file) {
  let value = source;
  for (const part of dottedKey.split('.')) {
    if (value === null || typeof value !== 'object' || !Object.prototype.hasOwnProperty.call(value, part)) {
      throw new Error(`${file}: missing required key "${dottedKey}"`);
    }
    value = value[part];
  }
  if (value === undefined || value === null || value === '') {
    throw new Error(`${file}: missing required key "${dottedKey}"`);
  }
  return value;
}

function loadConfig() {
  const databaseFile = path.relative(REPO_ROOT, DATABASE_CONFIG);
  const validationFile = path.relative(REPO_ROOT, VALIDATION_CONFIG);
  const databaseConfig = readYaml(DATABASE_CONFIG);
  const validationConfig = readYaml(VALIDATION_CONFIG);

  const uri = process.env.MONGO_URI || required(databaseConfig, 'database.uri', databaseFile);
  const dbName = required(databaseConfig, 'database.name', databaseFile);

  const collections = {
    publications: required(databaseConfig, 'database.collections.publications', databaseFile),
    features: required(databaseConfig, 'database.collections.features', databaseFile),
    validationIssues: required(databaseConfig, 'database.collections.validation_issues', databaseFile),
    changes: required(databaseConfig, 'database.collections.changes', databaseFile)
  };

  const aerodromeFormat = required(
    validationConfig,
    'validation.natural_key_formats.aerodrome',
    validationFile
  );

  let aerodromePattern;
  try {
    aerodromePattern = new RegExp(aerodromeFormat);
  } catch (err) {
    throw new Error(`${validationFile}: invalid regex at "validation.natural_key_formats.aerodrome" (${err.message})`);
  }

  const port = process.env.PORT === undefined ? 3000 : Number(process.env.PORT);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error('environment: PORT must be an integer between 0 and 65535');
  }

  return { uri, dbName, collections, aerodromePattern, port };
}

async function connect(config) {
  const client = new MongoClient(config.uri);
  await client.connect();
  return { client, db: client.db(config.dbName) };
}

module.exports = { loadConfig, connect };
