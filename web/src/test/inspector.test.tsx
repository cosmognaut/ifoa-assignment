import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Inspector } from '../components/Inspector';
import { makeCoordinateField, makeFeature, makeIssue } from './fixtures';

const waypoint = makeFeature({
  naturalKey: 'WPT/GIVOR',
  featureType: 'waypoint',
  geometry: { type: 'Point', coordinates: [6.391389, 48.658611] },
  fields: { coordinates: makeCoordinateField() }
});

const bboxIssue = makeIssue();

describe('Inspector validation issues', () => {
  it('shows the rule id, severity and message of a validation issue', () => {
    render(<Inspector feature={waypoint} loading={false} issues={[bboxIssue]} onClose={() => {}} />);

    expect(screen.getByText('RULE_OPERATIONAL_BBOX')).toBeInTheDocument();
    expect(screen.getByText('warning')).toBeInTheDocument();
    expect(
      screen.getByText('Coordinates outside operational FIR bounding box: [6.391389, 48.658611]')
    ).toBeInTheDocument();
  });

  it('renders no validation block when the issue list is empty', () => {
    render(<Inspector feature={waypoint} loading={false} issues={[]} onClose={() => {}} />);

    expect(screen.queryByText('RULE_OPERATIONAL_BBOX')).toBeNull();
    expect(screen.queryByText('validation')).toBeNull();
  });
});

describe('Inspector dismissal', () => {
  it('renders a close control that calls onClose when a feature is selected', () => {
    let closed = 0;
    render(
      <Inspector
        feature={waypoint}
        loading={false}
        issues={[]}
        onClose={() => {
          closed += 1;
        }}
      />
    );

    const close = screen.getByRole('button', { name: 'Close inspector' });
    fireEvent.click(close);

    expect(closed).toBe(1);
  });

  it('renders no close control for the empty sheet, which is already a bare bar', () => {
    render(<Inspector feature={null} loading={false} issues={[]} onClose={() => {}} />);

    expect(screen.queryByRole('button', { name: 'Close inspector' })).toBeNull();
    expect(screen.getByText(/Select any map feature/)).toBeInTheDocument();
  });
});
