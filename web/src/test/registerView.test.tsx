import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { REGISTER_ALL, RegisterView } from '../components/RegisterView';
import { makeFeature } from './fixtures';

const features = [
  makeFeature({ naturalKey: 'ASP/BRUSSELS CTA EAST ONE', featureType: 'airspace', geometry: null }),
  makeFeature({ naturalKey: 'ASP/BRUSSELS CTA EAST TWO', featureType: 'airspace', geometry: null }),
  makeFeature({ naturalKey: 'SUA/EBR04', featureType: 'specialUseArea', geometry: null })
];

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
