import L from 'leaflet';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FeatureType, FeatureTypeCount, GeoJsonFeature } from '../types';
import { LayerPanel, featureColour } from './LayerPanel';

const SELECTION_PANE = 'selection-pane';
const BG_TOKEN = '--bg';
const ACCENT_TOKEN = '--accent';

function cssToken(name: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value === '' ? 'transparent' : value;
}

function pointRadius(type: FeatureType): number {
  switch (type) {
    case 'aerodrome':
      return 7;
    case 'navaid':
      return 4;
    case 'waypoint':
      return 2.2;
    case 'runwayDirection':
      return 3;
    default:
      return 4;
  }
}

function lineOptions(type: FeatureType, colour: string): L.PolylineOptions {
  if (type === 'runway') {
    return { color: colour, weight: 4, opacity: 0.9 };
  }
  if (type === 'route') {
    return { color: colour, weight: 1.5, opacity: 0.55, dashArray: '6 4' };
  }
  return { color: colour, weight: 1.5, opacity: 0.7 };
}

function createFeatureLayer(
  feature: GeoJsonFeature,
  onSelect: (naturalKey: string) => void
): L.Layer {
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
    const marker = L.circleMarker([lat, lon], {
      radius: pointRadius(featureType),
      color: colour,
      weight: featureType === 'aerodrome' ? 2 : 1.5,
      fillColor: cssToken(BG_TOKEN),
      fillOpacity: 1
    });
    marker.on('click', () => {
      onSelect(naturalKey);
    });
    return marker;
  }

  const latLngs: [number, number][] = geometry.coordinates.map(([lon, lat]) => [lat, lon]);
  const line = L.polyline(latLngs, lineOptions(featureType, colour));
  line.on('click', () => {
    onSelect(naturalKey);
  });
  return line;
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

    const map = L.map(container, { preferCanvas: true }).setView([50.7, 4.6], 8);

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

    for (const feature of drawable) {
      const type = feature.properties.featureType;
      let group = groupsRef.current.get(type);
      if (group === undefined) {
        group = L.layerGroup();
        groupsRef.current.set(type, group);
      }

      let layer: L.Layer;
      try {
        layer = createFeatureLayer(feature, onSelect);
      } catch (error) {
        console.error(error);
        continue;
      }
      layer.addTo(group);
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
