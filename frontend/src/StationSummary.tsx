import { date, number, type Current } from "./data";

const madridDay = (value: string) =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Madrid",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));

export default function StationSummary({
  station,
  source,
}: {
  station: Current;
  source: string;
}) {
  const today = madridDay(station.generated_at);
  const readings = station.readings.filter((r) => !source || r.source_id === source);
  const metrics = [
    { label: "Mínima", reported: "temperature_daily_min", metric: "temperature", field: "minimum", unit: "°C" },
    { label: "Máxima", reported: "temperature_daily_max", metric: "temperature", field: "maximum", unit: "°C" },
    { label: "Precipitación", reported: "rain_daily", metric: "rain", field: "total", unit: "mm" },
  ] as const;
  return (
    <section className="station-summary" aria-label="Resumen del día">
      <h3>Resumen del día <span>{date(station.generated_at).split(",")[0]}</span></h3>
      <div className="daily-grid">
        {metrics.map(({ label, reported, metric, field, unit }) => {
          const report = readings.find((r) =>
            r.metric === reported && r.value != null &&
            madridDay(r.observed_at) === today &&
            new Date(r.observed_at) <= new Date(station.generated_at),
          );
          const origin = source || report?.source_id || readings.find((r) => r.metric === metric && r.value != null)?.source_id;
          const candidates = (station.day_summaries ?? []).filter((r) =>
            r.source_id === origin && r.metric === metric && r.unit === unit &&
            madridDay(r.period_start) === today,
          );
          // Ambiguous channels must never be silently combined.
          const local = candidates.length === 1 ? candidates[0] : undefined;
          // Preserve reported values; approximate occurrence times from the same
          // source's archived extrema, never from the report's publication time.
          const value = report ? report.value : local?.[field];
          const provider = report?.provider ?? local?.provider;
          const observed = metric === "temperature"
            ? local?.[field === "minimum" ? "minimum_at" : "maximum_at"]
            : report?.observed_at ?? local?.observed_at;
          return (
            <article className="daily-stat" key={field}>
              <h4>{label}</h4>
              <strong>{number(value)} <span>{unit}</span></strong>
              <small>{value == null ? "Sin datos de hoy" : report ? "Reportada" : "Archivo de hoy"}</small>
              {provider && value != null && <small>{provider.toUpperCase()}</small>}
              {value != null && <small>{observed
                ? `${metric === "temperature" ? "Hora aprox." : "Actualizada"}: ${date(observed)}`
                : "Hora del extremo no disponible"}</small>}
              {local && value != null && (metric === "temperature" || !report) && (
                <small>{local.partial ? "Parcial" : "Hasta ahora"} · cobertura {Math.round(local.coverage * 100)} %</small>
              )}
            </article>
          );
        })}
      </div>
      <p className="daily-context">
        Horas aproximadas de mínima y máxima según nuestro archivo de hoy de la misma fuente, en hora de Madrid. Pueden diferir del instante real del extremo.{" "}
        {readings.some((r) => metrics.some((m) => m.reported === r.metric) &&
          r.value != null && madridDay(r.observed_at) === today &&
          new Date(r.observed_at) <= new Date(station.generated_at))
          ? "Diarios reportados por la fuente: horario de reinicio desconocido."
          : "Día en curso · hora de Madrid. Los huecos del archivo no se rellenan."}
      </p>
      <div className="quick-readings">
        {[
          ["humidity", "Humedad"],
          ["wind_speed", "Viento"],
        ].map(([metric, label]) => {
          const reading = readings.find((r) => r.metric === metric);
          return reading && (
            <div key={metric}>
              <span>{label}</span>
              <strong>{number(reading.value)} {reading.unit}</strong>
              <small>{reading.provider.toUpperCase()} · {date(reading.observed_at)}{reading.freshness === "stale" ? " · Desactualizada" : ""}</small>
            </div>
          );
        })}
      </div>
    </section>
  );
}
