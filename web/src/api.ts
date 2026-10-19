import type {
  FeatureCollection,
  FeatureProperties,
  FeatureTypeCount,
  Field,
  Fields,
  GeoJsonFeature,
  Geometry,
  PointCoordinates,
  Publication
} from './types';

const API_ROOT = '/api';

export class ApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isPointCoordinates(value: unknown): value is PointCoordinates {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    isFiniteNumber(value[0]) &&
    isFiniteNumber(value[1])
  );
}

function isGeometry(value: unknown): value is Geometry | null {
  if (value === null) return true;
  if (!isRecord(value)) return false;
  if (value.type === 'Point') return isPointCoordinates(value.coordinates);
  if (value.type === 'LineString') {
    return Array.isArray(value.coordinates) && value.coordinates.every(isPointCoordinates);
  }
  return false;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function isField(value: unknown): value is Field {
  if (!isRecord(value)) return false;
  return (
    'value' in value &&
    isNullableString(value.unit) &&
    typeof value.source === 'string' &&
    typeof value.section === 'string' &&
    typeof value.effective_date === 'string' &&
    typeof value.retrieved_at === 'string' &&
    isNullableString(value.source_url) &&
    typeof value.parser_version === 'string' &&
    typeof value.raw_text === 'string'
  );
}

function isFields(value: unknown): value is Fields {
  if (!isRecord(value)) return false;
  return Object.values(value).every(isField);
}

function isFeatureProperties(value: unknown): value is FeatureProperties {
  if (!isRecord(value)) return false;
  return (
    typeof value.naturalKey === 'string' &&
    isNullableString(value.parentKey) &&
    typeof value.featureType === 'string' &&
    typeof value.publicationId === 'string' &&
    typeof value.effectiveFrom === 'string' &&
    isNullableString(value.effectiveTo) &&
    typeof value.sourceClass === 'string' &&
    typeof value.temporality === 'string' &&
    typeof value.contentHash === 'string' &&
    isFields(value.fields)
  );
}

export function isFeature(value: unknown): value is GeoJsonFeature {
  if (!isRecord(value)) return false;
  return (
    value.type === 'Feature' &&
    isGeometry(value.geometry) &&
    isFeatureProperties(value.properties)
  );
}

export function isFeatureCollection(value: unknown): value is FeatureCollection {
  if (!isRecord(value)) return false;
  return (
    value.type === 'FeatureCollection' &&
    Array.isArray(value.features) &&
    value.features.every(isFeature)
  );
}

export function isFeatureTypeCount(value: unknown): value is FeatureTypeCount {
  if (!isRecord(value)) return false;
  return (
    typeof value.featureType === 'string' &&
    isFiniteNumber(value.count) &&
    value.count >= 0
  );
}

function isPublication(value: unknown): value is Publication {
  if (!isRecord(value)) return false;
  return (
    typeof value.cycleId === 'string' &&
    typeof value.amendmentId === 'string' &&
    typeof value.effectiveDate === 'string' &&
    typeof value.status === 'string'
  );
}

async function fetchJson<T>(path: string, guard: (value: unknown) => value is T): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { headers: { Accept: 'application/json' } });
  } catch {
    throw new ApiError(`Network request to ${path} failed`, null);
  }

  if (!response.ok) {
    throw new ApiError(`${path} responded with ${response.status}`, response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(`${path} returned malformed JSON`, response.status);
  }

  if (!guard(payload)) {
    throw new ApiError(`${path} returned an unexpected response shape`, response.status);
  }

  return payload;
}

export function getFeatureTypes(): Promise<FeatureTypeCount[]> {
  return fetchJson(
    `${API_ROOT}/feature-types`,
    (value: unknown): value is FeatureTypeCount[] =>
      Array.isArray(value) && value.every(isFeatureTypeCount)
  );
}

export function getFeatures(withGeometry: boolean): Promise<FeatureCollection> {
  const query = withGeometry ? '?withGeometry=true' : '';
  return fetchJson(`${API_ROOT}/features${query}`, isFeatureCollection);
}

export function getPublications(): Promise<Publication[]> {
  return fetchJson(
    `${API_ROOT}/publications`,
    (value: unknown): value is Publication[] =>
      Array.isArray(value) && value.every(isPublication)
  );
}
