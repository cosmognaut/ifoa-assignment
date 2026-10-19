import { useMemo } from 'react';
import type { FeatureTypeCount, GeoJsonFeature, Publication } from '../types';

function textValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

function aerodromeName(feature: GeoJsonFeature): string | null {
  const fields = feature.properties.fields;
  for (const key of ['designation', 'name']) {
    const text = textValue(fields[key]?.value);
    if (text !== null) return text;
  }
  return null;
}

function aerodromeElevation(feature: GeoJsonFeature): string | null {
  const field = feature.properties.fields.elevation;
  if (field === undefined || field.value === null || field.value === undefined) return null;
  const unit = field.unit === null ? '' : ` ${field.unit}`;
  return `${String(field.value)}${unit}`;
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

  const promoted = publication !== null && publication.status === 'promoted';

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
              const footer = promoted
                ? 'promoted'
                : `${runwayCount} ${runwayCount === 1 ? 'runway' : 'runways'}`;
              return (
                <button
                  type="button"
                  key={key}
                  className="overview-card"
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
                    <span className="overview-dot" aria-hidden="true" />
                    {footer}
                  </span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="overview-section" aria-label="Methodology">
          <h2 className="overview-section-head t1">Methodology</h2>
          <ol className="overview-method">
            {METHOD_STEPS.map((item) => (
              <li className="overview-method-row" key={item.step}>
                <span className="overview-method-step t3 mono">{item.step}</span>
                <span className="overview-method-title t3">{item.title}</span>
                <span className="overview-method-desc t3">{item.description}</span>
              </li>
            ))}
          </ol>
        </section>

        <footer className="overview-signature t1">a cosmognaut production</footer>
      </div>
    </section>
  );
}
