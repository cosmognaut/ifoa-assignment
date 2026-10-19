import { useCallback, useEffect, useState } from 'react';
import { getFeatures, getFeatureTypes, getIssues, getPublications } from '../api';
import type {
  FeatureTypeCount,
  GeoJsonFeature,
  Publication,
  ValidationIssue
} from '../types';

export type LoadStatus = 'loading' | 'ready' | 'error';

export interface FeaturesState {
  status: LoadStatus;
  types: FeatureTypeCount[];
  features: GeoJsonFeature[];
  drawable: GeoJsonFeature[];
  publication: Publication | null;
  issues: ValidationIssue[];
  error: string | null;
}

const INITIAL_STATE: FeaturesState = {
  status: 'loading',
  types: [],
  features: [],
  drawable: [],
  publication: null,
  issues: [],
  error: null
};

export interface UseFeaturesResult extends FeaturesState {
  reload: () => void;
}

export function useFeatures(): UseFeaturesResult {
  const [state, setState] = useState<FeaturesState>(INITIAL_STATE);
  const [requestId, setRequestId] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState((previous) => ({
      ...previous,
      status: 'loading',
      error: null
    }));

    Promise.all([
      getFeatureTypes(),
      getFeatures(true),
      getFeatures(false),
      getPublications(),
      getIssues()
    ])
      .then(([types, drawable, all, publications, issues]) => {
        if (cancelled) return;
        setState({
          status: 'ready',
          types,
          features: all.features,
          drawable: drawable.features,
          publication: publications[0] ?? null,
          issues,
          error: null
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState((previous) => ({
          ...previous,
          status: 'error',
          error: error instanceof Error ? error.message : 'Unknown error while loading data'
        }));
      });

    return () => {
      cancelled = true;
    };
  }, [requestId]);

  const reload = useCallback(() => {
    setRequestId((value) => value + 1);
  }, []);

  return { ...state, reload };
}
