import L from 'leaflet';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FeatureType, FeatureTypeCount, GeoJsonFeature } from '../types';
import { LayerPanel, featureColour } from './LayerPanel';

const SELECTION_PANE = 'selection-pane';
const ACCENT_TOKEN = '--accent';

// Draw order inside the shared canvas renderer. Lines first, points last, so a
// point's 14px hit circle is never shadowed by a line's hit band. Aerodrome is
// last because it is the largest point feature.
const TYPE_DRAW_ORDER: FeatureType[] = [
  'runway',
  'route',
  'waypoint',
  'runwayDirection',
  'navaid',
  'aerodrome'
];

function drawPriority(type: FeatureType): number {
  const index = TYPE_DRAW_ORDER.indexOf(type);
  return index === -1 ? TYPE_DRAW_ORDER.length : index;
}

function cssToken(name: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value === '' ? 'transparent' : value;
}

function pointStyle(type: FeatureType, colour: string): L.CircleMarkerOptions {
  switch (type) {
    case 'aerodrome':
      return { radius: 7, color: colour, weight: 2.5, fillColor: colour, fillOpacity: 1 };
    case 'runwayDirection':
      return { radius: 4.5, color: colour, weight: 1.5, fillColor: colour, fillOpacity: 1 };
    case 'navaid':
      return { radius: 5.5, color: colour, weight: 2, fillOpacity: 0 };
    case 'waypoint':
      return { radius: 3.5, color: colour, weight: 0, fillColor: colour, fillOpacity: 0.9 };
    default:
      return { radius: 4, color: colour, weight: 1.5, fillColor: colour, fillOpacity: 1 };
  }
}

function lineOptions(type: FeatureType, colour: string): L.PolylineOptions {
  if (type === 'runway') {
    return { color: colour, weight: 5, opacity: 1 };
  }
  if (type === 'route') {
    return { color: colour, weight: 2, opacity: 0.8, dashArray: '6 4' };
  }
  return { color: colour, weight: 2, opacity: 0.8 };
}

interface FeatureLayers {
  visual: L.Layer;
  hit: L.Layer;
}

function createFeatureLayers(
  feature: GeoJsonFeature,
  onSelect: (naturalKey: string) => void
): FeatureLayers {
  const geometry = feature.geometry;
  if (geometry === null) {
    throw new Error(
      `Invariant violated: null geometry reached the map for ${feature.properties.naturalKey}`
    );
  }

  const { naturalKey, featureType } = feature.properties;
  const colour = featureColour(featureType);

  if (geometry.type === 'Point') {
    const [lon, lat] = geometry.coordinates;
    const latlng: [number, number] = [lat, lon];
    const visual = L.circleMarker(latlng, {
      ...pointStyle(featureType, colour),
      interactive: false
    });
    const hit = L.circleMarker(latlng, {
      radius: 14,
      stroke: false,
      fill: true,
      fillOpacity: 0,
      interactive: true
    });
    hit.on('click', () => {
      onSelect(naturalKey);
    });
    return { visual, hit };
  }

  const latLngs: [number, number][] = geometry.coordinates.map(([lon, lat]) => [lat, lon]);
  const visual = L.polyline(latLngs, {
    ...lineOptions(featureType, colour),
    interactive: false
  });
  const hit = L.polyline(latLngs, { weight: 14, opacity: 0, interactive: true });
  hit.on('click', () => {
    onSelect(naturalKey);
  });
  return { visual, hit };
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
  const mapRef = useRef<L.Map | null>(null);
  const groupsRef = useRef<Map<string, L.LayerGroup>>(new Map());
  const overlayRef = useRef<L.LayerGroup | null>(null);

  const [active, setActive] = useState<Set<string>>(() => new Set());

  const drawableTypes = useMemo(() => {
    const next = new Set<string>();
    for (const feature of drawable) next.add(feature.properties.featureType);
    return [...next];
  }, [drawable]);
  const typeSignature = drawableTypes.join('|');

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;

    const map = L.map(container, {
      preferCanvas: true,
      renderer: L.canvas({ tolerance: 8 })
    }).setView([50.7, 4.6], 8);

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19
    }).addTo(map);

    map.createPane(SELECTION_PANE);
    const selectionPane = map.getPane(SELECTION_PANE);
    if (selectionPane !== undefined) {
      selectionPane.style.zIndex = '450';
      selectionPane.style.pointerEvents = 'none';
    }

    overlayRef.current = L.layerGroup().addTo(map);
    groupsRef.current = new Map();
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
      groupsRef.current = new Map();
    };
  }, []);

  useEffect(() => {
    setActive(new Set(drawableTypes));
    // drawableTypes is derived from the same drawable array identity; keying on
    // the signature keeps this from resetting user toggles on every re-render.
  }, [typeSignature]);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;

    for (const group of groupsRef.current.values()) {
      map.removeLayer(group);
    }
    groupsRef.current = new Map();

    for (const feature of [...drawable].sort(
      (a, b) =>
        drawPriority(a.properties.featureType) - drawPriority(b.properties.featureType)
    )) {
      const type = feature.properties.featureType;
      let group = groupsRef.current.get(type);
      if (group === undefined) {
        group = L.layerGroup();
        groupsRef.current.set(type, group);
      }

      let layers: FeatureLayers;
      try {
        layers = createFeatureLayers(feature, onSelect);
      } catch (error) {
        console.error(error);
        continue;
      }
      layers.visual.addTo(group);
      layers.hit.addTo(group);
    }
  }, [drawable, onSelect]);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;

    for (const [type, group] of groupsRef.current) {
      const shouldShow = active.has(type);
      if (shouldShow && !map.hasLayer(group)) {
        group.addTo(map);
      } else if (!shouldShow && map.hasLayer(group)) {
        map.removeLayer(group);
      }
    }
  }, [active, drawable]);

  useEffect(() => {
    const overlay = overlayRef.current;
    if (overlay === null) return;

    overlay.clearLayers();
    if (selectedKey === null) return;

    const feature = drawable.find((item) => item.properties.naturalKey === selectedKey);
    if (feature === undefined || feature.geometry === null) return;

    const accent = cssToken(ACCENT_TOKEN);

    if (feature.geometry.type === 'Point') {
      const [lon, lat] = feature.geometry.coordinates;
      L.circleMarker([lat, lon], {
        radius: 22,
        color: accent,
        weight: 1.5,
        fillColor: accent,
        fillOpacity: 0.12,
        pane: SELECTION_PANE
      }).addTo(overlay);
      return;
    }

    const latLngs: [number, number][] = feature.geometry.coordinates.map(([lon, lat]) => [
      lat,
      lon
    ]);
    const base = lineOptions(feature.properties.featureType, accent);
    L.polyline(latLngs, {
      color: accent,
      weight: (base.weight ?? 1.5) + 1,
      opacity: 1,
      pane: SELECTION_PANE
    }).addTo(overlay);
  }, [selectedKey, drawable]);

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
