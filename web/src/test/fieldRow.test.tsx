import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FieldRow, humaniseFieldName, renderValue } from '../components/FieldRow';
import { makeCoordinateField, makeField } from './fixtures';

function textOf(value: unknown): string {
  const { container } = render(<div>{renderValue(value)}</div>);
  return container.textContent ?? '';
}

describe('renderValue', () => {
  it('renders an em dash for absent scalars', () => {
    expect(textOf(null)).toBe('—');
    expect(textOf(undefined)).toBe('—');
    expect(textOf('')).toBe('—');
  });

  it('renders one chip per string in an array', () => {
    const { container } = render(<div>{renderValue(['EBBR', 'EBCI', 'EBLG'])}</div>);
    const chips = [...container.querySelectorAll('.chip')].map((chip) => chip.textContent);
    expect(chips).toEqual(['EBBR', 'EBCI', 'EBLG']);
  });

  it('renders numeric arrays as a comma separated list', () => {
    expect(textOf([1, 2, 3])).toBe('1, 2, 3');
  });

  it('collapses an UNL limit to the reference alone', () => {
    const text = textOf({ value: 999, unit: 'FL', reference: 'UNL' });
    expect(text).toBe('UNL');
    expect(text).not.toContain('999');
  });

  it('joins value, unit and reference for an ordinary limit', () => {
    expect(textOf({ value: 55, unit: 'FL', reference: 'AMSL' })).toBe('55 FL AMSL');
  });

  it('unwraps a nested value object', () => {
    expect(textOf({ value: 'x' })).toBe('x');
  });
});

describe('humaniseFieldName', () => {
  const cases: [string, string][] = [
    ['atsRoutes', 'ATS Routes'],
    ['rffsCategory', 'RFFS Category'],
    ['arpCoordinates', 'ARP Coordinates'],
    ['operatorEmail', 'Operator Email'],
    ['waypointSequence', 'Waypoint Sequence']
  ];

  it.each(cases)('turns %s into %s', (input, expected) => {
    expect(humaniseFieldName(input)).toBe(expected);
  });
});

describe('FieldRow coordinate rendering', () => {
  it('prints coordinates latitude first and keeps the raw AIP string', () => {
    const { container } = render(
      <FieldRow name="coordinates" field={makeCoordinateField()} selected={false} onSelect={() => {}} />
    );
    const text = container.textContent ?? '';

    expect(text).toContain('50.770833° N, 5.317778° E');
    expect(text).toContain('504615N 0051904E');
    expect(text).not.toContain('5.317778, 50.770833');
  });

  it('does not apply coordinate formatting to a scalar field', () => {
    const { container } = render(
      <FieldRow
        name="elevation"
        field={makeField({ value: 175, unit: 'FT', raw_text: '175 FT' })}
        selected={false}
        onSelect={() => {}}
      />
    );
    const text = container.textContent ?? '';

    expect(text).toContain('175');
    expect(text).toContain('FT');
    expect(text).not.toContain('°');
  });
});
