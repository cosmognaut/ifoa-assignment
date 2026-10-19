import type { ReactNode } from 'react';
import { isRecord } from '../api';
import type { Field } from '../types';

export function humaniseFieldName(name: string): string {
  return name
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/_/g, ' ')
    .trim()
    .split(/\s+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function scalarText(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return null;
}

function renderLimit(value: Record<string, unknown>): ReactNode {
  if (value.reference === 'UNL') return 'UNL';

  const parts = [value.value, value.unit, value.reference]
    .map(scalarText)
    .filter((part): part is string => part !== null);

  const joined = parts.join(' ').trim();
  if (joined !== '') return joined;

  const raw = scalarText(value.raw);
  return raw ?? '—';
}

function unitAlreadyInValue(value: unknown): boolean {
  return isRecord(value) && ('reference' in value || 'unit' in value);
}

export function renderValue(value: unknown): ReactNode {
  if (value === null || value === undefined || value === '') return '—';

  if (Array.isArray(value)) {
    if (value.length === 0) return '—';
    if (value.every((item): item is string => typeof item === 'string')) {
      return (
        <span className="chips">
          {value.map((item, index) => (
            <span className="chip t1 mono" key={`${item}-${index}`}>
              {item}
            </span>
          ))}
        </span>
      );
    }
    return value
      .map((item) => scalarText(item) ?? JSON.stringify(item))
      .join(', ');
  }

  if (isRecord(value)) {
    if ('reference' in value) return renderLimit(value);
    if ('value' in value) return renderValue(value.value);
    return JSON.stringify(value);
  }

  return String(value);
}

function isNumericValue(value: unknown): boolean {
  if (typeof value === 'number') return true;
  if (Array.isArray(value)) {
    return value.length > 0 && value.every((item) => typeof item === 'number');
  }
  if (isRecord(value)) {
    if ('reference' in value || 'unit' in value) return true;
    if ('value' in value) return isNumericValue(value.value);
  }
  return false;
}

export interface FieldRowProps {
  name: string;
  field: Field;
  selected: boolean;
  onSelect: () => void;
}

export function FieldRow({ name, field, selected, onSelect }: FieldRowProps) {
  return (
    <button
      type="button"
      className={`field-row${selected ? ' is-selected' : ''}`}
      aria-pressed={selected}
      onClick={onSelect}
    >
      <span className="field-label t3">{humaniseFieldName(name)}</span>
      <span className="field-content">
        <span className="field-value-line">
          <span className={`field-value t3${isNumericValue(field.value) ? ' mono' : ''}`}>
            {renderValue(field.value)}
          </span>
          {field.unit && !unitAlreadyInValue(field.value) ? (
            <span className="field-unit t2 mono">{field.unit}</span>
          ) : null}
        </span>
        <span className="field-meta t1 mono">
          {field.section} · eff {field.effective_date}
        </span>
      </span>
    </button>
  );
}
