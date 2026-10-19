import type { Field, Fields, GeoJsonFeature, Geometry, ValidationIssue } from '../types';

/**
 * Minimal stand-ins for the API payloads. They mirror the shapes the API
 * actually returns (`api/server.ts` projects `_id` away and passes provenance
 * through untouched) without dragging the 619-feature dataset into the suite.
 */

const DEFAULT_SOURCE = 'Belgium & Luxembourg eAIP';
const DEFAULT_SECTION = 'EBBR AD 2.13';
const DEFAULT_EFFECTIVE_DATE = '2026-10-01';
const DEFAULT_RETRIEVED_AT = '2026-10-19T15:37:36.523447+00:00';
const DEFAULT_SOURCE_URL =
  'https://ops.skeyes.be/html/belgocontrol_static/eaip/eAIP_Main/html/eAIP/EB-AD-2.EBBR-en-GB.html';

export function makeField(overrides: Partial<Field> = {}): Field {
  return {
    value: null,
    unit: null,
    source: DEFAULT_SOURCE,
    section: DEFAULT_SECTION,
    effective_date: DEFAULT_EFFECTIVE_DATE,
    retrieved_at: DEFAULT_RETRIEVED_AT,
    source_url: DEFAULT_SOURCE_URL,
    parser_version: '1.0',
    raw_text: '',
    ...overrides
  };
}

export interface FeatureOptions {
  naturalKey?: string;
  parentKey?: string | null;
  featureType?: string;
  geometry?: Geometry | null;
  contentHash?: string;
  fields?: Fields;
}

export function makeFeature(options: FeatureOptions = {}): GeoJsonFeature {
  return {
    type: 'Feature',
    geometry: options.geometry ?? null,
    properties: {
      naturalKey: options.naturalKey ?? 'WPT/GIVOR',
      parentKey: options.parentKey ?? null,
      featureType: options.featureType ?? 'waypoint',
      publicationId: '2026-10-01-current',
      effectiveFrom: '2026-10-01',
      effectiveTo: null,
      sourceClass: 'AIP',
      temporality: 'permanent',
      contentHash: options.contentHash ?? '3f9a1c0e7b5d2a64',
      fields: options.fields ?? {}
    }
  };
}

/** The coordinate field the AIP prints as `504615N 0051904E` (lon, lat in GeoJSON). */
export function makeCoordinateField(): Field {
  return makeField({
    value: [5.317778, 50.770833],
    unit: null,
    section: 'ENR 4.4',
    effective_date: DEFAULT_EFFECTIVE_DATE,
    source_url:
      'https://ops.skeyes.be/html/belgocontrol_static/eaip/eAIP_Main/html/eAIP/EB-ENR-4.4-en-GB.html',
    raw_text: '504615N 0051904E'
  });
}

export interface IssueOptions {
  ruleId?: string;
  severity?: string;
  featureKey?: string | null;
  message?: string;
  rawSnippet?: string;
}

export function makeIssue(options: IssueOptions = {}): ValidationIssue {
  return {
    publicationId: '2026-10-01-current',
    severity: options.severity ?? 'warning',
    ruleId: options.ruleId ?? 'RULE_OPERATIONAL_BBOX',
    featureKey: options.featureKey ?? 'WPT/GIVOR',
    section: 'ENR 4.4',
    message: options.message ?? 'Coordinates outside operational FIR bounding box: [6.391389, 48.658611]',
    rawSnippet: options.rawSnippet ?? ''
  };
}
