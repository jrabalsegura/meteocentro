import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, number } from "./data";

type DayTemperature = {
  minimum: number | null;
  maximum: number | null;
  minimum_at: string | null;
  maximum_at: string | null;
  coverage: number | null;
  unit: string;
};
type DayRain = {
  total: number;
  coverage: number;
  partial: boolean;
  derivation: string;
  notes: string[];
};
type OverviewDay = {
  day: string;
  temperature: DayTemperature | null;
  rain: DayRain | null;
  pending: boolean;
};
type Period = {
  from: string;
  to: string;
  days: number;
  temperature: {
    unit: string;
    minimum: number | null;
    minimum_at: string | null;
    minimum_day: string | null;
    maximum: number | null;
    maximum_at: string | null;
    maximum_day: string | null;
    days_with_data: number;
  } | null;
  rain: {
    total: number;
    days_with_data: number;
    rain_days: number;
    partial: boolean;
  } | null;
  pending_days: number;
};
type Overview = {
  provider: string;
  archive_first: string | null;
  latest_observation: string | null;
  days: OverviewDay[];
  periods: Record<"yesterday" | "week" | "month", Period>;
};

const PERIODS = [
  ["yesterday", "Ayer"],
  ["week", "Últimos 7 días"],
  ["month", "Últimos 30 días"],
] as const;
const WARM = "#c4572f";
const RAIN = "#2c6fb0";

function civilDay(day: string, options: Intl.DateTimeFormatOptions) {
  // Civil days travel as YYYY-MM-DD; noon UTC keeps the same date in Madrid.
  return new Intl.DateTimeFormat("es-ES", {
    timeZone: "Europe/Madrid",
    ...options,
  }).format(new Date(`${day}T12:00:00Z`));
}
function clock(value: string | null) {
  return value
    ? new Intl.DateTimeFormat("es-ES", {
        timeZone: "Europe/Madrid",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value))
    : null;
}
function instant(value: string | null) {
  return value
    ? new Intl.DateTimeFormat("es-ES", {
        timeZone: "Europe/Madrid",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value))
    : "sin datos";
}

