export type Reading = {
  metric: string;
  value: number | null;
  unit: string;
  kind: string;
  period_start: string | null;
  period_end: string | null;
  period_basis: string | null;
  observed_at: string;
  fetched_at: string;
  age_seconds: number;
  stale_after_seconds: number;
  freshness: string;
  source_id: string;
  provider: string;
  external_id: string;
  product: string;
  direction_degrees: number | null;
  flags: string[];
};
export type Source = {
  id: string;
  provider: string;
  external_id: string;
  source_url: string | null;
  coordinate_precision: string | null;
  attribution: string;
  provider_status: string;
};
export type Station = {
  id: string;
  name: string;
  municipality: string | null;
  province_code: string;
  latitude: number | null;
  longitude: number | null;
  altitude_m: number | null;
  sources: Source[];
  reading: Reading | null;
  freshness: string;
  fallback: boolean;
};
export type Current = Station & { readings: Reading[]; generated_at: string };
export type Extreme = { station_id: string; name: string; reading: Reading };
export type MapPage = {
  items: Station[];
  total: number;
  registered: number;
  counts: Record<string, number>;
  truncated: boolean;
  generated_at: string;
  extremes: {
    comparable: boolean;
    eligible: number;
    population: number;
    reason: string | null;
    minimum: Extreme | null;
    maximum: Extreme | null;
    observed_from: string | null;
    observed_to: string | null;
  };
};
export type Provider = {
  code: string;
  name: string;
  status: string;
  attribution: string;
  license_url: string | null;
};
export const provinces: Record<string, string> = {
  "28": "Madrid",
  "05": "Ávila",
  "40": "Segovia",
  "19": "Guadalajara",
};
export const freshnessLabels: Record<string, string> = {
  fresh: "Reciente",
  stale: "Desactualizada",
  unknown: "Sin dato utilizable",
  historical_only: "Solo archivo histórico",
};
export const providerLabels: Record<string, string> = {
  verified: "Habilitada",
  paused: "Recogida pausada",
  disabled: "Deshabilitada",
  pending_terms: "Permiso pendiente",
  pending_access: "Acceso pendiente",
  deferred_cost: "Aplazada",
};
export const metricInfo: Record<
  string,
  {
    label: string;
    short: string;
    unit: string;
    scale: number[];
    colors: string[];
    period: string;
  }
> = {
  temperature: {
    label: "Temperatura",
    short: "Temperatura",
    unit: "°C",
    scale: [-5, 5, 15, 25, 35],
    colors: ["#514099", "#17629b", "#11745f", "#a05113", "#b7283a"],
    period: "Instantánea · hora de cada estación",
  },
  humidity: {
    label: "Humedad relativa",
    short: "Humedad",
    unit: "%",
    scale: [20, 40, 60, 80, 100],
    colors: ["#965710", "#8a611c", "#347052", "#146f91", "#3f4aa0"],
    period: "Instantánea · hora de cada estación",
  },
  wind_speed: {
    label: "Velocidad del viento",
    short: "Viento",
    unit: "km/h",
    scale: [0, 10, 25, 50, 80],
    colors: ["#546475", "#187787", "#2756a2", "#74469a", "#b32c60"],
    period: "AEMET: media de 10 min · Meteoclimatic: instantánea",
  },
  rain: {
    label: "Precipitación del intervalo",
    short: "Lluvia",
    unit: "mm",
    scale: [0, 1, 5, 15, 30],
    colors: ["#586677", "#16718b", "#2a589e", "#6746a4", "#ae2867"],
    period: "Total del intervalo · AEMET: 60 min anteriores",
  },
  wind_gust: {
    label: "Racha del intervalo",
    short: "Racha",
    unit: "km/h",
    scale: [0, 10, 25, 50, 80],
    colors: ["#546475", "#187787", "#2756a2", "#74469a", "#b32c60"],
    period: "Máxima del intervalo indicado · no racha diaria",
  },
  rain_rate: {
    label: "Intensidad de lluvia",
    short: "Intensidad",
    unit: "mm/h",
    scale: [0, 1, 5, 15, 30],
    colors: ["#586677", "#16718b", "#2a589e", "#6746a4", "#ae2867"],
    period: "Tasa reportada por la fuente",
  },
};
export const metricNames: Record<string, string> = {
  ...Object.fromEntries(
    Object.entries(metricInfo).map(([k, v]) => [k, v.label]),
  ),
  pressure_station: "Presión de estación",
  pressure_sea_level: "Presión al nivel del mar",
  wind_direction: "Dirección del viento",
  rain_daily: "Contador diario de lluvia",
  temperature_daily_min: "Mínima diaria reportada",
  temperature_daily_max: "Máxima diaria reportada",
  humidity_daily_min: "Humedad mínima diaria",
  humidity_daily_max: "Humedad máxima diaria",
  wind_speed_daily_max: "Máxima diaria de viento",
  pressure_sea_level_daily_min: "Presión mínima diaria",
  pressure_sea_level_daily_max: "Presión máxima diaria",
};
export function number(value: number | null | undefined) {
  return value == null
    ? "—"
    : new Intl.NumberFormat("es-ES", {
        maximumFractionDigits: 1,
        minimumFractionDigits: 1,
      }).format(value);
}
export function date(value: string | null | undefined) {
  return value
    ? new Intl.DateTimeFormat("es-ES", {
        timeZone: "Europe/Madrid",
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(new Date(value))
    : "Sin observación";
}
export function age(value: Reading) {
  const min = Math.floor(value.age_seconds / 60);
  return min < 60
    ? `${min} min`
    : min < 1440
      ? `${Math.floor(min / 60)} h ${min % 60} min`
      : `${Math.floor(min / 1440)} días`;
}
export function period(value: Reading) {
  if (value.period_basis === "provider_day_timezone_unknown")
    return "Diario reportado · horario de reinicio desconocido; no comparable";
  if (value.period_start && value.period_end)
    return `${value.kind === "interval_mean" ? "Media" : "Total / intervalo"}: ${date(value.period_start)} → ${date(value.period_end)}`;
  if (value.kind === "rate") return "Intensidad reportada (tasa)";
  return "Instantánea a la hora de observación";
}
export function color(value: number | null | undefined, metric: string) {
  const info = metricInfo[metric];
  if (value == null || !info) return "#626e80";
  let index = 0;
  info.scale.forEach((n, i) => {
    if (value >= n) index = i;
  });
  return info.colors[index];
}
export async function api<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    cache: "no-store",
  });
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? "Estación no disponible. Puede estar excluida o no ser publicable."
        : "No se pudo consultar la API. Reintenta en unos instantes.",
    );
  return response.json() as Promise<T>;
}
