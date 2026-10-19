import * as maplibregl from 'maplibre-gl';
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { ErrorEvent, GeoJSONSource, MapMouseEvent } from 'maplibre-gl';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FeatureType, FeatureTypeCount, GeoJsonFeature } from '../types';
import { LayerPanel } from './LayerPanel';

const STYLE_BASE = 'https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json';
const SOURCE_ID = 'features';

maplibregl.setWorkerUrl(maplibreWorkerUrl);

const TYPE_LAYER_IDS: Record<string, string> = {
  route: 'route-line',
  runway: 'runway-line',
  runwayDirection: 'runwaydir-point',
  waypoint: 'waypoint-point',
  navaid: 'navaid-point',
  aerodrome: 'aerodrome-point'
};

const SELECTION_LAYERS = ['selection-point', 'selection-line'];

const CLICKABLE_LAYERS = [
  'aerodrome-point',
  'navaid-point',
  'waypoint-point',
  'runwaydir-point',
  'runway-line',
  'route-line'
];

type MapFilter = Parameters<maplibregl.Map['setFilter']>[1];
type MapLayer = Parameters<maplibregl.Map['addLayer']>[0];

function styleUrl(): string {
  const key = import.meta.env.VITE_CARTO_API_KEY;
  if (typeof key !== 'string' || key.trim() === '') {
    return STYLE_BASE;
  }
  return `${STYLE_BASE}?api_key=${encodeURIComponent(key.trim())}`;
}

const STYLE_URL = styleUrl();

interface MapFeatureProperties {
  naturalKey: string;
  featureType: FeatureType;
  parentKey: string | null;
  effectiveFrom: string;
}

interface MapFeature {
  type: 'Feature';
  geometry: NonNullable<GeoJsonFeature['geometry']>;
  properties: MapFeatureProperties;
}

const FEATURE_LAYERS: MapLayer[] = [
  {
    id: 'route-line',
    type: 'line',
    source: SOURCE_ID,
    filter: ['==', ['get', 'featureType'], 'route'],
    paint: {
      'line-color': '#38BDF8',
      'line-width': 2,
      'line-dasharray': [3, 2],
      'line-opacity': 0.8
    }
  },
  {
    id: 'runway-line',
    type: 'line',
    source: SOURCE_ID,
    filter: ['==', ['get', 'featureType'], 'runway'],
    paint: {
      'line-color': '#FFFFFF',
      'line-width': 5
    }
  },
  {
    id: 'runwaydir-point',
    type: 'circle',
    source: SOURCE_ID,
    filter: ['==', ['get', 'featureType'], 'runwayDirection'],
    paint: {
      'circle-radius': 4.5,
      'circle-color': '#E5E5E5',
      'circle-stroke-color': '#000000',
      'circle-stroke-width': 1.5
    }
  },
  {
    id: 'waypoint-point',
    type: 'circle',
    source: SOURCE_ID,
    filter: ['==', ['get', 'featureType'], 'waypoint'],
    paint: {
      'circle-radius': 3.5,
      'circle-color': '#C084FC',
      'circle-stroke-width': 0
    }
  },
  {
    id: 'navaid-point',
    type: 'circle',
    source: SOURCE_ID,
    filter: ['==', ['get', 'featureType'], 'navaid'],
    paint: {
      'circle-radius': 5.5,
      'circle-color': 'rgba(0, 0, 0, 0)',
      'circle-stroke-color': '#22D3EE',
      'circle-stroke-width': 2
    }
  },
  {
    id: 'aerodrome-point',
    type: 'circle',
    source: SOURCE_ID,
    filter: ['==', ['get', 'featureType'], 'aerodrome'],
    paint: {
      'circle-radius': 7,
      'circle-color': '#C8FF00',
      'circle-stroke-color': '#000000',
      'circle-stroke-width': 2
    }
  },
  {
    id: 'selection-point',
    type: 'circle',
    source: SOURCE_ID,
    filter: ['==', ['get', 'naturalKey'], ''],
    paint: {
      'circle-radius': 15,
      'circle-color': 'rgba(0, 0, 0, 0)',
      'circle-stroke-color': '#C8FF00',
      'circle-stroke-width': 2
    }
  },
  {
    id: 'selection-line',
    type: 'line',
    source: SOURCE_ID,
    filter: ['==', ['get', 'naturalKey'], ''],
    paint: {
      'line-color': '#C8FF00',
      'line-width': 7,
      'line-opacity': 0.35
    }
  }
];

function toMapFeature(feature: GeoJsonFeature): MapFeature {
  if (feature.geometry === null) {
    throw new Error(
      `Invariant violated: null geometry reached the map for ${feature.properties.naturalKey}`
    );
  }
  return {
    type: 'Feature',
    geometry: feature.geometry,
    properties: {
      naturalKey: feature.properties.naturalKey,
      featureType: feature.properties.featureType,
      parentKey: feature.properties.parentKey,
      effectiveFrom: feature.properties.effectiveFrom
    }
  };
}

function buildFeatureCollection(features: GeoJsonFeature[]): {
  type: 'FeatureCollection';
  features: MapFeature[];
} {
  const mapFeatures: MapFeature[] = [];
  for (const feature of features) {
    try {
      mapFeatures.push(toMapFeature(feature));
    } catch (error) {
      console.error(error);
    }
  }
  return { type: 'FeatureCollection', features: mapFeatures };
}

