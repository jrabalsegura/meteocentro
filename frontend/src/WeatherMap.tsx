import { useEffect, useRef, useState } from "react";
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
  number,
  type Station,
} from "./data";
import { layoutMarkers } from "./mapMarkers";

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
  onView,
  onSelect,
}: {
  stations: Station[];
  metric: string;
  selected: string | null;
  initialView: View | null;
  onView: (view: View) => void;
  onSelect: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<LibreMap | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const groupPopup = useRef<maplibregl.Popup | null>(null);
  const focusedMarker = useRef<string | null>(null);
  const [base, setBase] = useState<"topo" | "light">("topo");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [revision, setRevision] = useState(0);
  const callbacks = useRef({ onView, onSelect });
  callbacks.current = { onView, onSelect };
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
      instance.on("error", () =>
        setError(
          "El fondo cartográfico no está disponible. Puedes consultar los números y la tabla.",
        ),
      );
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
      });
      const timeout = window.setTimeout(() => {
        if (!instance.areTilesLoaded())
          setError(
            "La cartografía está tardando. Los datos y la tabla siguen disponibles.",
          );
      }, 10000);
      return () => {
        clearTimeout(timeout);
        resize.disconnect();
        instance.remove();
        map.current = null;
      };
    } catch {
      setError(
        "Este navegador no puede dibujar el mapa. Consulta la tabla de estaciones.",
      );
      return;
    }
  }, []);
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
  function changeBase(next: "topo" | "light") {
    setBase(next);
    setError("");
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
      </div>
      {error && (
        <div className="map-error" role="status">
          {error}{" "}
          <button onClick={() => changeBase(base)}>Reintentar fondo</button>
        </div>
      )}

    </section>
  );
}
