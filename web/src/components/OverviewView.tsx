import { useMemo } from 'react';
import { isRecord } from '../api';
import type { FeatureTypeCount, GeoJsonFeature, Publication } from '../types';

function textValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

function aerodromeFullName(feature: GeoJsonFeature): string | null {
  const fields = feature.properties.fields;
  for (const key of ['airportName', 'designation', 'name']) {
    const text = textValue(fields[key]?.value);
    if (text !== null) return text;
  }
  return null;
}

/** The AIP prints "BRUSSELS / Brussels-National"; show only the airport part. */
function aerodromeName(feature: GeoJsonFeature): string | null {
  const full = aerodromeFullName(feature);
  if (full === null) return null;
  const separator = full.indexOf(' / ');
  return separator === -1 ? full : full.slice(separator + 3).trim();
}

function aerodromeElevation(feature: GeoJsonFeature): string | null {
  const field = feature.properties.fields.elevation;
  if (field === undefined || field.value === null || field.value === undefined) return null;
  const unit = field.unit === null ? '' : ` ${field.unit}`;
  return `${String(field.value)}${unit}`;
}

function numericStat(stats: Record<string, unknown> | null, key: string): string {
  if (stats === null) return '—';
  const value = stats[key];
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '—';
}

const METHOD_STEPS = [
  {
    step: '01',
    title: 'Discover',
    description:
      "The publisher's portal is probed for previous, current and preview editions. A cycle is identified by its AIRAC effective date, not by when it was downloaded."
  },
  {
    step: '02',
    title: 'Acquire',
    description:
      'Documents are fetched byte for byte at one request per second and registered in a SHA-256 manifest, so parsing can be replayed against the exact bytes without the network.'
  },
  {
    step: '03',
    title: 'Parse',
    description:
      'Declarative table locators map AIP cells to typed fields. Every value keeps its section, effective date, source URL and the exact raw text it came from.'
  },
  {
    step: '04',
    title: 'Validate',
    description:
      'Coordinate bounds, declared-distance physics and disappearance thresholds gate promotion. A cycle that fails is quarantined rather than published.'
  },
  {
    step: '05',
    title: 'Compare',
    description:
      'Consecutive cycles are diffed by natural key and content hash into NEW, MODIFIED and DELETED, so an amendment arrives as a reviewable change record rather than a replacement.'
  }
];

export interface OverviewViewProps {
  publication: Publication | null;
  types: FeatureTypeCount[];
  features: GeoJsonFeature[];
  aerodromes: GeoJsonFeature[];
  onSelectAerodrome: (naturalKey: string) => void;
}

