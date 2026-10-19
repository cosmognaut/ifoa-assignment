export type FeatureType = string;

export type PointCoordinates = [lon: number, lat: number];

export type Geometry =
  | { type: 'Point'; coordinates: PointCoordinates }
  | { type: 'LineString'; coordinates: PointCoordinates[] };

export interface Field {
  value: unknown;
  unit: string | null;
  source: string;
  section: string;
  effective_date: string;
  retrieved_at: string;
  source_url: string | null;
  parser_version: string;
  raw_text: string;
}

export type Fields = Record<string, Field>;

export interface FeatureProperties {
  naturalKey: string;
  parentKey: string | null;
  featureType: FeatureType;
  publicationId: string;
  effectiveFrom: string;
  effectiveTo: string | null;
  sourceClass: string;
  temporality: string;
  contentHash: string;
  fields: Fields;
}

export interface GeoJsonFeature {
  type: 'Feature';
  geometry: Geometry | null;
  properties: FeatureProperties;
}

export interface FeatureCollection {
  type: 'FeatureCollection';
  features: GeoJsonFeature[];
}

export interface FeatureTypeCount {
  featureType: FeatureType;
  count: number;
}

export interface Publication {
  cycleId: string;
  amendmentId: string;
  effectiveDate: string;
  status: string;
  [key: string]: unknown;
}
