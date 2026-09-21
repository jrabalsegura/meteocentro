import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  date,
  metricNames,
  historyMethod,
  historyPeriod,
  number,
  type Station,
  type Current,
} from "./data";
const WeatherMap = lazy(() => import("./WeatherMap"));
const HistoryChart = lazy(() => import("./HistoryChart"));
export type HistoricalPoint = {
  channel: string;
  product: string;
  time: string;
  period_start?: string;
  period_end?: string;
  period_basis?: string;
  aggregation_method: string;
  value?: number | null;
  minimum?: number | null;
  maximum?: number | null;
  mean?: number | null;
  total?: number | null;
  unit: string;
  coverage: number | null;
  partial?: boolean;
  provisional?: boolean;
  break_before?: boolean;
  flags?: string[];
  bucket?: string;
  fetched_at?: string;
  original?: Record<string, unknown>;
  quality?: Record<string, unknown>;
  provider?: string;
  external_id?: string;
  source_id?: string;
  latitude?: number;
  longitude?: number;
  first?: string;
  last?: string;
  station_id?: string;
  name?: string;
  eligible?: boolean;
};
type Availability = Record<
  "raw" | "hour" | "day",
  { first: string | null; last: string | null }
> & { pending_days: number };
type HistoricalPage = {
  items: HistoricalPoint[];
  next_offset: number | null;
  availability: Availability;
  export_allowed: boolean;
  coverage?: {
    channel: string;
    coverage: number;
    flags: string[];
    product: string;
  }[];
  events?: {
    time: string;
    kind: string;
    latitude: number;
    longitude: number;
  }[];
  provider: string;
  external_id: string;
  notice?: string;
};
type RecordItem = {
  unit: string;
  channel: string;
  aggregation_method: string;
  period_basis: string;
  first: string;
  last: string;
  days_available: number;
  minimum: {
    value: number;
    period_start: string;
    coverage: number | null;
  } | null;
  maximum: {
    value: number;
    period_start: string;
    coverage: number | null;
  } | null;
  total: {
    value: number;
    period_start: string;
    coverage: number | null;
  } | null;
};
const metrics = [
  "temperature",
  "humidity",
  "pressure_station",
  "pressure_sea_level",
  "wind_speed",
  "wind_direction",
  "wind_gust",
  "rain",
  "rain_daily",
  "rain_rate",
];
const labels: Record<string, string> = {
  series: "Evolución",
  daily: "Diarios",
  month: "Meses",
  year: "Años",
  records: "Efemérides",
};
function dayLabel(value: string | null | undefined) {
  return value
    ? new Intl.DateTimeFormat("es-ES", {
        timeZone: "Europe/Madrid",
        year: "numeric",
        month: "short",
        day: "numeric",
      }).format(new Date(value))
    : "Sin datos disponibles";
}
function converted(point: HistoricalPoint): HistoricalPoint {
  if (point.unit !== "m/s") return point;
  const result = { ...point, unit: "km/h" };
  for (const field of ["value", "minimum", "maximum", "mean", "total"] as const)
    if (result[field] != null) result[field] = result[field]! * 3.6;
  return result;
}
export default function HistoryPage({
  stations,
  stationId,
  networkView,
  onChoose,
  refresh,
}: {
  stations: Station[];
  stationId: string | null;
  networkView: boolean;
  onChoose: (id: string) => void;
  refresh: number;
}) {
  const lastStation = useRef(stationId);
  const [detail, setDetail] = useState<Current | null>(null);
  const [source, setSource] = useState("");
  const [metric, setMetric] = useState("temperature");
  const [mode, setMode] = useState("series");
  const [view, setView] = useState("chart");
  const [hours, setHours] = useState(24);
  const [method, setMethod] = useState("all");
  const [offset, setOffset] = useState(0);
  const [from, setFrom] = useState(() =>
    new Date(Date.now() - 86400000).toISOString().slice(0, 10),
  );
  const [to, setTo] = useState(() => new Date().toISOString().slice(0, 10));
  const [result, setResult] = useState<HistoricalPage | null>(null);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const [dailyStatistic, setDailyStatistic] = useState<"minimum" | "maximum">(
    "maximum",
  );
  const [networkDay, setNetworkDay] = useState(() =>
    new Date(Date.now() - 86400000).toISOString().slice(0, 10),
  );
  const [networkData, setNetworkData] = useState<{
    items: HistoricalPoint[];
    extremes: { field: string; population: number; extreme: HistoricalPoint }[];
    coverage_threshold: number;
  } | null>(null);
  const range = useMemo<[string, string]>(
    () =>
      hours
        ? [
            new Date(Date.now() - hours * 3600000).toISOString(),
            new Date().toISOString(),
          ]
        : [`${from}T00:00:00Z`, `${to}T00:00:00Z`],
    [hours, from, to, refresh, retry],
  );
  const days = (Date.parse(range[1]) - Date.parse(range[0])) / 86400000;
  const resolution = days > 31 ? "day" : days > 3 ? "hour" : "raw";
  useEffect(() => {
    setOffset(0);
  }, [stationId, source, metric, mode, hours, from, to, method]);
  useEffect(() => {
    if (lastStation.current !== stationId) {
      setDetail(null);
      setSource("");
      lastStation.current = stationId;
    }
    setResult(null);
    setError("");
    if (!stationId) return;
    const controller = new AbortController();
    api<Current>(`/api/v1/stations/${stationId}/current`, controller.signal)
      .then((d) => {
        setDetail(d);
        setSource((previous) => previous || (d.sources[0]?.id ?? ""));
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [stationId, refresh, retry]);
  const query = new URLSearchParams({
    from: range[0],
    to: range[1],
    metric,
    source,
    offset: String(offset),
    limit: mode === "series" ? "10000" : "500",
  });
  if (mode === "series") query.set("resolution", resolution);
  else {
    query.set(
      "resolution",
      mode === "month" ? "month" : mode === "year" ? "year" : "day",
    );
    query.set("method", method);
  }
  const queryString = query.toString();
  useEffect(() => {
    setResult(null);
    setRecords([]);
    if (!stationId || !source || networkView) return;
    if (
      days <= 0 ||
      days > (mode === "month" || mode === "year" ? 3660 : 366)
    ) {
      setError(
        "Elige un intervalo válido: hasta 366 días o 10 años para resúmenes.",
      );
      return;
    }
    const controller = new AbortController();
    setBusy(true);
    setError("");
    const endpoint =
      mode === "series" ? "series" : mode === "records" ? "records" : "daily";
    api<HistoricalPage & { items: RecordItem[] }>(
      `/api/v1/stations/${stationId}/${endpoint}?${mode === "records" ? new URLSearchParams({ source, metric }) : queryString}`,
      controller.signal,
    )
      .then((data) => {
        if (mode === "records") setRecords(data.items);
        else setResult({ ...data, items: data.items.map(converted) });
        setBusy(false);
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError(e.message);
          setBusy(false);
        }
      });
    return () => controller.abort();
  }, [stationId, source, queryString, mode, networkView, refresh, retry]);
  useEffect(() => {
    if (!networkView) return;
    const controller = new AbortController();
    setError("");
    setNetworkData(null);
    const m = ["temperature", "rain", "humidity", "wind_gust"].includes(metric)
      ? metric
      : "temperature";
    api<typeof networkData>(
      `/api/v1/daily?${new URLSearchParams({ day: networkDay, metric: m })}`,
      controller.signal,
    )
      .then(setNetworkData)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [networkView, networkDay, metric, refresh, retry]);
  const dailyMapStations: Station[] = (networkData?.items ?? [])
    .filter((p) => p.eligible && p.latitude != null && p.longitude != null)
    .map((raw) => {
      const p = converted(raw),
        base = stations.find((s) => s.id === p.station_id);
      const value = metric === "rain" ? p.total : p[dailyStatistic];
      return {
        id: p.source_id ?? p.station_id!,
        name: p.name!,
        latitude: Number(p.latitude),
        longitude: Number(p.longitude),
        municipality: null,
        province_code: base?.province_code ?? "28",
        altitude_m: base?.altitude_m ?? null,
        sources: base?.sources ?? [],
        freshness: "fresh",
        fallback: false,
        reading:
          value == null
            ? null
            : {
                metric,
                value,
                unit: p.unit,
                kind: "daily_summary",
                period_start: p.period_start ?? null,
                period_end: p.period_end ?? null,
                period_basis: p.period_basis ?? null,
                observed_at: p.period_end!,
                fetched_at: p.fetched_at!,
                age_seconds: 0,
                stale_after_seconds: 0,
                freshness: "fresh",
                source_id: p.source_id!,
                provider: p.provider!,
                external_id: p.external_id!,
                product: p.product,
                direction_degrees: null,
                flags: p.flags ?? [],
              },
      };
    });
  return (
    <section className="history-page">
      <div className="workspace-heading">
        <div>
          <div className="eyebrow">ARCHIVO METEOROLÓGICO</div>
          <h1>
            {networkView
              ? "Datos diarios de la red"
              : (detail?.name ?? "Históricos por estación")}
          </h1>
          <p>La evolución del tiempo, con su origen y su cobertura.</p>
        </div>
        <a href="/">← Volver al mapa</a>
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}{" "}
          <button onClick={() => setRetry((n) => n + 1)}>Reintentar</button>
        </div>
      )}
      {networkView ? (
        <>
          <div className="history-controls">
            <label>
              Día civil de Madrid
              <input
                type="date"
                value={networkDay}
                onChange={(e) => setNetworkDay(e.target.value)}
              />
            </label>
            <label>
              Variable diaria
              <select
                value={metric}
                onChange={(e) => setMetric(e.target.value)}
              >
                {["temperature", "rain", "humidity", "wind_gust"].map((m) => (
                  <option key={m} value={m}>
                    {metricNames[m]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p>
            Solo periodos civiles comparables. Cobertura mínima para extremos:{" "}
            {Math.round((networkData?.coverage_threshold ?? 0.9) * 100)} %. El
            día en curso usa un corte horario común.
          </p>
          {networkData && dailyMapStations.length > 0 && (
            <>
              <div className="history-periods">
                {metric !== "rain" &&
                  (["minimum", "maximum"] as const).map((stat) => (
                    <button
                      key={stat}
                      aria-pressed={dailyStatistic === stat}
                      onClick={() => setDailyStatistic(stat)}
                    >
                      {stat === "minimum"
                        ? "Mínimas diarias"
                        : "Máximas diarias"}
                    </button>
                  ))}
              </div>
              <div className="daily-map">
                <Suspense fallback={<p>Preparando mapa diario…</p>}>
                  <WeatherMap
                    stations={dailyMapStations}
                    metric={metric}
                    selected={null}
                    initialView={null}
                    onView={() => {}}
                    onSelect={(id) => {
                      const p = networkData.items.find(
                        (p) => (p.source_id ?? p.station_id) === id,
                      );
                      if (p?.station_id) onChoose(p.station_id);
                    }}
                  />
                </Suspense>
              </div>
              <p>
                Mapa de{" "}
                {metric === "rain"
                  ? "totales"
                  : dailyStatistic === "minimum"
                    ? "mínimas"
                    : "máximas"}{" "}
                del día seleccionado. Solo orígenes con cobertura suficiente;
                los grupos comparables figuran por separado.
              </p>
            </>
          )}
          <div className="history-extremes">
            {networkData?.extremes.map((e, i) => (
              <article key={i}>
                <span>
                  {e.field === "minimum"
                    ? "Mínima"
                    : e.field === "total"
                      ? "Mayor precipitación"
                      : "Máxima"}{" "}
                  · {e.population} orígenes comparables
                </span>
                <strong>
                  {number(
                    e.extreme[e.field as "minimum" | "maximum" | "total"],
                  )}{" "}
                  {e.extreme.unit}
                </strong>
                <button onClick={() => onChoose(e.extreme.station_id!)}>
                  {e.extreme.name} ↗
                </button>
                <small>
                  {date(e.extreme.period_start)} → {date(e.extreme.period_end)}
                </small>
              </article>
            ))}
          </div>
          {networkData && !networkData.items.length && (
            <p className="history-empty">
              Sin datos diarios disponibles para esta fecha.
            </p>
          )}
          {networkData && (
            <HistoryTable
              items={networkData.items.map(converted)}
              onChoose={onChoose}
            />
          )}
        </>
      ) : (
        <>
          <div className="history-controls">
            <label>
              Estación del archivo
              <select
                aria-label="Estación del archivo"
                value={stationId ?? ""}
                onChange={(e) => onChoose(e.target.value)}
              >
                <option value="">Selecciona una estación</option>
                {stations.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
                {detail && !stations.some((s) => s.id === detail.id) && (
                  <option value={detail.id}>{detail.name}</option>
                )}
              </select>
            </label>
            {detail && (
              <label>
                Origen de la serie
                <select
                  aria-label="Origen de la serie"
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                >
                  {source && !detail.sources.some((s) => s.id === source) && (
                    <option value={source}>Origen retirado · elige otro</option>
                  )}
                  {detail.sources.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.provider.toUpperCase()} · {s.external_id}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              Variable histórica
              <select
                aria-label="Variable histórica"
                value={metric}
                onChange={(e) => setMetric(e.target.value)}
              >
                {metrics.map((m) => (
                  <option key={m} value={m}>
                    {metricNames[m]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {stationId && (
            <>
              <div className="history-tabs" aria-label="Vista del archivo">
                {Object.entries(labels).map(([key, label]) => (
                  <button
                    key={key}
                    aria-pressed={mode === key}
                    onClick={() => setMode(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {mode !== "records" && (
                <>
                  <div
                    className="history-periods"
                    aria-label="Periodo histórico"
                  >
                    {[24, 48, 72, 168, 336].map((h) => (
                      <button
                        key={h}
                        aria-pressed={hours === h}
                        onClick={() => setHours(h)}
                      >
                        {h < 168 ? `${h} h` : `${h / 24} días`}
                      </button>
                    ))}
                    <button
                      aria-pressed={hours === 0}
                      onClick={() => setHours(0)}
                    >
                      Fechas personalizadas
                    </button>
                  </div>
                  {hours === 0 && (
                    <div className="history-controls">
                      <label>
                        Desde (UTC)
                        <input
                          type="date"
                          value={from}
                          onChange={(e) => setFrom(e.target.value)}
                        />
                      </label>
                      <label>
                        Hasta (UTC, excluido)
                        <input
                          type="date"
                          value={to}
                          onChange={(e) => setTo(e.target.value)}
                        />
                      </label>
                    </div>
                  )}
                  {mode !== "series" && (
                    <label className="history-method">
                      Método del diario
                      <select
                        value={method}
                        onChange={(e) => setMethod(e.target.value)}
                      >
                        <option value="all">Ambos, separados</option>
                        <option value="local">Cálculo Meteocentro</option>
                        <option value="provider">
                          Publicado por el proveedor
                        </option>
                      </select>
                    </label>
                  )}
                </>
              )}
              {busy && <p role="status">Consultando archivo…</p>}
              {result && (
                <>
                  <div className="coverage-strip">
                    <div>
                      <span>DETALLE INTRADIARIO</span>
                      <strong>{dayLabel(result.availability.raw.first)}</strong>
                      <small>
                        hasta {dayLabel(result.availability.raw.last)}
                      </small>
                    </div>
                    <div>
                      <span>RESÚMENES DIARIOS</span>
                      <strong>{dayLabel(result.availability.day.first)}</strong>
                      <small>
                        hasta {dayLabel(result.availability.day.last)}
                      </small>
                    </div>
                    <div>
                      <span>PROCEDENCIA</span>
                      <strong>
                        {result.provider.toUpperCase()} · {result.external_id}
                      </strong>
                      <small>
                        {mode === "series"
                          ? {
                              raw: "Resolución original",
                              hour: "Agregados horarios · mínima y máxima",
                              day: "Agregados diarios · mínima y máxima",
                            }[resolution]
                          : "Métodos y ventanas separados"}
                      </small>
                    </div>
                  </div>
                  {result.availability.pending_days > 0 && (
                    <p className="notice">
                      Hay {result.availability.pending_days} días pendientes de
                      recalcular. Los agregados pueden estar desactualizados.
                    </p>
                  )}
                  <div className="chart-toolbar">
                    <div>
                      <button
                        aria-pressed={view === "chart"}
                        onClick={() => setView("chart")}
                      >
                        Gráfico
                      </button>
                      <button
                        aria-pressed={view === "table"}
                        onClick={() => setView("table")}
                      >
                        Tabla de registros
                      </button>
                    </div>
                    {result.export_allowed &&
                      days <= (mode === "series" ? 31 : 366) && (
                        <a
                          href={`/api/v1/stations/${stationId}/export.csv?${new URLSearchParams({ from: range[0], to: range[1], source, metric, resolution: mode === "series" ? "raw" : "day" })}`}
                        >
                          Descargar CSV ↧
                        </a>
                      )}
                  </div>
                  {!result.export_allowed && (
                    <p>Exportación no autorizada para este origen.</p>
                  )}
                  {!result.items.length ? (
                    <div className="history-empty">
                      <h2>Sin datos disponibles</h2>
                      <p>
                        El archivo intradiario empieza en{" "}
                        {dayLabel(result.availability.raw.first)}. Los diarios
                        antiguos no generan curvas horarias.
                      </p>
                    </div>
                  ) : view === "chart" &&
                    (mode === "series" ||
                      mode === "daily" ||
                      mode === "month" ||
                      mode === "year") ? (
                    <Suspense fallback={<p>Cargando gráfico…</p>}>
                      <HistoryChart
                        items={result.items}
                        metric={metric}
                        range={range}
                      />
                    </Suspense>
                  ) : (
                    <HistoryTable items={result.items} />
                  )}
                  {result.coverage?.map((c) => (
                    <p className="coverage-note" key={c.channel}>
                      Cobertura de {metricNames[metric]} en el intervalo:{" "}
                      <strong>{number(c.coverage * 100)} %</strong> ·{" "}
                      {c.product}
                      {c.flags.length > 0 && (
                        <small> Limitaciones: {c.flags.join(", ")}</small>
                      )}
                    </p>
                  ))}
                  <p className="chart-note">
                    Hora de visualización: Europe/Madrid, con zona horaria en
                    cada registro. Sin interpolación en huecos. Líneas
                    discontinuas: mínimos y máximos del intervalo; lluvia: total
                    medido, que puede ser parcial.
                  </p>
                  {result.events && result.events.length > 0 && (
                    <details>
                      <summary>
                        Cambios de emplazamiento ({result.events.length})
                      </summary>
                      {result.events.map((event, i) => (
                        <p key={i}>
                          {date(event.time)} · {event.latitude}°,{" "}
                          {event.longitude}°
                        </p>
                      ))}
                    </details>
                  )}
                  <details>
                    <summary>
                      Fuentes y métodos presentes en la selección
                    </summary>
                    {[
                      ...new Map(
                        result.items.map((p) => [p.channel, p]),
                      ).values(),
                    ].map((p) => (
                      <p key={p.channel}>
                        {p.product} · {historyMethod(p.aggregation_method)} ·{" "}
                        {historyPeriod(p.period_basis)}
                      </p>
                    ))}
                  </details>
                  <div className="pagination">
                    <button disabled={!offset} onClick={() => setOffset(0)}>
                      Primera página
                    </button>
                    <span>{result.items.length} registros en esta página</span>
                    <button
                      disabled={result.next_offset == null}
                      onClick={() => setOffset(result.next_offset!)}
                    >
                      Siguiente página
                    </button>
                  </div>
                </>
              )}
              {mode === "records" && !busy && (
                <>
                  <p>
                    Extremos del archivo disponible; no récords absolutos. Cada
                    origen y método conserva su cobertura.
                  </p>
                  {!records.length && (
                    <p className="history-empty">Sin efemérides disponibles.</p>
                  )}
                  {records.map((record) => (
                    <article className="record-card" key={record.channel}>
                      <h2>
                        {historyPeriod(record.period_basis)} ·{" "}
                        {historyMethod(record.aggregation_method)}
                      </h2>
                      <p>
                        {dayLabel(record.first)} → {dayLabel(record.last)} ·{" "}
                        {record.days_available} diarios archivados
                      </p>
                      <div className="history-extremes">
                        {["minimum", "maximum", "total"].map((key) => {
                          const r =
                            record[key as "minimum" | "maximum" | "total"];
                          return (
                            <div key={key}>
                              <span>
                                {
                                  {
                                    minimum: "Mínima",
                                    maximum: "Máxima",
                                    total: "Mayor total",
                                  }[key]
                                }
                              </span>
                              <strong>
                                {number(
                                  r?.value == null
                                    ? null
                                    : r.value *
                                        (record.unit === "m/s" ? 3.6 : 1),
                                )}{" "}
                                {record.unit === "m/s" ? "km/h" : record.unit}
                              </strong>
                              <small>
                                {r
                                  ? dayLabel(r.period_start)
                                  : "Sin cobertura suficiente"}
                              </small>
                              <small>
                                {r?.coverage == null
                                  ? "Cobertura desconocida"
                                  : `${number(r.coverage * 100)} % de cobertura`}
                              </small>
                            </div>
                          );
                        })}
                      </div>
                    </article>
                  ))}
                </>
              )}
            </>
          )}
          {!stationId && (
            <p className="history-empty">
              Selecciona una estación para explorar sus series, diarios y
              efemérides.
            </p>
          )}
        </>
      )}
    </section>
  );
}
function HistoryTable({
  items,
  onChoose,
}: {
  items: HistoricalPoint[];
  onChoose?: (id: string) => void;
}) {
  return (
    <div className="table-scroll history-table">
      <table>
        <caption>Registros con unidad, periodo, calidad y cobertura</caption>
        <thead>
          <tr>
            {[
              "Fecha / estación",
              "Valor / media",
              "Mínima",
              "Máxima",
              "Total",
              "Cobertura",
              "Origen y periodo",
            ].map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((p, i) => (
            <tr key={i}>
              <td>
                {p.name && (
                  <button onClick={() => onChoose?.(p.station_id!)}>
                    {p.name} ↗
                  </button>
                )}
                {p.bucket ?? date(p.time ?? p.period_start)}
                <small>{p.provisional ? "Provisional" : ""}</small>
              </td>
              <td>
                {number(p.value ?? p.mean)} {p.unit}
              </td>
              <td>{number(p.minimum)}</td>
              <td>{number(p.maximum)}</td>
              <td>{number(p.total)}</td>
              <td>
                {p.coverage == null
                  ? "Desconocida"
                  : `${number(p.coverage * 100)} %`}
                {p.partial && <small>Parcial</small>}
              </td>
              <td>
                <details>
                  <summary>
                    {p.product} · {historyMethod(p.aggregation_method)}
                  </summary>
                  <p>{historyPeriod(p.period_basis)}</p>
                  <p>
                    {p.period_start ? date(p.period_start) : date(p.time)} →{" "}
                    {p.period_end ? date(p.period_end) : "—"}
                  </p>
                  <p>
                    Calidad: {p.flags?.join(", ") || "Sin banderas adicionales"}
                  </p>
                  <p>Recogido: {date(p.fetched_at)}</p>
                  {p.original && <p>Original: {JSON.stringify(p.original)}</p>}
                  {p.quality && (
                    <p>Calidad de origen: {JSON.stringify(p.quality)}</p>
                  )}
                </details>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