export function OverviewView({
  publication,
  types,
  features,
  aerodromes,
  onSelectAerodrome
}: OverviewViewProps) {
  const runwayCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const feature of features) {
      if (feature.properties.featureType !== 'runway') continue;
      const parent = feature.properties.parentKey;
      if (parent === null) continue;
      counts.set(parent, (counts.get(parent) ?? 0) + 1);
    }
    return counts;
  }, [features]);

  const stats = publication !== null && isRecord(publication.stats) ? publication.stats : null;
  const status = publication === null ? '—' : publication.status;
  const manifest =
    publication !== null && typeof publication.manifestSha256 === 'string'
      ? publication.manifestSha256
      : '—';
  const ingested =
    publication !== null && typeof publication.ingestedAt === 'string'
      ? publication.ingestedAt
      : '—';
  const runRows = [
    { label: 'CYCLE', value: publication?.cycleId ?? '—' },
    { label: 'AMENDMENT', value: publication?.amendmentId ?? '—' },
    { label: 'EFFECTIVE', value: publication?.effectiveDate ?? '—' },
    { label: 'DOCUMENTS', value: numericStat(stats, 'total_documents') },
    { label: 'FEATURES', value: numericStat(stats, 'parsed_features') },
    { label: 'ISSUES', value: numericStat(stats, 'issue_count') },
    { label: 'ERRORS', value: numericStat(stats, 'error_count') },
    { label: 'WARNINGS', value: numericStat(stats, 'warning_count') }
  ];

  return (
    <section className="overview" aria-label="Overview">
      <div className="overview-inner">
        <header className="overview-hero">
          <h1 className="hero">Belgian eAIP</h1>
          <p className="overview-subline t3">
            {publication === null
              ? 'Publication cycle unavailable.'
              : `${publication.cycleId} · ${publication.amendmentId} · effective ${publication.effectiveDate}`}
          </p>
          {aerodromes.length === 0 ? null : (
            <p className="overview-scope t3">
              Data for{' '}
              {aerodromes.map((feature, index) => {
                const code = feature.properties.naturalKey;
                const name = aerodromeName(feature);
                const separator =
                  index === aerodromes.length - 1
                    ? ''
                    : index === aerodromes.length - 2
                      ? ' and '
                      : ', ';
                return (
                  <span key={code}>
                    {name === null ? null : `${name} `}
                    <span className="mono">({code})</span>
                    {separator}
                  </span>
                );
              })}
            </p>
          )}
        </header>

        <div className="overview-counts" aria-label="Feature counts">
          {types.map((type) => (
            <div className="overview-count" key={type.featureType}>
              <span className="overview-count-value t5 mono">{type.count}</span>
              <span className="overview-count-label t2">{type.featureType}</span>
            </div>
          ))}
        </div>

        <section className="overview-section" aria-label="Aerodromes">
          <h2 className="overview-section-head t1">Aerodromes</h2>
          <div className="overview-grid">
            {aerodromes.map((feature) => {
              const key = feature.properties.naturalKey;
              const name = aerodromeName(feature);
              const elevation = aerodromeElevation(feature);
              const runwayCount = runwayCounts.get(key) ?? 0;
              return (
                <button
                  type="button"
                  key={key}
                  className="overview-card"
                  aria-label={`Open ${name ?? key} operational summary`}
                  onClick={() => onSelectAerodrome(key)}
                >
                  <span className="overview-card-icao t6">{key}</span>
                  {name === null ? null : (
                    <span className="overview-card-name t3">{name}</span>
                  )}
                  {elevation === null ? null : (
                    <span className="overview-card-elev t3 mono">{elevation}</span>
                  )}
                  <span className="overview-card-footer t2">
                    <span>
                      {runwayCount} {runwayCount === 1 ? 'runway' : 'runways'}
                    </span>
                    <span className="overview-card-action">Open summary →</span>
                  </span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="overview-section" aria-label="Methodology">
          <h2 className="overview-section-head t1">Methodology</h2>
          <div className="overview-method-grid">
            <ol className="overview-method">
              {METHOD_STEPS.map((item) => (
                <li className="overview-method-row" key={item.step}>
                  <span className="overview-method-step t3 mono">{item.step}</span>
                  <span className="overview-method-title t3">{item.title}</span>
                  <span className="overview-method-desc t3">{item.description}</span>
                </li>
              ))}
            </ol>

            <aside className="run-panel" aria-label="Current run">
              <header className="run-panel-head">
                <span className="run-panel-title t1">CURRENT RUN</span>
                <span className="run-panel-status">
                  <span className="run-panel-dot" aria-hidden="true" />
                  <span className="run-panel-status-text t2">{status}</span>
                </span>
              </header>

              <dl className="run-panel-rows">
                {runRows.map((row) => (
                  <div className="run-panel-row" key={row.label}>
                    <dt className="run-panel-label t2">{row.label}</dt>
                    <dd className="run-panel-value t2 mono">{row.value}</dd>
                  </div>
                ))}
              </dl>

              <div className="run-panel-block">
                <div className="run-panel-meta-label t1">MANIFEST</div>
                <div className="run-panel-manifest t2 mono">{manifest}</div>
                <div className="run-panel-meta-label run-panel-ingested-label t1">INGESTED</div>
                <div className="run-panel-ingested t2 mono">{ingested}</div>
              </div>
            </aside>
          </div>
        </section>

        <footer className="overview-signature t1">a cosmognaut production</footer>
      </div>
    </section>
  );
}
