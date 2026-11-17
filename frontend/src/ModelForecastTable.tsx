import { useMemo } from "react";
import type { ModelForecast } from "./ForecastPage";
import { cloudiness, isNight, skySymbol } from "./forecastSky";

const zone = "Europe/Madrid";
const H3 = 3 * 3600;
const H6 = 6 * 3600;
const dayKey = (ms: number) => new Date(ms).toLocaleDateString("sv-SE", { timeZone: zone });
const hourLabel = new Intl.DateTimeFormat("es-ES", { hour: "2-digit", minute: "2-digit", timeZone: zone });
const weekdayShort = new Intl.DateTimeFormat("es-ES", { weekday: "short", timeZone: "UTC" });
const monthShort = new Intl.DateTimeFormat("es-ES", { month: "short", timeZone: "UTC" });

/** How many days of table each model gets: AROME as far as it reaches, the globals a week. */
export const TABLE_DAYS: Record<string, number> = { arome: 3, gfs: 7, ecmwf: 7 };

export type Row = {
  t: number;
  /** Hours covered by the precipitation of this row, ending at t. */
  step: 3 | 6;
  rain: number | null;
  snow: number | null;
};

/**
 * Table rows from now to the horizon. Each row's rain is the total since the previous row,
 * so nothing is counted twice. Where the model is only 6-hourly (ECMWF past 144 h) the
 * 3 h view falls back to 6 h rows instead of splitting totals the model never gave.
 */
export function tableRows(model: ModelForecast, every: 3 | 6, from: number, days: number): Row[] {
  const short = model.accumulated_3h;
  const long = model.accumulated_6h;
  const shortIndex = new Map((short?.time ?? []).map((t, i) => [t, i]));
  const longIndex = new Map(long.time.map((t, i) => [t, i]));
  const fineUntil = every === 3 && short?.time.length ? short.time[short.time.length - 1] : -Infinity;
  const start = from / 1000;
  const end = start + days * 86400;
  const rows: Row[] = [];
  for (const t of model.time) {
    if (t < start || t > end) continue;
    if (t % H3 === 0 && t <= fineUntil) {
      const i = shortIndex.get(t);
      rows.push({
        t,
        step: 3,
        rain: i === undefined ? null : (short!.precipitation[i] ?? null),
        snow: i === undefined ? null : (short!.snowfall[i] ?? null),
      });
    } else if (t % H6 === 0) {
      const i = longIndex.get(t);
      rows.push({
        t,
        step: 6,
        rain: i === undefined ? null : (long.precipitation[i] ?? null),
        snow: i === undefined ? null : (long.snowfall[i] ?? null),
      });
    }
  }
  return rows;
}

export type DaySummary = {
  date: string;
  max: number | null;
  min: number | null;
  /** null when any window of the day is unknown: never a silent partial sum. */
  rain: number | null;
  snow: number | null;
  gust: number | null;
  icon: string;
  partial: boolean;
};

