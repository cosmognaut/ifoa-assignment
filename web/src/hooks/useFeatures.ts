import { useCallback, useEffect, useState } from 'react';
import { getFeatures, getFeatureTypes, getPublications } from '../api';
import type {
  FeatureTypeCount,
  GeoJsonFeature,
  Publication
} from '../types';

export type LoadStatus = 'loading' | 'ready' | 'error';

export interface FeaturesState {
  status: LoadStatus;
  types: FeatureTypeCount[];
  features: GeoJsonFeature[];
  drawable: GeoJsonFeature[];
  publication: Publication | null;
  error: string | null;
}

const INITIAL_STATE: FeaturesState = {
  status: 'loading',
  types: [],
  features: [],
  drawable: [],
  publication: null,
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
      getPublications()
    ])
      .then(([types, drawable, all, publications]) => {
        if (cancelled) return;
        setState({
          status: 'ready',
          types,
          features: all.features,
          drawable: drawable.features,
          publication: publications[0] ?? null,
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