function when(day: string | null, at: string | null, single: boolean) {
  if (!day) return null;
  const time = clock(at);
  const date = civilDay(day, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
  if (single) return time ? `hacia las ${time}` : null;
  return time ? `${date}, hacia las ${time}` : date;
}

function PeriodCard({ label, period }: { label: string; period: Period }) {
  const t = period.temperature;
  const r = period.rain;
  const single = period.days === 1;
  const range = single
    ? civilDay(period.to, { weekday: "long", day: "numeric", month: "long" })
    : `${civilDay(period.from, { day: "numeric", month: "short" })} – ${civilDay(period.to, { day: "numeric", month: "short" })}`;
  const missingTemperature = t ? period.days - t.days_with_data : period.days;
  return (
    <article className="overview-card">
      <header>
        <h2>{label}</h2>
        <small>{range}</small>
      </header>
      <dl>
        <div>
          <dt>Mínima</dt>
          <dd>
            <strong>{number(t?.minimum)}</strong> {t && "°C"}
            <small>
              {when(t?.minimum_day ?? null, t?.minimum_at ?? null, single)}
            </small>
          </dd>
        </div>
        <div>
          <dt>Máxima</dt>
          <dd>
            <strong>{number(t?.maximum)}</strong> {t && "°C"}
            <small>
              {when(t?.maximum_day ?? null, t?.maximum_at ?? null, single)}
            </small>
          </dd>
        </div>
        <div>
          <dt>Precipitación</dt>
          <dd>
            <strong>{number(r?.total)}</strong> {r && "mm"}
            <small>
              {r &&
                !single &&
                `${r.rain_days} ${r.rain_days === 1 ? "día" : "días"} con lluvia`}
              {r?.partial && (single ? "Suma parcial" : " · suma parcial")}
            </small>
          </dd>
        </div>
      </dl>
      {(missingTemperature > 0 || (r && r.days_with_data < period.days)) &&
        !single && (
          <p className="overview-gaps">
            Temperaturas de {t?.days_with_data ?? 0} de {period.days} días;
            lluvia de {r?.days_with_data ?? 0}. Los días sin datos no cuentan
            como cero.
          </p>
        )}
      {period.pending_days > 0 && (
        <p className="overview-gaps">
          {period.pending_days} {period.pending_days === 1 ? "día" : "días"}{" "}
          pendiente de recalcular.
        </p>
      )}
    </article>
  );
}

function niceStep(span: number) {
  return span > 30 ? 10 : span > 12 ? 5 : 2;
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => setWidth(Math.max(280, element.clientWidth));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function MonthChart({ days }: { days: OverviewDay[] }) {
  const [hover, setHover] = useState<number | null>(null);
  // SVG units equal CSS pixels, so axis text keeps its size on every screen.
  const [plot, width] = useWidth();
  const lows = days.flatMap((d) =>
    d.temperature?.minimum != null ? [d.temperature.minimum] : [],
  );
  const highs = days.flatMap((d) =>
    d.temperature?.maximum != null ? [d.temperature.maximum] : [],
  );
  const rains = days.flatMap((d) => (d.rain ? [d.rain.total] : []));
  if (!lows.length && !highs.length && !rains.length) return null;
  const left = 34,
    right = 8,
    tempTop = 12,
    tempHeight = 150,
    rainTop = tempTop + tempHeight + 30,
    rainHeight = 70,
    height = rainTop + rainHeight + 26;
  const column = (width - left - right) / days.length;
  const bar = Math.max(4, Math.min(12, column - 6));
  const all = [...lows, ...highs];
  const step = niceStep(Math.max(...all, 1) - Math.min(...all, 0));
  const tMin = Math.floor((Math.min(...all, 30) - 1) / step) * step;
  const tMax = Math.ceil((Math.max(...all, -30) + 1) / step) * step;
  const ty = (v: number) =>
    tempTop + tempHeight - ((v - tMin) / (tMax - tMin || 1)) * tempHeight;
  const rMax = Math.max(5, Math.ceil(Math.max(0, ...rains) / 5) * 5);
  const ry = (v: number) => rainTop + rainHeight - (v / rMax) * rainHeight;
  const ticks: number[] = [];
  for (let v = tMin; v <= tMax; v += step) ticks.push(v);
  const x = (i: number) => left + column * i + column / 2;
  const active = hover == null ? null : days[hover];
  return (
    <figure className="overview-chart">
      <figcaption>
        Últimos 30 días: rango diario de temperatura y precipitación
      </figcaption>
      <div
        className="overview-plot"
        ref={plot}
        onMouseLeave={() => setHover(null)}
      >
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="Temperatura mínima a máxima y precipitación de cada día"
        >
          {ticks.map((v) => (
            <g key={v}>
              <line
                x1={left}
                x2={width - right}
                y1={ty(v)}
                y2={ty(v)}
                className="overview-grid"
              />
              <text x={left - 6} y={ty(v) + 4} textAnchor="end">
                {v}°
              </text>
            </g>
          ))}
          <line
            x1={left}
            x2={width - right}
            y1={rainTop + rainHeight}
            y2={rainTop + rainHeight}
            className="overview-axis"
          />
          <text x={left - 6} y={rainTop + 8} textAnchor="end">
            {rMax}
          </text>
          <text x={left - 6} y={rainTop + rainHeight + 4} textAnchor="end">
            0
          </text>
          <text x={left} y={rainTop - 10} className="overview-panel">
            Precipitación (mm)
          </text>
          {days.map((d, i) => {
            const t = d.temperature;
            const lo = t?.minimum ?? t?.maximum;
            const hi = t?.maximum ?? t?.minimum;
            return (
              <g key={d.day} opacity={hover == null || hover === i ? 1 : 0.45}>
                {lo != null && hi != null && (
                  <rect
                    x={x(i) - bar / 2}
                    width={bar}
                    y={ty(hi)}
                    height={Math.max(2, ty(lo) - ty(hi))}
                    rx={Math.min(4, bar / 2)}
                    fill={WARM}
                  />
                )}
                {d.rain && d.rain.total > 0 && (
                  <rect
                    x={x(i) - bar / 2}
                    width={bar}
                    y={ry(d.rain.total)}
                    height={Math.max(
                      2,
                      rainTop + rainHeight - ry(d.rain.total),
                    )}
                    rx={Math.min(2, bar / 2)}
                    fill={RAIN}
                  />
                )}
                {(i === days.length - 1 || (days.length - 1 - i) % 7 === 0) && (
                  <text
                    x={x(i)}
                    y={height - 6}
                    textAnchor={i === days.length - 1 ? "end" : "middle"}
                  >
                    {civilDay(d.day, { day: "numeric", month: "short" })}
                  </text>
                )}
                <rect
                  x={left + column * i}
                  width={column}
                  y={0}
                  height={height - 20}
                  fill="transparent"
                  onMouseEnter={() => setHover(i)}
                  onClick={() => setHover(i)}
                />
              </g>
            );
          })}
        </svg>
        {active && hover != null && (
          <div
            className="overview-tooltip"
            style={{
              left: `${(x(hover) / width) * 100}%`,
              transform: `translateX(${hover > days.length / 2 ? "-100%" : "0"})`,
            }}
            role="status"
          >
            <strong>
              {civilDay(active.day, {
                weekday: "long",
                day: "numeric",
                month: "long",
              })}
            </strong>
            <span>
              Mínima {number(active.temperature?.minimum)} °C · Máxima{" "}
              {number(active.temperature?.maximum)} °C
            </span>
            <span>
              Precipitación{" "}
              {active.rain
                ? `${number(active.rain.total)} mm${active.rain.partial ? " (parcial)" : ""}`
                : "sin datos"}
            </span>
          </div>
        )}
      </div>
    </figure>
  );
}

export default function HistoryOverview({
  stationId,
  source,
  refresh,
}: {
  stationId: string;
  source: string;
  refresh: number;
}) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setData(null);
    setError("");
    if (!source) return;
    const controller = new AbortController();
    api<Overview>(
      `/api/v1/stations/${stationId}/overview?${new URLSearchParams({ source })}`,
      controller.signal,
    )
      .then(setData)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [stationId, source, refresh]);
  if (error) return <p className="notice error">{error}</p>;
  if (!data) return <p role="status">Preparando el resumen…</p>;
  return (
    <section className="history-overview" aria-label="Resumen del archivo">
      <p className="overview-latest">
        Último dato <strong>{instant(data.latest_observation)}</strong>
        {data.archive_first && (
          <> · archivo desde {instant(data.archive_first)}</>
        )}
      </p>
      <div className="overview-cards">
        {PERIODS.map(([key, label]) => (
          <PeriodCard key={key} label={label} period={data.periods[key]} />
        ))}
      </div>
      <MonthChart days={data.days} />
      <details className="overview-table">
        <summary>Ver los 30 días en tabla</summary>
        <table className="history-table">
          <thead>
            <tr>
              <th>Día</th>
              <th>Mínima (°C)</th>
              <th>Máxima (°C)</th>
              <th>Precipitación (mm)</th>
            </tr>
          </thead>
          <tbody>
            {[...data.days].reverse().map((d) => (
              <tr key={d.day}>
                <td>
                  {civilDay(d.day, {
                    weekday: "short",
                    day: "numeric",
                    month: "short",
                  })}
                  {d.pending && " · pendiente"}
                </td>
                <td>{number(d.temperature?.minimum)}</td>
                <td>{number(d.temperature?.maximum)}</td>
                <td>
                  {d.rain ? number(d.rain.total) : "—"}
                  {d.rain?.partial && " (parcial)"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      <p className="chart-note">
        Días civiles completos de Madrid. Temperaturas de los resúmenes diarios
        del archivo, a partir de las lecturas recogidas; la lluvia de{" "}
        {data.provider === "aemet"
          ? "AEMET suma sus totales horarios"
          : "Meteoclimatic suma los incrementos de su contador diario"}
        . Sin datos no significa cero.
      </p>
    </section>
  );
}