function setTypeVisibility(map: maplibregl.Map, active: Set<string>): void {
  for (const [type, layerId] of Object.entries(TYPE_LAYER_IDS)) {
    if (map.getLayer(layerId) === undefined) continue;
    map.setLayoutProperty(layerId, 'visibility', active.has(type) ? 'visible' : 'none');
  }
}

function setSelectionFilter(map: maplibregl.Map, selectedKey: string | null): void {
  const filter: MapFilter = ['==', ['get', 'naturalKey'], selectedKey ?? ''];
  for (const layerId of SELECTION_LAYERS) {
    if (map.getLayer(layerId) === undefined) continue;
    map.setFilter(layerId, filter);
  }
}

export interface MapViewProps {
  types: FeatureTypeCount[];
  features: GeoJsonFeature[];
  drawable: GeoJsonFeature[];
  selectedKey: string | null;
  onSelect: (naturalKey: string) => void;
  onShowRegister: (featureType: string) => void;
}

export function MapView({
  types,
  features,
  drawable,
  selectedKey,
  onSelect,
  onShowRegister
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const drawableRef = useRef(drawable);
  const activeRef = useRef<Set<string>>(new Set());
  const selectedKeyRef = useRef<string | null>(selectedKey);
  const onSelectRef = useRef(onSelect);

  const [mapReady, setMapReady] = useState(false);
  const [active, setActive] = useState<Set<string>>(() => new Set());

  const drawableTypes = useMemo(() => {
    const next = new Set<string>();
    for (const feature of drawable) next.add(feature.properties.featureType);
    return [...next];
  }, [drawable]);
  const typeSignature = drawableTypes.join('|');

  useEffect(() => {
    drawableRef.current = drawable;
  }, [drawable]);

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  useEffect(() => {
    selectedKeyRef.current = selectedKey;
  }, [selectedKey]);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    setActive(new Set(drawableTypes));
    // drawableTypes is derived from the same drawable array identity; keying on
    // the signature keeps this from resetting user toggles on every re-render.
  }, [typeSignature]);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;

    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
        container,
        style: STYLE_URL,
        center: [4.6, 50.7],
        zoom: 8,
        maxZoom: 18,
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false
      });
    } catch (error) {
      console.error('Failed to initialise map', error);
      return;
    }

    mapRef.current = map;

    map.addControl(
      new maplibregl.AttributionControl({
        compact: true,
        customAttribution: '© OpenStreetMap © CARTO'
      }),
      'bottom-right'
    );
    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      'bottom-right'
    );

    const handleLoad = () => {
      try {
        map.addSource(SOURCE_ID, {
          type: 'geojson',
          data: buildFeatureCollection(drawableRef.current)
        });
        for (const layer of FEATURE_LAYERS) {
          map.addLayer(layer);
        }
        setTypeVisibility(map, activeRef.current);
        setSelectionFilter(map, selectedKeyRef.current);
        setMapReady(true);
      } catch (error) {
        console.error('Failed to add feature layers', error);
      }
    };

    const handleError = (event: ErrorEvent) => {
      console.error('MapLibre error', event.error);
    };

    const handleClick = (event: MapMouseEvent) => {
      const box: [[number, number], [number, number]] = [
        [event.point.x - 8, event.point.y - 8],
        [event.point.x + 8, event.point.y + 8]
      ];
      const hits = map.queryRenderedFeatures(box, { layers: CLICKABLE_LAYERS });
      const naturalKey = hits[0]?.properties?.naturalKey;
      if (typeof naturalKey === 'string' && naturalKey !== '') {
        onSelectRef.current(naturalKey);
      }
    };

    const handleMouseMove = (event: MapMouseEvent) => {
      const box: [[number, number], [number, number]] = [
        [event.point.x - 8, event.point.y - 8],
        [event.point.x + 8, event.point.y + 8]
      ];
      const hits = map.queryRenderedFeatures(box, { layers: CLICKABLE_LAYERS });
      map.getCanvas().style.cursor = hits.length > 0 ? 'pointer' : '';
    };

    map.on('load', handleLoad);
    map.on('error', handleError);
    map.on('click', handleClick);
    map.on('mousemove', handleMouseMove);

    const observer = new ResizeObserver(() => {
      map.resize();
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      map.off('load', handleLoad);
      map.off('error', handleError);
      map.off('click', handleClick);
      map.off('mousemove', handleMouseMove);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null || !mapReady) return;
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    if (source === undefined) return;
    source.setData(buildFeatureCollection(drawable));
  }, [drawable, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null || !mapReady) return;
    setTypeVisibility(map, active);
  }, [active, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null || !mapReady) return;
    setSelectionFilter(map, selectedKey);
  }, [selectedKey, mapReady]);

  const handleToggle = useCallback((type: string) => {
    setActive((previous) => {
      const next = new Set(previous);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  }, []);

  return (
    <div className="map-view">
      <div className="map-canvas" ref={containerRef} />
      <LayerPanel
        types={types}
        features={features}
        drawable={drawable}
        active={active}
        onToggle={handleToggle}
        onShowRegister={onShowRegister}
      />
    </div>
  );
}
