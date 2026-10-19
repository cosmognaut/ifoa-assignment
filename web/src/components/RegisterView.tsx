import { useMemo } from 'react';
import type { FeatureType, Fields, GeoJsonFeature } from '../types';
import { renderValue } from './FieldRow';

export const REGISTER_ALL = 'all';

export interface RegisterViewProps {
  features: GeoJsonFeature[];
  filter: string;
  onFilterChange: (filter: string) => void;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}

function fieldValue(fields: Fields, name: string): unknown {
  return fields[name]?.value;
}

function sectionOf(fields: Fields): string {
  const first = Object.values(fields)[0];
  return first === undefined ? '—' : first.section;
}

export function RegisterView({
  features,
  filter,
  onFilterChange,
  selectedKey,
  onSelect
}: RegisterViewProps) {
  const unmapped = useMemo(
    () => features.filter((feature) => feature.geometry === null),
    [features]
  );

  const counts = useMemo(() => {
    const next = new Map<FeatureType, number>();
    for (const feature of unmapped) {
      const type = feature.properties.featureType;
      next.set(type, (next.get(type) ?? 0) + 1);
    }
    return next;
  }, [unmapped]);

  const rows = useMemo(() => {
    const filtered =
      filter === REGISTER_ALL
        ? unmapped
        : unmapped.filter((feature) => feature.properties.featureType === filter);
    return [...filtered].sort((a, b) =>
      a.properties.naturalKey.localeCompare(b.properties.naturalKey)
    );
  }, [unmapped, filter]);

  const chips = useMemo(
    () => [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0])),
    [counts]
  );

  return (
    <section className="register" aria-label="Airspace register">
      <header className="register-header">
        <h2 className="t1">AIRSPACE REGISTER</h2>
        <span className="register-count t2 mono">{rows.length} rows</span>
      </header>

      <div className="register-filters">
        <button
          type="button"
          className={`chip-filter${filter === REGISTER_ALL ? ' is-active' : ''}`}
          onClick={() => onFilterChange(REGISTER_ALL)}
        >
          All ({unmapped.length})
        </button>
        {chips.map(([type, count]) => (
          <button
            type="button"
            key={type}
            className={`chip-filter${filter === type ? ' is-active' : ''}`}
            onClick={() => onFilterChange(type)}
          >
            {type} ({count})
          </button>
        ))}
      </div>

      <div className="register-table" role="table" aria-label="Features without geometry">
        <div className="register-head" role="row">
          <span role="columnheader" className="t1">ID</span>
          <span role="columnheader" className="t1">TYPE</span>
          <span role="columnheader" className="t1">LOWER</span>
          <span role="columnheader" className="t1">UPPER</span>
          <span role="columnheader" className="t1">ACTIVITY</span>
          <span role="columnheader" className="t1">SECTION</span>
        </div>

        <div className="register-body">
          {rows.map((feature) => {
            const { fields, naturalKey, featureType } = feature.properties;
            const selected = naturalKey === selectedKey;
            return (
              <button
                type="button"
                role="row"
                key={naturalKey}
                className={`register-row${selected ? ' is-selected' : ''}`}
                aria-selected={selected}
                onClick={() => onSelect(naturalKey)}
              >
                <span role="cell" className="register-id t3 mono">{naturalKey}</span>
                <span role="cell" className="register-type t2">{featureType}</span>
                <span role="cell" className="register-cell t3 mono">
                  {renderValue(fieldValue(fields, 'lowerLimit'))}
                </span>
                <span role="cell" className="register-cell t3 mono">
                  {renderValue(fieldValue(fields, 'upperLimit'))}
                </span>
                <span role="cell" className="register-cell t3 mono">
                  {renderValue(fieldValue(fields, 'timeOfActivity'))}
                </span>
                <span role="cell" className="register-section t2 mono">{sectionOf(fields)}</span>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
