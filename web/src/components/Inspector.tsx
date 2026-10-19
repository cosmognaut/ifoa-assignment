import { useEffect, useState } from 'react';
import type { GeoJsonFeature } from '../types';
import { FieldRow, renderValue } from './FieldRow';
import { ProvenanceBlock } from './ProvenanceBlock';

export interface InspectorProps {
  feature: GeoJsonFeature | null;
  loading: boolean;
}

const LATERAL_KEY = 'lateralLimitsRaw';

function InspectorEmpty() {
  return (
    <div className="inspector-void">
      <p className="inspector-empty t2">
        Select any map feature or table row to inspect its attributes and eAIP provenance.
      </p>
    </div>
  );
}

function InspectorSkeleton() {
  return (
    <div className="inspector-skeleton" aria-label="Loading feature">
      <div className="skeleton-bar" />
      {Array.from({ length: 6 }, (_, index) => (
        <div className="skeleton-row" key={index} />
      ))}
    </div>
  );
}

export function Inspector({ feature, loading }: InspectorProps) {
  const [selectedField, setSelectedField] = useState<string | null>(null);
  const naturalKey = feature === null ? null : feature.properties.naturalKey;

  useEffect(() => {
    setSelectedField(null);
  }, [naturalKey]);

  if (feature === null) {
    return (
      <aside className="inspector" aria-label="Inspector">
        {loading ? <InspectorSkeleton /> : <InspectorEmpty />}
      </aside>
    );
  }

  const { properties } = feature;
  const entries = Object.entries(properties.fields);
  const firstEntry = entries[0];
  const activeFieldName =
    selectedField !== null && selectedField in properties.fields
      ? selectedField
      : (firstEntry?.[0] ?? null);
  const activeField = activeFieldName === null ? null : properties.fields[activeFieldName] ?? null;
  const lateral = properties.fields[LATERAL_KEY];

  return (
    <aside className="inspector" aria-label="Inspector">
      <header className="inspector-header">
        <span className="type-badge t1 mono">{properties.featureType}</span>
        <h2 className="inspector-key t4 mono">{properties.naturalKey}</h2>
        <p className="inspector-subline t2 mono">
          parent {properties.parentKey ?? '—'} · eff {properties.effectiveFrom}
        </p>
      </header>

      <div className="inspector-scroll">
        {entries
          .filter(([name]) => name !== LATERAL_KEY)
          .map(([name, field]) => (
            <FieldRow
              key={name}
              name={name}
              field={field}
              selected={name === activeFieldName}
              onSelect={() => setSelectedField(name)}
            />
          ))}

        {lateral === undefined ? null : (
          <div className="raw-block">
            <div className="raw-block-title t1">LATERAL LIMITS RAW</div>
            <div className="raw-well t2 mono">{renderValue(lateral.value)}</div>
          </div>
        )}
      </div>

      <ProvenanceBlock field={activeField} />
    </aside>
  );
}
