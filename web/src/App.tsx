import { useCallback, useMemo, useState } from 'react';
import { Inspector } from './components/Inspector';
import { MapView } from './components/MapView';
import { OverviewView } from './components/OverviewView';
import { REGISTER_ALL, RegisterView } from './components/RegisterView';
import { TopBar } from './components/TopBar';
import { useFeatures } from './hooks/useFeatures';
import type { GeoJsonFeature } from './types';

export type View = 'overview' | 'map' | 'airport' | 'register';

interface AirportViewProps {
  aerodromes: GeoJsonFeature[];
  selectedKey: string | null;
  onSelect: (naturalKey: string) => void;
}

function AirportView({ aerodromes, selectedKey, onSelect }: AirportViewProps) {
  return (
    <section className="airport" aria-label="Airport view">
      <div className="airport-rail">
        <div className="airport-rail-head t1">AERODROMES</div>
        {aerodromes.map((feature) => {
          const key = feature.properties.naturalKey;
          const selected = key === selectedKey;
          return (
            <button
              type="button"
              key={key}
              className={`airport-rail-row t3 mono${selected ? ' is-selected' : ''}`}
              aria-pressed={selected}
              onClick={() => onSelect(key)}
            >
              {key}
            </button>
          );
        })}
      </div>
      <div className="airport-panel">
        <p className="airport-placeholder t3">
          Airport operational summary — implemented in part 2.
        </p>
      </div>
    </section>
  );
}

interface ContentStateProps {
  title: string;
  detail: string;
  action?: { label: string; onClick: () => void };
}

function ContentState({ title, detail, action }: ContentStateProps) {
  return (
    <div className="content-state">
      <div className="content-state-box">
        <h2 className="t1">{title}</h2>
        <p className="t2">{detail}</p>
        {action === undefined ? null : (
          <button type="button" className="retry-button t3" onClick={action.onClick}>
            {action.label}
          </button>
        )}
      </div>
    </div>
  );
}

export function App() {
  const { status, types, features, drawable, publication, error, reload } = useFeatures();
  const [view, setView] = useState<View>('overview');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [registerFilter, setRegisterFilter] = useState<string>(REGISTER_ALL);

  const selected = useMemo(() => {
    if (selectedKey === null) return null;
    return features.find((feature) => feature.properties.naturalKey === selectedKey) ?? null;
  }, [features, selectedKey]);

  const aerodromes = useMemo(
    () =>
      features
        .filter((feature) => feature.properties.featureType === 'aerodrome')
        .sort((a, b) => a.properties.naturalKey.localeCompare(b.properties.naturalKey)),
    [features]
  );

  const handleSelect = useCallback((naturalKey: string) => {
    setSelectedKey(naturalKey);
  }, []);

  const handleSelectAerodrome = useCallback((naturalKey: string) => {
    setSelectedKey(naturalKey);
    setView('airport');
  }, []);

  const registerCount = useMemo(
    () => features.filter((feature) => feature.geometry === null).length,
    [features]
  );

  const handleShowRegister = useCallback((featureType: string) => {
    setRegisterFilter(featureType);
    setView('register');
  }, []);

  return (
    <div className="app">
      <TopBar
        view={view}
        onViewChange={setView}
        publication={publication}
        registerCount={registerCount}
      />

      <main className="workspace">
        <section className="content">
          {status === 'loading' ? (
            <ContentState
              title="LOADING eAIP DATA"
              detail="Fetching feature inventory, geometry and publication cycle."
            />
          ) : null}

          {status === 'error' ? (
            <ContentState
              title="DATA UNAVAILABLE"
              detail={error ?? 'Unknown error while loading data.'}
              action={{ label: 'Retry', onClick: reload }}
            />
          ) : null}

          {status === 'ready' && view === 'overview' ? (
            <OverviewView
              publication={publication}
              types={types}
              features={features}
              aerodromes={aerodromes}
              onSelectAerodrome={handleSelectAerodrome}
            />
          ) : null}

          {status === 'ready' && view === 'map' ? (
            <MapView
              types={types}
              features={features}
              drawable={drawable}
              selectedKey={selectedKey}
              onSelect={handleSelect}
              onShowRegister={handleShowRegister}
            />
          ) : null}

          {status === 'ready' && view === 'airport' ? (
            <AirportView
              aerodromes={aerodromes}
              selectedKey={selectedKey}
              onSelect={handleSelect}
            />
          ) : null}

          {status === 'ready' && view === 'register' ? (
            <RegisterView
              features={features}
              filter={registerFilter}
              onFilterChange={setRegisterFilter}
              selectedKey={selectedKey}
              onSelect={handleSelect}
            />
          ) : null}
        </section>

        <Inspector feature={selected} loading={status === 'loading'} />
      </main>
    </div>
  );
}