/** Daily figures from the 3-hourly rows; rain goes to the day of each window's middle. */
export function daySummaries(model: ModelForecast, rows: Row[]): DaySummary[] {
  const index = new Map(model.time.map((t, i) => [t, i]));
  const value = (key: string, t: number) => {
    const i = index.get(t);
    return i === undefined ? null : (model.series[key]?.[i] ?? null);
  };
  const days = new Map<string, { rows: Row[]; rainRows: Row[] }>();
  const day = (key: string) => {
    let entry = days.get(key);
    if (!entry) days.set(key, (entry = { rows: [], rainRows: [] }));
    return entry;
  };
  for (const row of rows) {
    day(dayKey(row.t * 1000)).rows.push(row);
    day(dayKey((row.t - (row.step * 3600) / 2) * 1000)).rainRows.push(row);
  }
  const last = rows.length ? rows[rows.length - 1].t : 0;
  return [...days.entries()]
    .filter(([, d]) => d.rows.length)
    .map(([date, d]) => {
      const numbers = (key: string) =>
        d.rows.map((r) => value(key, r.t)).filter((v): v is number => v !== null);
      const temps = numbers("temperature_2m");
      const gusts = numbers("wind_gusts_10m");
      const rain = d.rainRows.some((r) => r.rain === null)
        ? null
        : Math.round(d.rainRows.reduce((sum, r) => sum + (r.rain ?? 0), 0) * 10) / 10;
      const snow = d.rainRows.some((r) => r.snow === null)
        ? null
        : Math.round(d.rainRows.reduce((sum, r) => sum + (r.snow ?? 0), 0) * 10) / 10;
      const daytime = d.rows.filter((r) => !isNight(r.t * 1000));
      const clouds = (daytime.length ? daytime : d.rows)
        .map((r) => cloudiness(value("cloud_cover_low", r.t), value("cloud_cover_mid", r.t), value("cloud_cover_high", r.t)))
        .filter((v): v is number => v !== null);
      const cloud = clouds.length ? clouds.reduce((a, b) => a + b, 0) / clouds.length : null;
      const firstHour = Number(hourLabel.format(d.rows[0].t * 1000).slice(0, 2));
      const lastRow = d.rows[d.rows.length - 1].t;
      const lastHour = Number(hourLabel.format(lastRow * 1000).slice(0, 2));
      return {
        date,
        max: temps.length ? Math.max(...temps) : null,
        min: temps.length ? Math.min(...temps) : null,
        rain,
        snow,
        gust: gusts.length ? Math.max(...gusts) : null,
        // Daily symbol: a wet day needs more than a trace; noon stands for the daytime sky.
        icon: skySymbol(
          cloud,
          rain === null ? null : rain >= 1 ? 1 : rain >= 0.2 ? 0.1 : 0,
          snow === null ? null : snow >= 1 ? 1 : 0,
          Date.parse(`${date}T11:00:00Z`),
        ),
        partial: firstHour > 2 || (lastRow === last && lastHour < 22),
      };
    });
}

type Stop = [number, string];
function hex(color: string) {
  return [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16));
}
/** Linear colour between stops; the text turns white on dark cells. */
function paint(value: number | null | undefined, stops: Stop[], whiteFrom?: number, whiteBelow?: number) {
  if (value == null) return undefined;
  let color = stops[stops.length - 1][1];
  if (value <= stops[0][0]) color = stops[0][1];
  else {
    for (let i = 1; i < stops.length; i++) {
      if (value <= stops[i][0]) {
        const [a, ca] = stops[i - 1];
        const [b, cb] = stops[i];
        const k = (value - a) / (b - a);
        const [x, y] = [hex(ca), hex(cb)];
        color = `rgb(${x.map((c, j) => Math.round(c + (y[j] - c) * k)).join(",")})`;
        break;
      }
    }
  }
  const white = (whiteFrom !== undefined && value >= whiteFrom) || (whiteBelow !== undefined && value <= whiteBelow);
  return { background: color, color: white ? "#fff" : undefined };
}

const TEMP: Stop[] = [
  [-10, "#6f4ea8"],
  [-4, "#4d74c9"],
  [0, "#7fb0e6"],
  [5, "#a9dcd0"],
  [10, "#d3eda0"],
  [15, "#f1e35d"],
  [20, "#f6c544"],
  [25, "#f29b3c"],
  [30, "#e6633c"],
  [35, "#c8344a"],
  [40, "#8c1f4b"],
];
const WIND: Stop[] = [
  [0, "#f5faf3"],
  [10, "#dcefd2"],
  [20, "#c1e4aa"],
  [30, "#f4e27c"],
  [45, "#f4b553"],
  [60, "#ea7a3d"],
  [80, "#cc3b3b"],
];
const GUST: Stop[] = [
  [0, "#f5faf3"],
  [20, "#dcefd2"],
  [35, "#f4e27c"],
  [50, "#f4b553"],
  [65, "#ea7a3d"],
  [85, "#cc3b3b"],
  [110, "#962a52"],
];
const RAIN: Stop[] = [
  [0.1, "#e7f1fb"],
  [1, "#bfdaf5"],
  [3, "#83b8eb"],
  [7, "#418cd9"],
  [15, "#1f5fae"],
  [30, "#57309a"],
];
const HUMIDITY: Stop[] = [
  [20, "#fbf3df"],
  [50, "#eef4ea"],
  [75, "#cfe8de"],
  [100, "#93cdb9"],
];
const CLOUD: Stop[] = [
  [0, "#f6f8fb"],
  [100, "#b3bcc7"],
];

