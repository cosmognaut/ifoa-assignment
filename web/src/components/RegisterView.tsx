import { useMemo } from 'react';
import type { ReactNode } from 'react';
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

function sectionOf(fields: Fields): string {
  const first = Object.values(fields)[0];
  return first === undefined ? '—' : first.section;
}

const SUMMARY_CHARS = 80;

/** Plain text for a stored value, without JSX — used where a summary needs a string. */
function textOf(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(textOf).filter((part) => part !== '').join(', ');
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const parts = [record.value, record.unit, record.reference]
      .filter((part) => part !== null && part !== undefined && part !== '')
      .map((part) => String(part));
    if (parts.length > 0) return parts.join(' ');
    return typeof record.raw === 'string' ? record.raw : '';
  }
  return '';
}

function truncated(text: string): string {
  return text.length > SUMMARY_CHARS ? `${text.slice(0, SUMMARY_CHARS).trimEnd()}…` : text;
}

/**
 * One type-aware summary per row. The register now holds every feature without
 * geometry, so the old LOWER/UPPER/ACTIVITY columns were airspace-shaped and the
 * operational-info rows read as missing data. Airspace keeps the real limit
 * renderer (so `UNL` still collapses and 999 never leaks); everything else gets a
 * summary derived from its own fields rather than a hardcoded per-key table.
 */
function summaryOf(feature: GeoJsonFeature): ReactNode {
  const { featureType, fields } = feature.properties;

  if (featureType === 'airspace') {
    if (fields.lowerLimit === undefined && fields.upperLimit === undefined) return '—';
    return (
      <>
        {renderValue(fields.lowerLimit?.value ?? null)} – {renderValue(fields.upperLimit?.value ?? null)}
      </>
    );
  }

  if (featureType === 'specialUseArea') {
    const activity = textOf(fields.timeOfActivity?.value);
    const restriction = truncated(textOf(fields.restrictionType?.value));
    if (activity !== '' && restriction !== '') return `${activity} · ${restriction}`;
    return activity || restriction || '—';
  }

  if (featureType === 'operationalInfo') {
    const names = Object.keys(fields);
    const longest = names.reduce((max, name) => Math.max(max, textOf(fields[name]?.value).length), 0);
    return `${names.length} fields · ${longest} characters`;
  }

  for (const name of Object.keys(fields)) {
    const text = textOf(fields[name]?.value);
    if (text !== '') return truncated(text);
  }
  return '—';
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
    <section className="register" aria-label="Non-geometric features">
      <header className="register-header">
        <h2 className="t1">NON-GEOMETRIC FEATURES</h2>
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
          <span role="columnheader" className="t1">SUMMARY</span>
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
                <span role="cell" className="register-summary t3">
                  {summaryOf(feature)}
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
