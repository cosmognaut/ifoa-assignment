import { useMemo } from 'react';
import type { FeatureType, FeatureTypeCount, GeoJsonFeature } from '../types';

const FEATURE_COLOUR_TOKENS: Record<string, string> = {
  aerodrome: '--accent',
  navaid: '--ok-fg',
  waypoint: '--text-muted',
  runway: '--text-primary',
  runwayDirection: '--warn-fg',
  route: '--text-secondary',
  airspace: '--err-fg',
  specialUseArea: '--err-fg'
};

const FALLBACK_TOKEN = '--err-fg';

const tokenCache = new Map<string, string>();

function resolveToken(name: string): string {
  const cached = tokenCache.get(name);
  if (cached !== undefined) return cached;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const resolved = value === '' ? '#7c8b9e' : value;
  tokenCache.set(name, resolved);
  return resolved;
}

export function featureColour(type: FeatureType): string {
  return resolveToken(FEATURE_COLOUR_TOKENS[type] ?? FALLBACK_TOKEN);
}

export interface LayerPanelProps {
  types: FeatureTypeCount[];
  features: GeoJsonFeature[];
  drawable: GeoJsonFeature[];
  active: Set<string>;
  onToggle: (type: string) => void;
  onShowRegister: (type: string) => void;
}

function countByType(features: GeoJsonFeature[], nullOnly: boolean): Map<string, number> {
  const counts = new Map<string, number>();
  for (const feature of features) {
    if (nullOnly !== (feature.geometry === null)) continue;
    const type = feature.properties.featureType;
    counts.set(type, (counts.get(type) ?? 0) + 1);
  }
  return counts;
}

export function LayerPanel({
  types,
  features,
  drawable,
  active,
  onToggle,
  onShowRegister
}: LayerPanelProps) {
  const drawableCounts = useMemo(() => countByType(drawable, false), [drawable]);
  const unmappedCounts = useMemo(() => countByType(features, true), [features]);

  const drawableRows = types.filter(
    (type) => (drawableCounts.get(type.featureType) ?? 0) > 0
  );
  const unmappedRows = types.filter(
    (type) => (unmappedCounts.get(type.featureType) ?? 0) > 0
  );

  return (
    <div className="layer-panel">
      <div className="layer-panel-head t1">LAYERS</div>

      <div className="layer-list">
        {drawableRows.map((type) => {
          const enabled = active.has(type.featureType);
          return (
            <button
              type="button"
              key={type.featureType}
              className="layer-row"
              aria-pressed={enabled}
              onClick={() => onToggle(type.featureType)}
            >
              <span className={`layer-toggle${enabled ? ' is-on' : ''}`} aria-hidden="true" />
              <span
                className="layer-swatch"
                style={{ background: featureColour(type.featureType) }}
                aria-hidden="true"
              />
              <span className="layer-label t2">{type.featureType}</span>
              <span className="layer-count t2 mono">
                {drawableCounts.get(type.featureType) ?? 0}
              </span>
            </button>
          );
        })}
      </div>

      <div className="layer-divider" />
      <div className="layer-panel-head t1">UNMAPPED · GEOMETRY NULL</div>

      <div className="layer-list">
        {unmappedRows.map((type) => (
          <button
            type="button"
            key={type.featureType}
            className="layer-row layer-row-unmapped"
            onClick={() => onShowRegister(type.featureType)}
          >
            <span className="warn-badge t1">no geometry</span>
            <span className="layer-label t2">{type.featureType}</span>
            <span className="layer-count t2 mono">
              {unmappedCounts.get(type.featureType) ?? 0}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
