import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { api } from "./data";

const Meteogram = lazy(() =>
  import("./ForecastCharts").then((m) => ({ default: m.Meteogram })),
);
const EnsembleChart = lazy(() =>
  import("./ForecastCharts").then((m) => ({ default: m.EnsembleChart })),
);

type Snapshot<T> = {
  issued_at: string | null;
  fetched_at: string;
  stale: boolean;
  data: T;
} | null;

type DailyPeriod = {
  period: string;
  sky_code?: string | null;
  sky?: string | null;
  precipitation_probability?: number | null;
  snow_level?: number | null;
  wind_direction?: string | null;
  wind_speed?: number | null;
  gust?: number | null;
};
type Extremes = { max: number | null; min: number | null };
type AemetDaily = {
  issued_at: string;
  link: string;
  days: {
    date: string;
    periods: DailyPeriod[];
    temperature: Extremes;
    feels_like: Extremes;
    humidity: Extremes;
    uv_max: number | null;
  }[];
};
type AemetHour = {
  time: string;
  sky_code?: string | null;
  sky?: string | null;
  night?: boolean;
  temperature?: number | null;
  feels_like?: number | null;
  humidity?: number | null;
  precipitation?: number | null;
  precipitation_trace?: boolean;
  snow?: number | null;
  snow_trace?: boolean;
  wind_direction?: string | null;
  wind_speed?: number | null;
  gust?: number | null;
};
type AemetHourly = {
  issued_at: string;
  link: string;
  hours: AemetHour[];
  windows: {
    start: string;
    end: string;
    precipitation?: number | null;
    storm?: number | null;
    snow?: number | null;
  }[];
};
export type ModelForecast = {
  model: string;
  label: string;
  run: string | null;
  grid: { latitude: number | null; longitude: number | null; elevation: number | null };
  time: number[];
  series: Record<string, (number | null)[]>;
  accumulated_6h: { time: number[]; precipitation: (number | null)[]; snowfall: (number | null)[] };
};
export type EnsembleForecast = {
  model: string;
  label: string;
  run: string | null;
  grid: ModelForecast["grid"];
  members: number;
  temperature_850hPa: { time: number[]; members: (number | null)[][]; mean: (number | null)[] };
  precipitation_6h: { time: number[]; members: (number | null)[][]; mean: (number | null)[] };
};
type Place = {
  code: string;
  name: string;
  aemet_url: string;
  aemet: { daily: Snapshot<AemetDaily>; hourly: Snapshot<AemetHourly> };
  models: Record<string, Snapshot<ModelForecast>>;
  ensembles: Record<string, Snapshot<EnsembleForecast>> | null;
};
type Forecasts = {
  locations: Place[];
  attribution: { source: string; text: string; url: string }[];
};

const zone = "Europe/Madrid";
const weekday = new Intl.DateTimeFormat("es-ES", { weekday: "long", timeZone: "UTC" });
const shortDay = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short", timeZone: "UTC" });
const stamp = new Intl.DateTimeFormat("es-ES", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: zone,
});
const hourOnly = new Intl.DateTimeFormat("es-ES", { hour: "2-digit", minute: "2-digit", timeZone: zone });
const dayOfHour = new Intl.DateTimeFormat("es-ES", { weekday: "short", day: "numeric", timeZone: zone });
const runLabel = (value: string | null) =>
  value
    ? `pasada ${new Date(value).toISOString().slice(11, 13)} UTC del ${shortDay.format(new Date(value))}`
    : "pasada no indicada";

// AEMET sky codes; "n" (night) is handled separately.
function skyIcon(code: string | null | undefined, night = false) {
  if (!code) return "·";
  const n = Number(code);
  if (n === 11) return night ? "🌙" : "☀️";
  if (n === 12 || n === 17) return night ? "🌙" : "🌤️";
  if (n === 13) return "⛅";
  if (n === 14 || n === 15) return "🌥️";
  if (n === 16) return "☁️";
  if ([23, 24, 25, 26].includes(n)) return "🌧️";
  if ([43, 44, 45, 46].includes(n)) return "🌦️";
  if ((n >= 33 && n <= 36) || (n >= 71 && n <= 74)) return "🌨️";
  if (n >= 51 && n <= 64) return "⛈️";
  if (n === 81 || n === 82) return "🌫️";
  if (n === 83) return "🌪️";
  return "🌡️";
}

const num = (value: number | null | undefined, unit = "") =>
  value == null ? "—" : `${value.toLocaleString("es-ES", { maximumFractionDigits: 1 })}${unit}`;

function amount(value: number | null | undefined, trace?: boolean) {
  if (trace) return "Ip";
  return value == null ? "—" : num(value);
}