const SECTORS = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
const fmt = (value: number | null | undefined, digits = 0) =>
  value == null ? "—" : value.toLocaleString("es-ES", { minimumFractionDigits: digits, maximumFractionDigits: digits });

function Wind({ direction }: { direction: number | null }) {
  if (direction == null) return <>—</>;
  return (
    <span className="model-wind" title={`Viento del ${SECTORS[Math.round(direction / 45) % 8]} (${Math.round(direction)}°)`}>
      {/* The arrow points where the wind blows to; the letters say where it comes from. */}
      <span aria-hidden="true" style={{ transform: `rotate(${direction + 180}deg)` }}>
        ↑
      </span>
      {SECTORS[Math.round(direction / 45) % 8]}
    </span>
  );
}

export function ModelDays({ days }: { days: DaySummary[] }) {
  const today = dayKey(Date.now());
  return (
    <div className="model-days">
      {days.map((d) => {
        const date = new Date(`${d.date}T00:00:00Z`);
        return (
          <article key={d.date} className="model-day">
            <h4>
              {d.date === today ? "Hoy" : weekdayShort.format(date)} <strong>{date.getUTCDate()}</strong>{" "}
              {monthShort.format(date)}
            </h4>
            <div className="model-day-icon" aria-hidden="true">
              {d.icon || "·"}
            </div>
            <div className="model-day-temps">
              <span style={paint(d.max, TEMP, 33, -6)} title="Máxima">
                {fmt(d.max)}°
              </span>
              <span style={paint(d.min, TEMP, 33, -6)} title="Mínima">
                {fmt(d.min)}°
              </span>
            </div>
            <div className={`model-day-rain${(d.rain ?? 0) >= 1 ? " wet" : ""}`}>
              {d.rain === null ? "— mm" : `${fmt(d.rain, d.rain > 0 && d.rain < 10 ? 1 : 0)} mm`}
              {(d.snow ?? 0) >= 0.5 && <small> · nieve {fmt(d.snow, 1)} cm</small>}
            </div>
            <div className={`model-day-gust${(d.gust ?? 0) >= 50 ? " strong" : ""}`}>racha {fmt(d.gust)}</div>
            {d.partial && <small className="model-day-partial">día parcial</small>}
          </article>
        );
      })}
    </div>
  );
}

