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
  freshnessLabels,
  metricInfo,
  number,
  type Station,
} from "./data";

const wmts = (service: string, layer: string) =>
  `https://www.ign.es/wmts/${service}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=${layer}&STYLE=default&FORMAT=image/jpeg&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}`;
const tiles = {
  topo: import.meta.env.VITE_TOPO_TILES_URL || wmts("mapa-raster", "MTN"),
  light:
    import.meta.env.VITE_LIGHT_TILES_URL || wmts("ign-base", "IGNBaseTodo"),
};
export type View = { lng: number; lat: number; zoom: number };
const coords = boundaries.features.flatMap(
  (f) => f.geometry.coordinates.flat(2) as unknown as number[][],
);
// The checked-in province union, not a capital or a geolocation request.
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
  const [base, setBase] = useState<"topo" | "light">("topo");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [revision, setRevision] = useState(0);
  const callbacks = useRef({ onView, onSelect });
  callbacks.current = { onView, onSelect };
  const firstView = useRef(initialView);
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
        center: firstView.current
          ? [firstView.current.lng, firstView.current.lat]
          : [-3.9, 40.7],
        zoom: firstView.current?.zoom ?? 7,
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
      if (!firstView.current)
        instance.fitBounds(bounds, { padding: 38, duration: 0 });
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
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready) return;
    markers.current.forEach((marker) => marker.remove());
    markers.current = [];
    const groups: { x: number; y: number; items: Station[] }[] = [];
    const grid = new Map<string, number[]>();
    for (const station of [...stations].sort(
      (a, b) => Number(b.id === selected) - Number(a.id === selected),
    )) {
      if (station.latitude == null || station.longitude == null) continue;
      const point = instance.project([station.longitude, station.latitude]);
      if (
        point.x < -40 ||
        point.y < -30 ||
        point.x > instance.getContainer().clientWidth + 40 ||
        point.y > instance.getContainer().clientHeight + 30
      )
        continue;
      const col = Math.floor(point.x / 66),
        row = Math.floor(point.y / 44);
      let neighbor: number | undefined;
      for (let x = col - 1; x <= col + 1; x++)
        for (let y = row - 1; y <= row + 1; y++) {
          for (const index of grid.get(`${x}:${y}`) ?? []) {
            if (
              Math.abs(groups[index].x - point.x) < 64 &&
              Math.abs(groups[index].y - point.y) < 38
            )
              neighbor = index;
          }
        }
      if (neighbor !== undefined) groups[neighbor].items.push(station);
      else {
        const key = `${col}:${row}`;
        const cell = grid.get(key) ?? [];
        cell.push(groups.length);
        grid.set(key, cell);
        groups.push({ x: point.x, y: point.y, items: [station] });
      }
    }
    for (const { items: group } of groups) {
      const station = group[0];
      const button = document.createElement("button");
      button.className = `map-number ${group.length > 1 ? "cluster" : station.freshness} ${station.id === selected ? "selected" : ""}`;
      button.style.color = color(station.reading?.value, metric);
      button.textContent =
        group.length > 1
          ? `${group.length} est.`
          : number(station.reading?.value);
      button.setAttribute(
        "aria-label",
        group.length > 1
          ? `Ampliar grupo de ${group.length} estaciones`
          : `${station.name}: ${number(station.reading?.value)} ${station.reading?.unit ?? ""}, ${freshnessLabels[station.freshness]}. Abrir resumen`,
      );
      button.addEventListener("click", () => {
        if (group.length === 1) {
          callbacks.current.onSelect(station.id);
          return;
        }
        if (instance.getZoom() >= 17) {
          const list = document.createElement("div");
          list.className = "group-list";
          for (const item of group) {
            const choice = document.createElement("button");
            choice.textContent = item.name;
            choice.onclick = () => {
              popup.remove();
              callbacks.current.onSelect(item.id);
            };
            list.append(choice);
          }
          const popup = new maplibregl.Popup({ maxWidth: "280px" })
            .setLngLat([station.longitude!, station.latitude!])
            .setDOMContent(list)
            .addTo(instance);
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
      markers.current.push(
        new maplibregl.Marker({ element: button })
          .setLngLat([station.longitude!, station.latitude!])
          .addTo(instance),
      );
    }
    return () => {
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
      <div className="map-scale" aria-label="Escala del mapa">
        <strong>
          {metricInfo[metric].short} · {metricInfo[metric].unit}
        </strong>
        <div>
          {metricInfo[metric].scale.map((value, i) => (
            <span key={value}>
              <i style={{ background: metricInfo[metric].colors[i] }} />
              {i === 0
                ? `< ${metricInfo[metric].scale[1]}`
                : i === 4
                  ? `≥ ${value}`
                  : `${value}–${metricInfo[metric].scale[i + 1]}`}
            </span>
          ))}
        </div>
      </div>
      <div className="map-note">
        Números: observaciones · «est.»: grupo, amplía para separar
      </div>
    </section>
  );
}