function Freshness({
  snapshot,
  source,
  issued = true,
}: {
  snapshot: NonNullable<Snapshot<unknown>>;
  source: string;
  issued?: boolean;
}) {
  return (
    <p className={`forecast-meta${snapshot.stale ? " stale" : ""}`}>
      {source}
      {issued && snapshot.issued_at ? ` · elaborada ${stamp.format(new Date(snapshot.issued_at))}` : ""}
      {` · recogida ${stamp.format(new Date(snapshot.fetched_at))}`}
      {snapshot.stale ? " · sin actualizar recientemente" : ""}
    </p>
  );
}

function Missing({ what }: { what: string }) {
  return <p className="forecast-empty">{what}: todavía no hay datos. El worker los descargará en su próximo ciclo.</p>;
}

function mainPeriod(periods: DailyPeriod[]) {
  return periods.find((p) => p.period === "00-24" && p.sky_code) ??
    periods.find((p) => p.period === "12-24" && p.sky_code) ??
    periods.find((p) => p.sky_code) ??
    periods[0];
}

function AemetDays({ snapshot }: { snapshot: NonNullable<Snapshot<AemetDaily>> }) {
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: zone });
  return (
    <>
      <div className="forecast-days">
        {snapshot.data.days
          .filter((day) => day.date >= today)
          .map((day) => {
            const main = mainPeriod(day.periods);
            // AEMET blanks elapsed periods of today; then the main period summarises the rest.
            const fullDay = day.periods.find((p) => p.period === "00-24");
            const whole = fullDay?.sky_code ? fullDay : main;
            const quarters = day.periods.filter((p) => ["00-06", "06-12", "12-18", "18-24"].includes(p.period));
            const date = new Date(`${day.date}T00:00:00Z`);
            return (
              <article className="forecast-day" key={day.date}>
                <h4>
                  {day.date === today ? "Hoy" : weekday.format(date)} <span>{shortDay.format(date)}</span>
                </h4>
                <div className="forecast-sky" title={main?.sky ?? ""}>
                  <span aria-hidden="true">{skyIcon(main?.sky_code)}</span>
                  <small>{main?.sky ?? "Sin estado del cielo"}</small>
                </div>
                <div className="forecast-temps">
                  <strong className="max">{num(day.temperature.max, "°")}</strong>
                  <span className="min">{num(day.temperature.min, "°")}</span>
                </div>
                <dl>
                  <dt>Prob. precipitación</dt>
                  <dd>{num(whole?.precipitation_probability, " %")}</dd>
                  <dt>Viento</dt>
                  <dd>
                    {whole?.wind_direction ?? ""} {num(whole?.wind_speed, " km/h")}
                  </dd>
                  {whole?.gust != null && (
                    <>
                      <dt>Racha máx.</dt>
                      <dd>{num(whole.gust, " km/h")}</dd>
                    </>
                  )}
                  {whole?.snow_level != null && (
                    <>
                      <dt>Cota de nieve</dt>
                      <dd>{num(whole.snow_level, " m")}</dd>
                    </>
                  )}
                  {day.uv_max != null && (
                    <>
                      <dt>UV máx.</dt>
                      <dd>{num(day.uv_max)}</dd>
                    </>
                  )}
                </dl>
                {quarters.some((q) => q.sky_code || q.precipitation_probability != null) && (
                  <ol className="forecast-quarters" aria-label="Por franjas de 6 horas">
                    {quarters.map((q) => (
                      <li key={q.period} title={q.sky ?? ""}>
                        <small>{q.period.replace("-", "–")}</small>
                        <span aria-hidden="true">{skyIcon(q.sky_code, q.period === "00-06")}</span>
                        <small>{num(q.precipitation_probability, "%")}</small>
                      </li>
                    ))}
                  </ol>
                )}
              </article>
            );
          })}
      </div>
    </>
  );
}