export function ModelTable({ model, rows }: { model: ModelForecast; rows: Row[] }) {
  const index = useMemo(() => new Map(model.time.map((t, i) => [t, i])), [model]);
  const groups = useMemo(() => {
    const result: { date: string; rows: Row[] }[] = [];
    for (const row of rows) {
      const date = dayKey(row.t * 1000);
      if (result.at(-1)?.date !== date) result.push({ date, rows: [] });
      result.at(-1)!.rows.push(row);
    }
    return result;
  }, [rows]);
  const mixed = rows.some((r) => r.step === 3) && rows.some((r) => r.step === 6);
  const now = Date.now() / 1000;
  const current = rows.findIndex((r, i) => r.t >= now - (r.step * 3600) / 2 || i === rows.length - 1);
  const v = (key: string, t: number) => {
    const i = index.get(t);
    return i === undefined ? null : (model.series[key]?.[i] ?? null);
  };
  return (
    <div className="model-table-scroll" tabIndex={0} role="region" aria-label={`Tabla de previsión ${model.label}`}>
      <table className="model-table">
        <thead>
          <tr>
            <th rowSpan={2}>Día</th>
            <th rowSpan={2}>Hora</th>
            <th rowSpan={2}>
              Temp.
              <small>°C</small>
            </th>
            <th rowSpan={2}>
              Lluvia
              <small>mm</small>
            </th>
            <th colSpan={3}>Viento (km/h)</th>
            <th rowSpan={2}>
              Hum.
              <small>%</small>
            </th>
            <th rowSpan={2}>
              Presión
              <small>hPa</small>
            </th>
            <th rowSpan={2}>
              Nubes
              <small>%</small>
            </th>
            <th rowSpan={2}>Tiempo</th>
          </tr>
          <tr>
            <th>Dir.</th>
            <th>Medio</th>
            <th>Racha</th>
          </tr>
        </thead>
        {groups.map((group) => {
          const date = new Date(`${group.date}T00:00:00Z`);
          return (
            <tbody key={group.date}>
              {group.rows.map((row, i) => {
                const ms = row.t * 1000;
                const temp = v("temperature_2m", row.t);
                const speed = v("wind_speed_10m", row.t);
                const gust = v("wind_gusts_10m", row.t);
                const humidity = v("relative_humidity_2m", row.t);
                const total = v("cloud_cover", row.t);
                const [low, mid, high] = [v("cloud_cover_low", row.t), v("cloud_cover_mid", row.t), v("cloud_cover_high", row.t)];
                // Night follows the row hour, like the ☾ beside it.
                const sky = skySymbol(cloudiness(low, mid, high), row.rain, row.snow, ms);
                const snowy = (row.snow ?? 0) >= 0.5;
                return (
                  <tr key={row.t} className={rows[current]?.t === row.t ? "current" : ""}>
                    {i === 0 && (
                      <th rowSpan={group.rows.length} scope="rowgroup" className="model-table-day">
                        <small>{weekdayShort.format(date)}</small>
                        <strong>{date.getUTCDate()}</strong>
                        <small>{monthShort.format(date)}</small>
                      </th>
                    )}
                    <th scope="row" className="model-table-hour">
                      {isNight(ms) && <span aria-label="noche">☾ </span>}
                      {hourLabel.format(ms)}
                    </th>
                    <td className="strong" style={paint(temp, TEMP, 33, -6)}>
                      {fmt(temp, 1)}
                    </td>
                    <td
                      className={row.rain ? "strong" : "faint"}
                      style={row.rain ? paint(row.rain, RAIN, 7) : undefined}
                      title={`Acumulada en ${row.step} h hasta esta hora${snowy ? ` · nieve ${fmt(row.snow, 1)} cm` : ""}`}
                    >
                      {row.rain === null ? "—" : row.rain === 0 ? "0" : fmt(row.rain, 1)}
                      {snowy && <small> ❄</small>}
                      {mixed && row.step === 6 && <small className="step"> 6 h</small>}
                    </td>
                    <td className="dir">
                      <Wind direction={v("wind_direction_10m", row.t)} />
                    </td>
                    <td style={paint(speed, WIND, 70)}>{fmt(speed)}</td>
                    <td className="strong" style={paint(gust, GUST, 70)}>
                      {fmt(gust)}
                    </td>
                    <td style={paint(humidity, HUMIDITY)}>{fmt(humidity)}</td>
                    <td>{fmt(v("pressure_msl", row.t))}</td>
                    <td className="clouds" style={paint(total, CLOUD)}>
                      {fmt(total)}
                      <small title="Nubes bajas · medias · altas">
                        {fmt(low)}·{fmt(mid)}·{fmt(high)}
                      </small>
                    </td>
                    <td className="sky">{sky || "·"}</td>
                  </tr>
                );
              })}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}
