import { render, screen } from '@testing-library/react';
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
    render(<Inspector feature={waypoint} loading={false} issues={[bboxIssue]} />);

    expect(screen.getByText('RULE_OPERATIONAL_BBOX')).toBeInTheDocument();
    expect(screen.getByText('warning')).toBeInTheDocument();
    expect(
      screen.getByText('Coordinates outside operational FIR bounding box: [6.391389, 48.658611]')
    ).toBeInTheDocument();
  });

  it('renders no validation block when the issue list is empty', () => {
    render(<Inspector feature={waypoint} loading={false} issues={[]} />);

    expect(screen.queryByText('RULE_OPERATIONAL_BBOX')).toBeNull();
    expect(screen.queryByText('validation')).toBeNull();
  });
});
