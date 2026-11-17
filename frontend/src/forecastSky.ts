const localHour = new Intl.DateTimeFormat("es-ES", { hour: "numeric", hourCycle: "h23", timeZone: "Europe/Madrid" });

/** Rough night for symbols in official time; not an astronomical sunset. */
export function isNight(ms: number) {
  const hour = Number(localHour.format(ms));
  return hour < 7 || hour >= 21;
}

/** Cloudiness for symbols from model layers; high cloud counts less. */
export function cloudiness(low: number | null | undefined, mid: number | null | undefined, high: number | null | undefined) {
  if (low == null || mid == null || high == null) return null;
  return Math.max(low, mid, high * 0.6);
}

/** A sky symbol for a time window from model cloudiness and precipitation (mm, snow cm). */
export function skySymbol(cloud: number | null, rain: number | null, snow: number | null, middle: number) {
  if (cloud === null || rain === null) return "";
  const night = isNight(middle);
  if (rain >= 0.5) return (snow ?? 0) >= 0.5 ? "🌨️" : "🌧️";
  if (rain >= 0.1) return night ? "🌧️" : "🌦️";
  if (cloud < 20) return night ? "🌙" : "☀️";
  if (cloud < 50) return night ? "🌙" : "🌤️";
  if (cloud < 80) return "⛅";
  return "☁️";
}
