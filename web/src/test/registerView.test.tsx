import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { REGISTER_ALL, RegisterView } from '../components/RegisterView';
import { makeFeature, makeField } from './fixtures';

const features = [
  makeFeature({ naturalKey: 'ASP/BRUSSELS CTA EAST ONE', featureType: 'airspace', geometry: null }),
  makeFeature({ naturalKey: 'ASP/BRUSSELS CTA EAST TWO', featureType: 'airspace', geometry: null }),
  makeFeature({ naturalKey: 'SUA/EBR04', featureType: 'specialUseArea', geometry: null })
];

const limitField = (raw: string) =>
  makeField({ value: { raw, value: null, unit: null, reference: null } });

function summaryFeatures() {
  return [
    makeFeature({
      naturalKey: 'ASP/BEAUVECHAIN TMA FOUR',
      featureType: 'airspace',
      geometry: null,
      fields: {
        lowerLimit: limitField('2 500 FT AMSL'),
        upperLimit: limitField('4 500 FT AMSL')
      }
    }),
    makeFeature({
      naturalKey: 'SUA/EBR01',
      featureType: 'specialUseArea',
      geometry: null,
      fields: {
        timeOfActivity: makeField({ value: 'PERM' }),
        restrictionType: makeField({
          value: 'Entry prohibited, unless instructed by ATC . An additional sentence that pushes the restriction text well past the eighty character limit used by the summary column.'
        })
      }
    }),
    makeFeature({
      naturalKey: 'EBBR/OPS/LOCAL-REGS',
      featureType: 'operationalInfo',
      geometry: null,
      fields: {
        localRegulationsText: makeField({ value: 'x'.repeat(24602) }),
        pprRequired: makeField({ value: false })
      }
    }),
    makeFeature({
      naturalKey: 'ASP/UNLIMITED TEST',
      featureType: 'airspace',
      geometry: null,
      fields: {
        lowerLimit: limitField('GND'),
        upperLimit: makeField({ value: { value: 999, unit: 'FL', reference: 'UNL' } })
      }
    })
  ];
}

function renderSummary() {
  return render(
    <RegisterView
      features={summaryFeatures()}
      filter={REGISTER_ALL}
      onFilterChange={() => {}}
      selectedKey={null}
      onSelect={() => {}}
    />
  );
}

function rowText(naturalKey: string): string {
  const row = [...document.querySelectorAll('.register-row')].find((n) =>
    (n.textContent ?? '').includes(naturalKey)
  );
  return row?.textContent ?? '';
}

function Harness() {
  const [filter, setFilter] = useState<string>(REGISTER_ALL);
  return (
    <RegisterView
      features={features}
      filter={filter}
      onFilterChange={setFilter}
      selectedKey={null}
      onSelect={() => {}}
    />
  );
}

function rowCount(): number {
  return document.querySelectorAll('.register-row').length;
}

describe('RegisterView filtering', () => {
  it('narrows to one row for a type and restores the full list on All', () => {
    render(<Harness />);

    expect(rowCount()).toBe(3);
    expect(screen.getByText('SUA/EBR04')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /specialUseArea/ }));

    expect(rowCount()).toBe(1);
    expect(screen.getByText('SUA/EBR04')).toBeInTheDocument();
    expect(screen.queryByText('ASP/BRUSSELS CTA EAST ONE')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /^All/ }));

    expect(rowCount()).toBe(3);
  });
});

describe('RegisterView summary column', () => {
  it('renders both airspace limits through the shared limit renderer', () => {
    renderSummary();

    expect(rowText('ASP/BEAUVECHAIN TMA FOUR')).toContain('2 500 FT AMSL – 4 500 FT AMSL');
  });

  it('summarises operationalInfo rows by field count and longest text', () => {
    renderSummary();

    const text = rowText('EBBR/OPS/LOCAL-REGS');

    expect(text).toContain('2 fields · 24602 characters');
    expect(text).not.toContain('—');
  });

  it('keeps the UNL collapse and never prints 999', () => {
    renderSummary();

    const text = rowText('ASP/UNLIMITED TEST');

    expect(text).toContain('UNL');
    expect(text).not.toContain('999');
  });

  it('truncates special-use-area restriction text with an ellipsis', () => {
    renderSummary();

    const text = rowText('SUA/EBR01');

    expect(text).toContain('PERM · Entry prohibited');
    expect(text).toContain('…');
    // 80 characters of restriction text plus the activity prefix and separators.
    expect(text.length).toBeLessThan(140);
  });

  it('titles the panel for what it now contains', () => {
    renderSummary();

    expect(screen.getByText('NON-GEOMETRIC FEATURES')).toBeInTheDocument();
    expect(screen.queryByText('AIRSPACE REGISTER')).toBeNull();
  });
});