function AemetHours({ snapshot }: { snapshot: NonNullable<Snapshot<AemetHourly>> }) {
  const from = Date.now() - 3600000;
  const hours = snapshot.data.hours.filter((h) => Date.parse(h.time) >= from);
  if (!hours.length) return <p className="forecast-empty">La predicción horaria recogida ya ha caducado.</p>;
  return (
    <div className="forecast-hours" tabIndex={0} role="region" aria-label="Predicción por horas">
      <table>
        <thead>
          <tr>
            <th scope="row">Hora</th>
            {hours.map((h, i) => {
              const newDay = i === 0 || dayOfHour.format(new Date(h.time)) !== dayOfHour.format(new Date(hours[i - 1].time));
              return (
                <th key={h.time} scope="col" className={newDay ? "new-day" : ""}>
                  {newDay && <small>{dayOfHour.format(new Date(h.time))}</small>}
                  {hourOnly.format(new Date(h.time))}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Cielo</th>
            {hours.map((h) => (
              <td key={h.time} title={h.sky ?? ""} className="icon">
                {skyIcon(h.sky_code, h.night)}
              </td>
            ))}
          </tr>
          <tr>
            <th scope="row">Temp. °C</th>
            {hours.map((h) => <td key={h.time} className="strong">{num(h.temperature)}</td>)}
          </tr>
          <tr>
            <th scope="row">Sensación °C</th>
            {hours.map((h) => <td key={h.time}>{num(h.feels_like)}</td>)}
          </tr>
          <tr>
            <th scope="row">Lluvia mm</th>
            {hours.map((h) => <td key={h.time}>{amount(h.precipitation, h.precipitation_trace)}</td>)}
          </tr>
          {hours.some((h) => (h.snow ?? 0) > 0 || h.snow_trace) && (
            <tr>
              <th scope="row">Nieve mm</th>
              {hours.map((h) => <td key={h.time}>{amount(h.snow, h.snow_trace)}</td>)}
            </tr>
          )}
          <tr>
            <th scope="row">Viento km/h</th>
            {hours.map((h) => (
              <td key={h.time}>
                <small>{h.wind_direction ?? ""}</small> {num(h.wind_speed)}
              </td>
            ))}
          </tr>
          <tr>
            <th scope="row">Racha km/h</th>
            {hours.map((h) => <td key={h.time}>{num(h.gust)}</td>)}
          </tr>
          <tr>
            <th scope="row">Humedad %</th>
            {hours.map((h) => <td key={h.time}>{num(h.humidity)}</td>)}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function ModelTable({ model }: { model: ModelForecast }) {
  const rain = new Map(model.accumulated_6h.time.map((t, i) => [t, i]));
  const rows = model.time.filter((t) => t % 21600 === 0);
  return (
    <details className="forecast-data">
      <summary>Ver datos en tabla (cada 6 h)</summary>
      <div className="forecast-table-scroll">
        <table>
          <thead>
            <tr>
              <th>Hora oficial</th>
              <th>T 2 m °C</th>
              <th>Rocío °C</th>
              <th>T 850 hPa °C</th>
              <th>Precip. 6 h mm</th>
              <th>Presión hPa</th>
              <th>Viento km/h</th>
              <th>Racha km/h</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => {
              const i = model.time.indexOf(t);
              const r = rain.get(t);
              return (
                <tr key={t}>
                  <td>{stamp.format(new Date(t * 1000))}</td>
                  <td>{num(model.series.temperature_2m[i])}</td>
                  <td>{num(model.series.dew_point_2m[i])}</td>
                  <td>{num(model.series.temperature_850hPa[i])}</td>
                  <td>{r === undefined ? "—" : num(model.accumulated_6h.precipitation[r])}</td>
                  <td>{num(model.series.pressure_msl[i])}</td>
                  <td>{num(model.series.wind_speed_10m[i])}</td>
                  <td>{num(model.series.wind_gusts_10m[i])}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function span(times: number[][]): [number, number] {
  const all = times.flat();
  return all.length ? [Math.min(...all) * 1000, Math.max(...all) * 1000] : [0, 1];
}

function ModelSection({ place }: { place: Place }) {
  const models = ["gfs", "ecmwf"].map((code) => [code, place.models[code]] as const);
  // One time range for both models, so their horizons compare at a glance.
  const range = useMemo(
    () => span(models.flatMap(([, s]) => (s ? [s.data.time] : []))),
    [place],
  );
  return (
    <section className="forecast-section" aria-labelledby={`models-${place.code}`}>
      <h3 id={`models-${place.code}`}>Meteogramas GFS y ECMWF</h3>
      <p className="forecast-note">
        Punto de rejilla más cercano. Temperatura, presión, viento y nubes cada 3 h; precipitación
        acumulada en ventanas de 6 h (00, 06, 12 y 18 UTC). La línea discontinua vertical marca el momento actual.
      </p>
      <div className="forecast-models">
        {models.map(([code, snapshot]) => (
          <article className="forecast-panel" key={code}>
            {snapshot ? (
              <>
                <h4>{snapshot.data.label}</h4>
                <Freshness snapshot={snapshot} source={`Open-Meteo · ${runLabel(snapshot.data.run)}`} issued={false} />
                <Suspense fallback={<p className="forecast-empty">Dibujando meteograma…</p>}>
                  <Meteogram model={snapshot.data} range={range} />
                </Suspense>
                <p className="forecast-grid">
                  Rejilla: {num(snapshot.data.grid.latitude)}°, {num(snapshot.data.grid.longitude)}° · altitud
                  del modelo {num(snapshot.data.grid.elevation, " m")}
                </p>
                <ModelTable model={snapshot.data} />
              </>
            ) : (
              <Missing what={code === "gfs" ? "GFS" : "ECMWF"} />
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function EnsembleSection({ place }: { place: Place }) {
  const ensembles = ["gfs", "ecmwf"].map((code) => [code, place.ensembles?.[code] ?? null] as const);
  const range = useMemo(
    () => span(ensembles.flatMap(([, s]) => (s ? [s.data.temperature_850hPa.time] : []))),
    [place],
  );
  return (
    <section className="forecast-section" aria-labelledby={`ens-${place.code}`}>
      <h3 id={`ens-${place.code}`}>Conjuntos (ensembles): temperatura a 850 hPa y precipitación</h3>
      <p className="forecast-note">
        Cada línea gris es un miembro. La media solo se calcula cuando están todos los miembros;
        la precipitación es la media de los miembros en ventanas de 6 h, con el valor del miembro más lluvioso.
      </p>
      <div className="forecast-models">
        {ensembles.map(([code, snapshot]) => (
          <article className="forecast-panel" key={code}>
            {snapshot ? (
              <>
                <h4>{snapshot.data.label}</h4>
                <Freshness snapshot={snapshot} source={`Open-Meteo · ${runLabel(snapshot.data.run)}`} issued={false} />
                <Suspense fallback={<p className="forecast-empty">Dibujando conjunto…</p>}>
                  <EnsembleChart ensemble={snapshot.data} range={range} />
                </Suspense>
              </>
            ) : (
              <Missing what={code === "gfs" ? "GEFS" : "ECMWF ENS"} />
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

export default function ForecastPage({
  place: requested,
  onPlace,
  refresh,
}: {
  place: string | null;
  onPlace: (code: string) => void;
  refresh: number;
}) {
  const [data, setData] = useState<Forecasts | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    if (document.hidden) return () => controller.abort();
    void api<Forecasts>("/api/v1/forecasts", controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return;
        setData(next);
        setError("");
      })
      .catch((e: Error) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
    // Forecasts change at most hourly; the 1-minute app tick is enough.
  }, [Math.floor(refresh / 10)]);
  const place = data?.locations.find((p) => p.code === requested) ?? data?.locations[0];
  return (
    <div className="forecast-page">
      <section className="workspace-heading">
        <div>
          <div className="eyebrow">PREVISIONES</div>
          <h1>Previsión para {place?.name ?? "Madrid y Huétor de Santillán"}</h1>
        </div>
        <div className="refresh-status" role="status">
          {error ? error : data ? "Previsiones de AEMET y de los modelos GFS y ECMWF" : "Consultando previsiones…"}
        </div>
      </section>
      {data && place && (
        <>
          <div className="forecast-places" role="tablist" aria-label="Localidad">
            {data.locations.map((p) => (
              <button
                key={p.code}
                role="tab"
                aria-selected={p.code === place.code}
                className={p.code === place.code ? "active" : ""}
                onClick={() => onPlace(p.code)}
              >
                {p.name}
              </button>
            ))}
          </div>
          <section className="forecast-section" aria-labelledby={`aemet-${place.code}`}>
            <h3 id={`aemet-${place.code}`}>
              Predicción AEMET{" "}
              <a href={place.aemet_url} target="_blank" rel="noreferrer">
                ver en aemet.es
              </a>
            </h3>
            {place.aemet.daily ? (
              <>
                <Freshness snapshot={place.aemet.daily} source="AEMET" />
                <AemetDays snapshot={place.aemet.daily} />
              </>
            ) : (
              <Missing what="Predicción diaria de AEMET" />
            )}
            <h3 className="forecast-subheading">Próximas horas</h3>
            {place.aemet.hourly ? (
              <AemetHours snapshot={place.aemet.hourly} />
            ) : (
              <Missing what="Predicción horaria de AEMET" />
            )}
          </section>
          <ModelSection place={place} key={`m-${place.code}`} />
          {place.ensembles && <EnsembleSection place={place} key={`e-${place.code}`} />}
          <footer className="forecast-attribution">
            {data.attribution.map((item) => (
              <p key={item.source}>
                <a href={item.url} target="_blank" rel="noreferrer">
                  {item.text}
                </a>
              </p>
            ))}
            <p>
              Las horas se muestran en hora oficial peninsular. Las previsiones no se mezclan con las
              observaciones ni con los históricos.
            </p>
          </footer>
        </>
      )}
    </div>
  );
}
