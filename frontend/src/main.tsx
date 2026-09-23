import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  age,
  api,
  color,
  date,
  freshnessLabels,
  metricInfo,
  metricNames,
  number,
  period,
  providerLabels,
  provinces,
  type Current,
  type MapPage,
  type Provider,
  type Reading,
} from "./data";
import type { View } from "./WeatherMap";
import "./style.css";
import { Access } from "./Access";
import StationSummary from "./StationSummary";
const AdminPage = lazy(() => import("./AdminPage"));
const HistoryPage = lazy(() => import("./HistoryPage"));
const WeatherMap = lazy(() => import("./WeatherMap"));

function readLocation() {
  return {
    path: location.pathname,
    params: new URLSearchParams(location.search),
  };
}
function viewFrom(params: URLSearchParams): View | null {
  if (!params.has("view")) return null;
  const values = params.get("view")!.split(",").map(Number);
  if (values.length !== 3 || !values.every(Number.isFinite)) return null;
  const [lng, lat, zoom] = values;
  return lng >= -180 &&
    lng <= 180 &&
    lat >= -85 &&
    lat <= 85 &&
    zoom >= 5 &&
    zoom <= 18
    ? { lng, lat, zoom }
    : null;
}
function App() {
  const [route, setRoute] = useState(readLocation);
  const [result, setResult] = useState<{ query: string; data: MapPage } | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [detail, setDetail] = useState<Current | null>(null);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [busy, setBusy] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [sort, setSort] = useState({ key: "name", descending: false });
  const [page, setPage] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(
    !window.matchMedia("(max-width: 700px)").matches,
  );
  const heading = useRef<HTMLHeadingElement>(null);
  const loadedSelection = useRef<string | null>(null);
  const focusedSelection = useRef("");
  const initialView = useRef(viewFrom(route.params));
  const metric = metricInfo[route.params.get("metric") ?? ""]
    ? route.params.get("metric")!
    : "temperature";
  const info = metricInfo[metric];
  const isHistory = route.path === "/historicos" || route.path === "/diarios";
  const isTable = route.path === "/estaciones";
  const detailId = route.path.startsWith("/estaciones/")
    ? route.path.split("/")[2]
    : null;
  const selectedId = detailId ?? route.params.get("station");
  const chosenSource = route.params.get("source") ?? "";
  const filters = new URLSearchParams({ metric, limit: "2000" });
  for (const key of ["province", "network", "freshness", "q"]) {
    for (const value of route.params.getAll(key))
      if (value) filters.append(key, value);
  }
  const query = filters.toString();
  // Never show a previous filter population while its replacement is loading.
  const data = result?.query === query ? result.data : null;
  function navigate(
    path: string,
    changes: Record<string, string | null> = {},
    replace = false,
  ) {
    const params = new URLSearchParams(location.search);
    Object.entries(changes).forEach(([key, value]) => {
      if (value) params.set(key, value);
      else params.delete(key);
    });
    history[replace ? "replaceState" : "pushState"](
      {},
      "",
      `${path}${params.size ? "?" + params : ""}`,
    );
    setRoute(readLocation());
  }
  function setView(view: View) {
    initialView.current = view;
    const params = new URLSearchParams(location.search);
    params.set(
      "view",
      `${view.lng.toFixed(5)},${view.lat.toFixed(5)},${view.zoom.toFixed(2)}`,
    );
    history.replaceState({}, "", `${location.pathname}?${params}`);
  }
  function closeSelection() {
    navigate(
      detailId
        ? route.params.get("return") === "table"
          ? "/estaciones"
          : "/"
        : route.path,
      { station: null, source: null },
    );
    requestAnimationFrame(() => {
      const input = document.getElementById("station-search");
      if (input?.offsetParent) input.focus();
      else
        document.querySelector<HTMLElement>(".filter-panel summary")?.focus();
    });
  }
  useEffect(() => {
    const pop = () => setRoute(readLocation());
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  useEffect(() => {
    const tick = () => {
      if (!document.hidden) setRefresh((n) => n + 1);
    };
    const timer = window.setInterval(tick, 60000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    if (document.hidden) return () => controller.abort();
    setBusy(true);
    setError("");
    const timer = window.setTimeout(() => {
      void Promise.all([
        api<MapPage>(`/api/v1/map?${query}`, controller.signal),
        api<{ items: Provider[] }>("/api/v1/providers", controller.signal),
      ])
        .then(([next, networks]) => {
          if (controller.signal.aborted) return;
          setResult({ query, data: next });
          setProviders(networks.items.filter((p) => p.code !== "wunderground"));
          setBusy(false);
        })
        .catch((error) => {
          if (!controller.signal.aborted) {
            setResult(null);
            setBusy(false);
            setError(error.message);
          }
        });
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, refresh]);
  useEffect(() => {
    setPage(0);
  }, [query]);
  useEffect(() => {
    if (loadedSelection.current !== selectedId) setDetail(null);
    loadedSelection.current = selectedId;
    setDetailError("");
    if (!selectedId || document.hidden) return;
    const controller = new AbortController();
    void api<Current>(
      `/api/v1/stations/${encodeURIComponent(selectedId)}/current`,
      controller.signal,
    )
      .then((next) => {
        if (!controller.signal.aborted) setDetail(next);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setDetail(null);
          setDetailError(error.message);
        }
      });
    return () => controller.abort();
  }, [selectedId, refresh]);
  useEffect(() => {
    const selection = selectedId ? `${selectedId}:${!!detailId}` : "";
    if (!selection) focusedSelection.current = "";
    if (
      selection &&
      (detail || detailError) &&
      focusedSelection.current !== selection
    ) {
      heading.current?.focus();
      focusedSelection.current = selection;
    }
  }, [selectedId, detailId, !!detail, detailError]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && selectedId) closeSelection();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  });
  const stations = data?.items ?? [];
  const sorted = useMemo(
    () =>
      [...stations].sort((a, b) => {
        const av =
          sort.key === "name"
            ? a.name
            : sort.key === "value"
              ? (a.reading?.value ?? null)
              : (a.reading?.observed_at ?? null);
        const bv =
          sort.key === "name"
            ? b.name
            : sort.key === "value"
              ? (b.reading?.value ?? null)
              : (b.reading?.observed_at ?? null);
        if (av == null) return bv == null ? 0 : 1;
        if (bv == null) return -1;
        const order =
          typeof av === "number" && typeof bv === "number"
            ? av - bv
            : String(av).localeCompare(String(bv), "es");
        return (sort.descending ? -order : order) || a.id.localeCompare(b.id);
      }),
    [stations, sort],
  );
  const effectiveSource = detail?.sources.some((s) => s.id === chosenSource)
    ? chosenSource
    : "";
  const sourceUnavailable = !!detail && !!chosenSource && !effectiveSource;
  const preferred = detail?.readings.find(
    (r) =>
      r.metric === metric &&
      (!effectiveSource || r.source_id === effectiveSource),
  );
  const detailReadings =
    detail?.readings.filter(
      (r) => !effectiveSource || r.source_id === effectiveSource,
    ) ?? [];
  const metricSet = [...new Set(detailReadings.map((r) => r.metric))];
  const currentMetrics = metricSet
    .filter((m) => !m.includes("daily"))
    .sort((a, b) =>
      a === metric ? -1 : b === metric ? 1 : a.localeCompare(b),
    );
  const dailyMetrics = metricSet.filter((m) => m.includes("daily"));
  const choose = (id: string, scrollToMap = false) => {
    navigate(isTable ? "/" : route.path, {
      station: id,
      source: stations.find((s) => s.id === id)?.reading?.source_id ?? null,
    });
    if (scrollToMap)
      requestAnimationFrame(() =>
        document
          .querySelector(".map-workspace")
          ?.scrollIntoView({ block: "start" }),
      );
  };
  const openStation = (id: string) =>
    navigate(`/estaciones/${id}`, {
      station: id,
      source:
        selectedId === id
          ? chosenSource
          : (stations.find((s) => s.id === id)?.reading?.source_id ?? null),
      return: isTable ? "table" : "map",
    });
  const backPath = route.params.get("return") === "table" ? "/estaciones" : "/";
  const tabHref = (path: string) =>
    `${path}?${new URLSearchParams(location.search)}`;
  const readingCard = (name: string) => {
    const reading = detailReadings.find((r) => r.metric === name)!;
    return (
      <article className="reading-card" key={name}>
        <h3>{metricNames[name] ?? name}</h3>
        <div
          className="reading-number"
          style={{ color: color(reading.value, name) }}
        >
          {number(reading.value)} <span>{reading.unit}</span>
        </div>
        <ReadingMeta reading={reading} />
      </article>
    );
  };
  return (
    <>
      <a className="skip-link" href="#content">
        Saltar a las observaciones
      </a>
      <header className="site-header">
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            navigate("/");
          }}
        >
          <span className="brand-icon" aria-hidden="true">
            m<span>°</span>
          </span>
          <span>
            Meteocentro<small>OBSERVATORIO DE LA ZONA CENTRO</small>
          </span>
        </a>
        <nav aria-label="Principal">
          <a
            className={!isTable && !isHistory ? "active" : ""}
            href={tabHref("/")}
            onClick={(e) => {
              e.preventDefault();
              navigate("/");
            }}
          >
            Mapa
          </a>
          <a
            className={isTable ? "active" : ""}
            href={tabHref("/estaciones")}
            onClick={(e) => {
              e.preventDefault();
              navigate("/estaciones");
            }}
          >
            Estaciones
          </a>
          {[
            ["/diarios", "Datos diarios"],
            ["/historicos", "Históricos"],
          ].map(([path, label]) => (
            <a
              key={path}
              className={route.path === path ? "active" : ""}
              href={tabHref(path)}
              onClick={(e) => {
                e.preventDefault();
                navigate(path);
              }}
            >
              {label}
            </a>
          ))}
        </nav>
        <a href="/gestion">Gestión privada</a>
        <div className="header-region">
          Madrid · Ávila
          <br />
          Segovia · Guadalajara
        </div>
      </header>
      <main id="content">
        {isHistory ? (
          <Suspense fallback={<p>Abriendo archivo…</p>}>
            <HistoryPage
              stations={stations}
              stationId={selectedId}
              networkView={route.path === "/diarios"}
              onChoose={(id) => navigate("/historicos", { station: id })}
              refresh={refresh}
            />
          </Suspense>
        ) : (
          <>
        <section className="workspace-heading">
          <div>
            <div className="eyebrow">RED DE OBSERVACIÓN</div>
            <h1>
              {isTable
                ? "Estaciones de la zona centro"
                : "El tiempo, estación a estación"}
            </h1>
          </div>
          <div className="refresh-status" role="status">
            {busy ? (
              "Consultando observaciones…"
            ) : error ? (
              "Sin conexión con la API"
            ) : (
              <>
                Consulta: {date(data?.generated_at)}
                <small>Refresco cada 60 s · horas de Madrid</small>
              </>
            )}
          </div>
        </section>
        <section className="controls" aria-label="Variables y filtros">
          <div className="variables">
            {Object.entries(metricInfo)
              .slice(0, 4)
              .map(([key, value]) => (
                <button
                  key={key}
                  className={metric === key ? "active" : ""}
                  aria-pressed={metric === key}
                  onClick={() => navigate(route.path, { metric: key })}
                >
                  <span aria-hidden="true">
                    {key === "temperature"
                      ? "°"
                      : key === "humidity"
                        ? "◒"
                        : key === "wind_speed"
                          ? "↗"
                          : "≋"}
                  </span>
                  {value.short}
                  <small>{value.unit}</small>
                </button>
              ))}
          </div>
          <details
            className="filter-panel"
            open={filtersOpen}
            onToggle={(event) => setFiltersOpen(event.currentTarget.open)}
          >
            <summary>
              Filtros y búsqueda <span>⌄</span>
            </summary>
            <div className="filter-grid">
              <label className="search-label">
                Buscar estación
                <input
                  id="station-search"
                  type="search"
                  placeholder="Nombre, municipio o ID…"
                  value={route.params.get("q") ?? ""}
                  onChange={(e) =>
                    navigate(route.path, { q: e.target.value }, true)
                  }
                />
              </label>
              <label>
                Provincia
                <select
                  aria-label="Provincia"
                  value={route.params.get("province") ?? ""}
                  onChange={(e) =>
                    navigate(route.path, { province: e.target.value })
                  }
                >
                  <option value="">Las cuatro provincias</option>
                  {Object.entries(provinces).map(([code, name]) => (
                    <option key={code} value={code}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Fuente
                <select
                  aria-label="Fuente"
                  value={route.params.get("network") ?? ""}
                  onChange={(e) =>
                    navigate(route.path, { network: e.target.value })
                  }
                >
                  <option value="">Todas las fuentes</option>
                  <option value="aemet">AEMET</option>
                  <option value="meteoclimatic">Meteoclimatic</option>
                </select>
              </label>
              <label>
                Estado
                <select
                  aria-label="Estado"
                  value={route.params.get("freshness") ?? "all"}
                  onChange={(e) =>
                    navigate(route.path, { freshness: e.target.value })
                  }
                >
                  <option value="all">Todos los estados</option>
                  {Object.entries(freshnessLabels).map(([code, name]) => (
                    <option value={code} key={code}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </details>
        </section>
        {error && (
          <div className="notice error" role="alert">
            {error}{" "}
            <button onClick={() => setRefresh((n) => n + 1)}>
              Reintentar consulta
            </button>
          </div>
        )}
        {data?.truncated && (
          <div className="notice" role="status">
            Se muestran {stations.length} de {data.total} estaciones como
            mínimo. Acota los filtros; los extremos están desactivados para
            esta selección parcial.
          </div>
        )}
        {providers
          .filter((p) => p.status !== "verified")
          .map((p) => (
            <div className="provider-notice" key={p.code}>
              {p.name}: {providerLabels[p.status] ?? p.status}.{" "}
              {p.status === "paused"
                ? "Se conserva el archivo; comprueba la hora de cada observación."
                : "Esta fuente no aporta estaciones a la consulta."}
            </div>
          ))}
        <div className="summary-strip" aria-label="Resumen de la selección">
          <span>
            <strong>{data?.registered ?? "—"}</strong> registradas
          </span>
          <span>
            <strong>{data?.counts.fresh ?? (data ? 0 : "—")}</strong>{" "}
            recientes
          </span>
          <span>
            <strong>{data?.counts.stale ?? (data ? 0 : "—")}</strong>{" "}
            desactualizadas
          </span>
          <span>
            <strong>
              {data
                ? (data.counts.unknown ?? 0) +
                  (data.counts.historical_only ?? 0)
                : "—"}
            </strong>{" "}
            sin dato actual
          </span>
          <small>
            Según variable y filtros · {stations.length} visibles
          </small>
        </div>
        {!isTable && (
          <div
            className={`map-workspace ${selectedId ? "has-selection" : ""}`}
          >
            <Suspense
              fallback={
                <div className="map-loading">
                  Preparando mapa… La tabla está disponible debajo.
                </div>
              }
            >
              <WeatherMap
                stations={stations}
                metric={metric}
                selected={selectedId}
                initialView={initialView.current}
                onView={setView}
                onSelect={choose}
              />
            </Suspense>
            <div className="map-caption">
              <span>{info.label}</span>
              <strong>{info.unit}</strong>
              <span>{info.period}</span>
            </div>
            {!busy && !error && !stations.length && (
              <div className="empty-map">
                No hay estaciones que cumplan estos filtros.
                <button
                  onClick={() =>
                    navigate("/", {
                      q: null,
                      province: null,
                      network: null,
                      freshness: null,
                    })
                  }
                >
                  Restablecer filtros
                </button>
              </div>
            )}
            {selectedId && (
              <aside
                className={`station-panel ${detailId ? "full-detail" : ""}`}
                aria-label="Ficha de estación"
              >
                <div className="panel-top">
                  <span>
                    {detailId
                      ? "FICHA DE ESTACIÓN"
                      : "ESTACIÓN SELECCIONADA"}
                  </span>
                  <button
                    aria-label="Cerrar ficha"
                    onClick={closeSelection}
                  >
                    ×
                  </button>
                </div>
                <h2 ref={heading} tabIndex={-1}>
                  {detail?.name ??
                    (detailError
                      ? "Estación no disponible"
                      : "Consultando estación…")}
                </h2>
                {detailError && <p role="alert">{detailError}</p>}
                {detail && (
                  <>
                    <p className="station-location">
                      {provinces[detail.province_code]} ·{" "}
                      {detail.altitude_m == null
                        ? "Altitud no disponible"
                        : `${number(detail.altitude_m)} m de altitud`}
                    </p>
                    {!detailId && (
                      <div className="station-current">
                        <div>
                          <h3>{info.label}</h3>
                          <small>{preferred ? `${preferred.provider.toUpperCase()} · ${date(preferred.observed_at)}` : "Sin observación"}</small>
                          {preferred?.freshness === "stale" && <small>Desactualizada</small>}
                        </div>
                        <div className="popup-value" style={{ color: color(preferred?.value, metric) }}>
                          {number(preferred?.value)} <span>{preferred?.unit ?? info.unit}</span>
                        </div>
                      </div>
                    )}
                    <StationSummary station={detail} source={effectiveSource} />
                    <label className="source-select">
                      Fuente del dato
                      <select
                        aria-label="Fuente del dato"
                        value={effectiveSource}
                        onChange={(e) =>
                          navigate(route.path, { source: e.target.value })
                        }
                      >
                        <option value="">Automática por variable</option>
                        {detail.sources.map((s) => (
                          <option value={s.id} key={s.id}>
                            {s.provider.toUpperCase()} · {s.external_id}
                          </option>
                        ))}
                      </select>
                    </label>
                    {sourceUnavailable && (
                      <p className="provider-notice" role="status">
                        El origen seleccionado ya no está disponible. Se
                        muestran los otros orígenes elegibles.
                      </p>
                    )}
                    {!detailId && (
                      <>
                        <details className="current-metadata">
                          <summary>Detalle de la observación</summary>
                          {preferred ? <ReadingMeta reading={preferred} /> : <p>Sin dato de esta variable en la fuente seleccionada.</p>}
                        </details>
                        <button
                          className="primary-button"
                          onClick={() => openStation(detail.id)}
                        >
                          Ver ficha completa <span>↗</span>
                        </button>
                      </>
                    )}
                    {detailId && (
                      <>
                        <div className="reading-grid">
                          {currentMetrics.map(readingCard)}
                        </div>
                        {!currentMetrics.length && (
                          <p>No hay observaciones actuales disponibles.</p>
                        )}
                        {dailyMetrics.length > 0 && (
                          <details className="reported-daily">
                            <summary>Valores diarios reportados</summary>
                            <p>
                              Horario diario desconocido. No son resúmenes
                              de un día civil validado.
                            </p>
                            {dailyMetrics.map(readingCard)}
                          </details>
                        )}
                        <button
                          className="primary-button"
                          onClick={() =>
                            navigate("/historicos", { station: detail.id })
                          }
                        >
                          Explorar históricos y gráficos ↗
                        </button>
                      </>
                    )}
                    <details className="station-metadata">
                      <summary>Ubicación y procedencia</summary>
                      <p>
                        Municipio:{" "}
                        {detail.municipality ??
                          "no consta por separado en el catálogo"}
                      </p>
                      <p>
                        {detail.latitude != null && detail.longitude != null
                          ? `${detail.latitude.toFixed(4)}° N, ${detail.longitude.toFixed(4)}° E`
                          : "Coordenadas no disponibles"}
                      </p>
                      {detail.sources.map((s) => (
                        <p key={s.id}>
                          {s.source_url ? (
                            <a
                              href={s.source_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {s.attribution} · {s.external_id} ↗
                            </a>
                          ) : (
                            `${s.attribution} · ${s.external_id}`
                          )}
                          <br />
                          {s.coordinate_precision === "minute"
                            ? "Posición aproximada a minutos."
                            : ""}{" "}
                          {providerLabels[s.provider_status] ??
                            s.provider_status}
                        </p>
                      ))}
                    </details>
                    {detailId && (
                      <a
                        className="back-link"
                        href={tabHref(backPath)}
                        onClick={(e) => {
                          e.preventDefault();
                          navigate(backPath);
                        }}
                      >
                        ← Volver a{" "}
                        {backPath === "/" ? "mapa" : "estaciones"} con los
                        filtros
                      </a>
                    )}
                  </>
                )}
              </aside>
            )}
          </div>
        )}
        <section className="legend" aria-label="Leyenda">
          <div>
            <strong>
              {info.label} <span>({info.unit})</span>
            </strong>
            <small>{info.period}</small>
          </div>
          <div className="color-scale">
            {info.scale.map((value, i) => (
              <span key={value}>
                <i style={{ background: info.colors[i] }} />
                {i === 0
                  ? "< " + info.scale[1]
                  : i === info.scale.length - 1
                    ? "≥ " + value
                    : value + "–" + info.scale[i + 1]}
              </span>
            ))}
          </div>
          <p>
            — Sin dato · Borde discontinuo: desactualizada.
            <br />
            El número y la hora acompañan siempre al color.
          </p>
        </section>
        {metric === "wind_speed" && (
          <p className="context-note">
            La dirección indica de dónde viene el viento: 0° norte, 90°
            este; la flecha apunta hacia su destino. La máxima diaria y la
            racha no se usan como velocidad actual.
          </p>
        )}
        {metric === "rain" && (
          <p className="context-note">
            Meteoclimatic aporta un contador diario de horario desconocido,
            disponible en su ficha. No se representa como lluvia horaria. La
            intensidad y la racha del intervalo aún no están disponibles en
            las fuentes integradas.
          </p>
        )}
        <section className="extremes" aria-label="Extremos comparables">
          <div>
            <h2>Extremos de la selección</h2>
            <p>
              {data?.extremes.eligible ?? 0} estaciones con dato reciente ·{" "}
              {data?.extremes.comparable
                ? "misma unidad y periodo"
                : data?.extremes.reason === "different_periods"
                  ? "Periodos diferentes: no se comparan."
                  : "Sin cobertura comparable suficiente."}
            </p>
            <small>
              {data?.extremes.comparable &&
                `Observaciones: ${date(data.extremes.observed_from)} — ${date(data.extremes.observed_to)}. No son récords diarios.`}
            </small>
          </div>
          {data?.extremes.comparable &&
            [data.extremes.minimum, data.extremes.maximum].map(
              (extreme, i) =>
                extreme && (
                  <button
                    key={i}
                    onClick={() => openStation(extreme.station_id)}
                  >
                    <small>{i === 0 ? "MENOR VALOR" : "MAYOR VALOR"}</small>
                    <strong
                      style={{
                        color: color(extreme.reading.value, metric),
                      }}
                    >
                      {number(extreme.reading.value)}{" "}
                      <span>{extreme.reading.unit}</span>
                    </strong>
                    <span>{extreme.name} ↗</span>
                  </button>
                ),
            )}
        </section>
        <section className="station-table" aria-label="Tabla de estaciones">
          <div className="table-heading">
            <div>
              <h2>
                {isTable
                  ? "Todas las estaciones seleccionadas"
                  : "Las estaciones, al detalle"}
              </h2>
              <p>
                La misma selección del mapa · {stations.length} estaciones
              </p>
            </div>
            <a
              href={tabHref(isTable ? "/" : "/estaciones")}
              onClick={(e) => {
                e.preventDefault();
                navigate(isTable ? "/" : "/estaciones");
              }}
            >
              {isTable ? "Ver en el mapa ↗" : "Abrir tabla ↗"}
            </a>
          </div>
          <div className="table-scroll">
            <table>
              <caption className="sr-only">
                Observaciones de {info.label}. Las cabeceras permiten
                ordenar. Las filas seleccionan una estación en el mapa.
              </caption>
              <thead>
                <tr>
                  {[
                    ["name", "Estación"],
                    ["value", `${info.short} (${info.unit})`],
                    ["time", "Observación · Madrid"],
                  ].map(([key, name]) => (
                    <th
                      key={key}
                      aria-sort={
                        sort.key === key
                          ? sort.descending
                            ? "descending"
                            : "ascending"
                          : "none"
                      }
                    >
                      <button
                        onClick={() => {
                          setSort({
                            key,
                            descending:
                              sort.key === key
                                ? !sort.descending
                                : key !== "name",
                          });
                          setPage(0);
                        }}
                      >
                        {name}{" "}
                        {sort.key === key
                          ? sort.descending
                            ? "↓"
                            : "↑"
                          : "↕"}
                      </button>
                    </th>
                  ))}
                  <th>Fuente / periodo</th>
                  <th>Estado</th>
                </tr>
              </thead>
              <tbody>
                {sorted.slice(page * 50, page * 50 + 50).map((station) => (
                  <tr
                    key={station.id}
                    className={
                      selectedId === station.id ? "selected-row" : ""
                    }
                  >
                    <td>
                      <button
                        className="station-name"
                        onClick={() => {
                          choose(station.id, true);
                        }}
                      >
                        {station.name}
                      </button>
                      <small>
                        {provinces[station.province_code]} ·{" "}
                        {station.altitude_m == null
                          ? "Altitud s/d"
                          : `${number(station.altitude_m)} m`}
                      </small>
                      <a
                        href={`/estaciones/${station.id}?${new URLSearchParams(location.search)}`}
                        onClick={(e) => {
                          e.preventDefault();
                          openStation(station.id);
                        }}
                      >
                        Ficha ↗
                      </a>
                    </td>
                    <td>
                      <strong
                        className="table-value"
                        style={{
                          color: color(station.reading?.value, metric),
                        }}
                      >
                        {number(station.reading?.value)}
                      </strong>
                    </td>
                    <td>
                      {date(station.reading?.observed_at)}
                      <small>
                        {station.reading
                          ? `Hace ${age(station.reading)}`
                          : "Sin medida utilizable"}
                      </small>
                    </td>
                    <td>
                      {station.reading?.provider.toUpperCase() ??
                        station.sources
                          .map((s) => s.provider.toUpperCase())
                          .join(" · ")}
                      {station.fallback && (
                        <small>Origen alternativo</small>
                      )}
                      <small>
                        {station.reading
                          ? period(station.reading)
                          : "Sin periodo"}
                      </small>
                    </td>
                    <td>
                      <span className={`status-tag ${station.freshness}`}>
                        {freshnessLabels[station.freshness]}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!stations.length && (
            <p className="table-empty">
              {busy
                ? "Consultando…"
                : error
                  ? "No hay datos visibles mientras falla la consulta."
                  : "No hay estaciones para esta selección."}
            </p>
          )}
          <div className="pagination">
            <span>
              {stations.length
                ? `${page * 50 + 1}–${Math.min((page + 1) * 50, stations.length)} de ${stations.length}`
                : "0 estaciones"}
            </span>
            <button
              disabled={page === 0}
              onClick={() => setPage((n) => n - 1)}
            >
              Anterior
            </button>
            <button
              disabled={(page + 1) * 50 >= stations.length}
              onClick={() => setPage((n) => n + 1)}
            >
              Siguiente
            </button>
          </div>
        </section>
          </>
        )}
        <footer>
          <strong>Meteocentro</strong>
          <p>
            Observaciones, no pronósticos. Hora de observación en Europe/Madrid;
            la recogida no renueva la antigüedad.
          </p>
          <p>AEMET: reciente hasta 90 min · Meteoclimatic: hasta 45 min.</p>
          <div>
            {providers.map((p) => (
              <span key={p.code}>
                Datos: {p.attribution}{" "}
                {p.license_url && (
                  <a href={p.license_url} target="_blank" rel="noreferrer">
                    {p.code === "meteoclimatic"
                      ? "CC BY-NC-ND 3.0"
                      : "Condiciones de uso"}{" "}
                    ↗
                  </a>
                )}
              </span>
            ))}
          </div>
          <p>
            Archivo local privado. Coordenadas Meteoclimatic aproximadas a
            minutos.
          </p>
        </footer>
      </main>
    </>
  );
}
function ReadingMeta({ reading }: { reading: Reading }) {
  return (
    <div className="reading-meta">
      <span className={`status-tag ${reading.freshness}`}>
        {freshnessLabels[reading.freshness]}
      </span>
      <p>
        <strong>{date(reading.observed_at)}</strong> · hace {age(reading)}
      </p>
      <p>{period(reading)}</p>
      {reading.direction_degrees != null && (
        <p>
          <span
            className="wind-arrow"
            style={{
              transform: `rotate(${reading.direction_degrees + 180}deg)`,
            }}
            aria-hidden="true"
          >
            ↑
          </span>{" "}
          Procede de {number(reading.direction_degrees)}°
        </p>
      )}
      <p className="source-line">
        {reading.provider.toUpperCase()} · {reading.external_id}
      </p>
      <details>
        <summary>Detalle de la medida</summary>
        <p>
          Recogida: {date(reading.fetched_at)}
          <br />
          Producto: {reading.product}
          <br />
          Hora original UTC: {reading.observed_at}
          <br />
          Umbral de frescura: {reading.stale_after_seconds / 60} min
        </p>
        {reading.flags.length > 0 && (
          <p>Dato no utilizable: {reading.flags.join(", ")}</p>
        )}
      </details>
    </div>
  );
}
const management = location.pathname.startsWith("/gestion");
createRoot(document.getElementById("root")!).render(
  <Access management={management}>
    {session => management
      ? <Suspense fallback={<p>Cargando gestión…</p>}><AdminPage session={session} /></Suspense>
      : <App />}
  </Access>,
);
