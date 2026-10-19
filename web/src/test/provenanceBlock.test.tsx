import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProvenanceBlock } from '../components/ProvenanceBlock';
import { makeField } from './fixtures';

const field = makeField({
  value: 'TORA 3638',
  section: 'EBBR AD 2.13',
  effective_date: '2026-10-01',
  source_url:
    'https://ops.skeyes.be/html/belgocontrol_static/eaip/eAIP_Main/html/eAIP/EB-AD-2.EBBR-en-GB.html'
});

describe('ProvenanceBlock', () => {
  it('shows provenance keys as spaced labels, never as snake_case', () => {
    const { container } = render(<ProvenanceBlock field={field} />);
    const keys = [...container.querySelectorAll('.provenance-key')].map(
      (node) => node.textContent ?? ''
    );

    // `.t1` upper-cases visually; the DOM keeps the sentence-case label.
    expect(keys.some((key) => key.toLowerCase() === 'effective date')).toBe(true);
    expect(keys.every((key) => !key.includes('_'))).toBe(true);
    expect(screen.queryByText('EFFECTIVE_DATE')).toBeNull();
  });

  it('renders an http source url as a link', () => {
    render(<ProvenanceBlock field={field} />);
    const link = screen.getByRole('link');

    expect(link).toHaveAttribute('href', field.source_url);
  });
});
