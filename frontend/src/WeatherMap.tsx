import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import { type Map as LibreMap, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import boundariesText from "../../config/provinces.geojson?raw";
const boundaries = JSON.parse(boundariesText) as {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    properties: Record<string, string>;
    geometry: { type: "MultiPolygon"; coordinates: number[][][][] };
  }[];
};
import {
  color,
  date,
  freshnessLabels,
  isRecentMapReading,
  metricInfo,
  number,
  type Station,
} from "./data";
import { layoutMarkers } from "./mapMarkers";
import {
  fieldRamps,
  rampGradient,
  renderField,
  type Extent,
} from "./interpolation";

const wmts = (service: string, layer: string) =>
  `https://www.ign.es/wmts/${service}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=${layer}&STYLE=default&FORMAT=image/jpeg&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}`;
const tiles = {
  topo: import.meta.env.VITE_TOPO_TILES_URL || wmts("mapa-raster", "MTN"),
  light:
    import.meta.env.VITE_LIGHT_TILES_URL || wmts("ign-base", "IGNBaseTodo"),
};
export type View = { lng: number; lat: number; zoom: number };
// User-selected Madrid city view. Explicit URL views take precedence.
const DEFAULT_VIEW: View = { lng: -3.70765, lat: 40.42437, zoom: 10.69 };
const coords = boundaries.features.flatMap(
  (f) => f.geometry.coordinates.flat(2) as unknown as number[][],
);
// Full province extent for the explicit overview button.
const bounds = coords.reduce(
  (b, p) => b.extend(p as [number, number]),
  new maplibregl.LngLatBounds(),
);
// Fixed raster extent for the interpolated field: the four provinces plus a margin.
const FIELD_EXTENT: Extent = {
  west: bounds.getWest() - 0.05,
  south: bounds.getSouth() - 0.05,
  east: bounds.getEast() + 0.05,
  north: bounds.getNorth() + 0.05,
};
const FIELD_WIDTH = 720;
const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const FIELD_HEIGHT = Math.round(
  (FIELD_WIDTH * (mercY(FIELD_EXTENT.north) - mercY(FIELD_EXTENT.south)) * 180) /
    Math.PI /
    (FIELD_EXTENT.east - FIELD_EXTENT.west),
);
const FIELD_CORNERS: [[number, number], [number, number], [number, number], [number, number]] = [
  [FIELD_EXTENT.west, FIELD_EXTENT.north],
  [FIELD_EXTENT.east, FIELD_EXTENT.north],
  [FIELD_EXTENT.east, FIELD_EXTENT.south],
  [FIELD_EXTENT.west, FIELD_EXTENT.south],
];
// Paint the field and keep it only inside the four provinces.
function fieldImage(stations: Station[], metric: string): string | null {
  const ramp = fieldRamps[metric];
  if (!ramp) return null;
  const points = stations.flatMap((s) =>
    s.latitude != null && s.longitude != null && isRecentMapReading(s.reading)
      ? [{ lng: s.longitude, lat: s.latitude, value: s.reading!.value! }]
      : [],
  );
  const pixels = renderField(points, ramp, FIELD_EXTENT, FIELD_WIDTH, FIELD_HEIGHT);
  const canvas = document.createElement("canvas");
  canvas.width = FIELD_WIDTH;
  canvas.height = FIELD_HEIGHT;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.putImageData(new ImageData(pixels, FIELD_WIDTH, FIELD_HEIGHT), 0, 0);
  const top = mercY(FIELD_EXTENT.north), bottom = mercY(FIELD_EXTENT.south);
  ctx.globalCompositeOperation = "destination-in";
  ctx.beginPath();
  for (const feature of boundaries.features)
    for (const polygon of feature.geometry.coordinates)
      for (const ring of polygon)
        ring.forEach(([lng, lat], i) => {
          const x = ((lng - FIELD_EXTENT.west) / (FIELD_EXTENT.east - FIELD_EXTENT.west)) * FIELD_WIDTH;
          const y = ((top - mercY(lat)) / (top - bottom)) * FIELD_HEIGHT;
          if (i) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
        });
  ctx.fill("evenodd");
  return canvas.toDataURL("image/png");
}
function style(base: "topo" | "light"): StyleSpecification {
  return {
    version: 8,
    sources: {
      base: {
        type: "raster",
        tiles: [tiles[base]],
        tileSize: 256,
        maxzoom: 18,
        attribution:
          '© <a href="https://www.ign.es/">IGN</a> · <a href="https://www.scne.es/">SCNE</a>',
      },
      provinces: { type: "geojson", data: boundaries },
    },
    layers: [
      {
        id: "background",
        type: "background",
        paint: { "background-color": "#edf0f3" },
      },
      {
        id: "base",
        type: "raster",
        source: "base",
        paint: {
          "raster-opacity": base === "topo" ? 0.86 : 0.7,
          "raster-saturation": -0.35,
        },
      },
      {
        id: "province-fill",
        type: "fill",
        source: "provinces",
        paint: { "fill-color": "#6570b7", "fill-opacity": 0.035 },
      },
      {
        id: "province-lines",
        type: "line",
        source: "provinces",
        paint: {
          "line-color": "#66649b",
          "line-width": 1.4,
          "line-dasharray": [4, 3],
        },
      },
    ],
  };
}
export default function WeatherMap({
  stations,
  metric,
  selected,
  initialView,
  searchFocus,
  onSearchFocus,
  onView,
  onSelect,
  field = false,
  onField,
}: {
  stations: Station[];
  metric: string;
  selected: string | null;
  initialView: View | null;
  searchFocus?: View | null;
  onSearchFocus?: () => void;
  onView: (view: View) => void;
  onSelect: (id: string) => void;
  field?: boolean;
  onField?: (on: boolean) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<LibreMap | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const groupPopup = useRef<maplibregl.Popup | null>(null);
  const focusedMarker = useRef<string | null>(null);
  const [base, setBase] = useState<"topo" | "light">("topo");
  const [unsupported, setUnsupported] = useState(false);
  const [ready, setReady] = useState(false);
  const [revision, setRevision] = useState(0);
  const [styleRevision, setStyleRevision] = useState(0);
  const callbacks = useRef({ onView, onSelect, onSearchFocus });
  callbacks.current = { onView, onSelect, onSearchFocus };
  const firstView = useRef(initialView ?? DEFAULT_VIEW);
  useEffect(() => {
    let instance: LibreMap;
    try {
      instance = new maplibregl.Map({
        container: container.current!,
        style: style("topo"),
        locale: {
          "Map.Title": "Mapa de observaciones",
          "NavigationControl.ZoomIn": "Acercar",
          "NavigationControl.ZoomOut": "Alejar",
          "FullscreenControl.Enter": "Pantalla completa",
          "FullscreenControl.Exit": "Salir de pantalla completa",
          "Popup.Close": "Cerrar grupo",
          "AttributionControl.ToggleAttribution": "Mostrar atribución",
        },
        center: [firstView.current.lng, firstView.current.lat],
        zoom: firstView.current.zoom,
        minZoom: 5,
        maxZoom: 18,
        attributionControl: false,
      });
      map.current = instance;
      instance.addControl(
        new maplibregl.NavigationControl({ showCompass: false }),
        "top-right",
      );
      instance.addControl(new maplibregl.FullscreenControl(), "top-right");
      instance.addControl(
        new maplibregl.AttributionControl({ compact: false }),
        "bottom-right",
      );
      instance.on("load", () => {
        setReady(true);
        setRevision((n) => n + 1);
      });
      instance.on("moveend", () => {
        const center = instance.getCenter();
        callbacks.current.onView({
          lng: center.lng,
          lat: center.lat,
          zoom: instance.getZoom(),
        });
        setRevision((n) => n + 1);
      });
      const resize = new ResizeObserver(() => {
        instance.resize();
        setRevision((n) => n + 1);
      });
      resize.observe(container.current!);
      // A slow raster must not hold numeric observations hostage.
      instance.on("styledata", () => {
        setReady(true);
        // A base-map change replaces the style; the field must be re-added.
        if (!instance.getSource("field")) setStyleRevision((n) => n + 1);
      });
      return () => {
        resize.disconnect();
        instance.remove();
        map.current = null;
      };
    } catch {
      setUnsupported(true);
      return;
    }
  }, []);
  useEffect(() => {
    if (!map.current || !ready || !searchFocus) return;
    map.current.easeTo({
      center: [searchFocus.lng, searchFocus.lat],
      zoom: searchFocus.zoom,
      duration: 450,
    });
    callbacks.current.onSearchFocus?.();
  }, [searchFocus, ready]);
  useEffect(() => () => {
    groupPopup.current?.remove();
    groupPopup.current = null;
  }, [stations, metric, selected]);
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready) return;
    markers.current.forEach((marker) => marker.remove());
    markers.current = [];
    const points = stations.flatMap((station) => {
      if (station.latitude == null || station.longitude == null) return [];
      const { x, y } = instance.project([station.longitude, station.latitude]);
      return [{ station, x, y }];
    });
    const groups = layoutMarkers(points, instance.getContainer().clientWidth, instance.getContainer().clientHeight);
    for (const { x, y, items } of groups) {
      const group = items.map((item) => item.station);
      const station = group[0];
      const coincident = group.every((item) => item.latitude === station.latitude && item.longitude === station.longitude);
      const showList = coincident || instance.getZoom() >= 17;
      const offset: [number, number] = [x - items[0].x, y - items[0].y];
      const displaced = Math.hypot(...offset) > 1;
      const button = document.createElement("button");
      button.dataset.mapFocusId = group.map((item) => item.id).join(",");
      button.className = `map-number ${group.length > 1 ? "cluster" : station.freshness} ${group.some((item) => item.id === selected) ? "selected" : ""}`;
      button.style.color = color(station.reading?.value, metric);
      button.textContent =
        group.length > 1
          ? `${showList ? "Ver " : ""}${group.length} est.`
          : number(station.reading?.value);
      button.setAttribute(
        "aria-label",
        group.length > 1
          ? `${showList ? "Ver" : "Ampliar grupo de"} ${group.length} estaciones`
          : `${station.name}: ${number(station.reading?.value)} ${station.reading?.unit ?? ""}, ${freshnessLabels[station.freshness]}. Abrir resumen`,
      );
      button.title = group.length > 1
        ? button.getAttribute("aria-label")!
        : `${station.name}${displaced ? ". Marcador separado visualmente; la línea señala la ubicación publicada." : ""}`;
      button.addEventListener("click", (event) => {
        // Do not let the opening click reach the map and immediately close the popup.
        event.stopPropagation();
        if (group.length === 1) {
          callbacks.current.onSelect(station.id);
          return;
        }
        if (showList) {
          groupPopup.current?.remove();
          const list = document.createElement("div");
          list.className = "group-list";
          const explanation = document.createElement("p");
          explanation.textContent = coincident
            ? "Estas estaciones comparten coordenadas publicadas. La coincidencia de ubicación no implica que sean duplicadas."
            : "Las estaciones están muy próximas en el mapa. Selecciona una para ver su resumen.";
          list.append(explanation);
          for (const item of group) {
            const choice = document.createElement("button");
            const name = document.createElement("strong");
            name.textContent = `${item.name} · ${number(item.reading?.value)} ${item.reading?.unit ?? ""}`;
            const origin = document.createElement("span");
            origin.textContent = item.sources.map((source) => `${source.provider.toUpperCase()} · ${source.external_id}`).join(" / ");
            const observed = document.createElement("span");
            observed.textContent = date(item.reading?.observed_at);
            choice.append(name, origin, observed);
            choice.onclick = () => {
              popup.remove();
              callbacks.current.onSelect(item.id);
            };
            list.append(choice);
          }
          const popup = new maplibregl.Popup({ maxWidth: "280px", closeOnMove: true })
            .setLngLat([station.longitude!, station.latitude!])
            .setDOMContent(list)
            .addTo(instance);
          groupPopup.current = popup;
        } else {
          const box = new maplibregl.LngLatBounds();
          group.forEach((s) => box.extend([s.longitude!, s.latitude!]));
          instance.fitBounds(box, {
            padding: 90,
            maxZoom: Math.min(18, instance.getZoom() + 2),
            duration: 350,
          });
        }
      });
      if (displaced) {
        const leader = document.createElement("div");
        leader.className = "map-marker-leader";
        leader.setAttribute("aria-hidden", "true");
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        const line = document.createElementNS(svg.namespaceURI, "line");
        line.setAttribute("x2", String(offset[0]));
        line.setAttribute("y2", String(offset[1]));
        const dot = document.createElementNS(svg.namespaceURI, "circle");
        dot.setAttribute("r", "3");
        svg.append(line, dot);
        leader.append(svg);
        markers.current.push(new maplibregl.Marker({ element: leader })
          .setLngLat([station.longitude!, station.latitude!]).addTo(instance));
      }
      markers.current.push(
        new maplibregl.Marker({ element: button })
          .setLngLat([station.longitude!, station.latitude!])
          .setOffset(offset)
          .addTo(instance),
      );
      if (button.dataset.mapFocusId === focusedMarker.current && document.activeElement === document.body)
        button.focus({ preventScroll: true });
    }
    focusedMarker.current = null;
    return () => {
      focusedMarker.current = document.activeElement instanceof HTMLElement
        ? document.activeElement.dataset.mapFocusId ?? null : null;
      markers.current.forEach((marker) => marker.remove());
      markers.current = [];
    };
  }, [stations, metric, selected, ready, revision]);
  const fieldRamp = fieldRamps[metric];
  const showField = field && !!fieldRamp;
  const image = useMemo(
    () => (showField ? fieldImage(stations, metric) : null),
    [showField, stations, metric],
  );
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready) return;
    const remove = () => {
      if (instance.getLayer("field")) instance.removeLayer("field");
      if (instance.getSource("field")) instance.removeSource("field");
    };
    if (!image) {
      remove();
      return;
    }
    const source = instance.getSource("field") as maplibregl.ImageSource | undefined;
    if (source) source.updateImage({ url: image, coordinates: FIELD_CORNERS });
    else {
      instance.addSource("field", { type: "image", url: image, coordinates: FIELD_CORNERS });
      instance.addLayer(
        {
          id: "field",
          type: "raster",
          source: "field",
          paint: { "raster-opacity": 0.62, "raster-resampling": "linear", "raster-fade-duration": 0 },
        },
        instance.getLayer("province-lines") ? "province-lines" : undefined,
      );
    }
  }, [image, ready, styleRevision]);
  function changeBase(next: "topo" | "light") {
    setBase(next);
    map.current?.setStyle(style(next));
  }
  return (
    <section className="map-shell" aria-label="Mapa de observaciones">
      <div className="map-canvas" ref={container} />
      <div className="map-toolbar">
        <div className="segmented">
          <button
            aria-pressed={base === "topo"}
            onClick={() => changeBase("topo")}
          >
            Topográfico
          </button>
          <button
            aria-pressed={base === "light"}
            onClick={() => changeBase("light")}
          >
            Claro
          </button>
        </div>
        <button
          className="region-button"
          onClick={() => map.current?.fitBounds(bounds, { padding: 38 })}
          aria-label="Ver las cuatro provincias"
        >
          ⌖ <span>Las 4 provincias</span>
        </button>
        {fieldRamp && onField && (
          <label className="field-toggle" title="Interpolación visual entre estaciones con dato de la última hora. No es una medición.">
            <input
              type="checkbox"
              checked={field}
              onChange={(e) => onField(e.target.checked)}
            />
            Capa de color
          </label>
        )}
      </div>
      {showField && (
        <div className="field-legend" aria-label={`Escala de color de ${metricInfo[metric]?.label ?? metric}`}>
          <div className="field-legend-bar" style={{ background: rampGradient(fieldRamp) }} />
          <div className="field-legend-labels">
            {fieldRamp.stops
              .filter((_, i, all) => i === 0 || i === all.length - 1 || i % 2 === 0)
              .map(([v]) => (
                <span key={v}>{v}</span>
              ))}
            <span>{fieldRamp.label}</span>
          </div>
          <small>Interpolación entre estaciones · orientativa</small>
        </div>
      )}
      {unsupported && (
        <div className="map-error" role="status">
          Este navegador no puede dibujar el mapa. Consulta la tabla de
          estaciones.
        </div>
      )}

    </section>
  );
}
