import type { Page } from "@playwright/test";
import type { Current, Reading, Station } from "../src/data";
export const observed = "2026-09-19T08:00:00+00:00";
export function reading(index: number, metric = "temperature"): Reading {
  return {
    metric,
    value:
      metric === "temperature"
        ? (index % 45) - 5
        : metric === "humidity"
          ? index % 100
          : index % 20,
    unit:
      metric === "temperature"
        ? "°C"
        : metric === "humidity"
          ? "%"
          : metric === "rain"
            ? "mm"
            : "km/h",
    kind: metric === "rain" ? "interval_total" : "instant",
    observed_at: observed,
    fetched_at: "2026-09-19T08:03:00+00:00",
    period_start: metric === "rain" ? "2026-09-19T07:00:00+00:00" : null,
    period_end: metric === "rain" ? observed : null,
    period_basis: metric === "rain" ? "preceding_60_minutes_UTC" : null,
    age_seconds: 600,
    stale_after_seconds: 5400,
    freshness: index === 4 ? "stale" : "fresh",
    source_id: `source-${index}`,
    provider: "aemet",
    external_id: `SYN-${index}`,
    product: "SINTÉTICO · prueba de interfaz",
    direction_degrees: metric === "wind_speed" ? 90 : null,
    flags: [],
  };
}
export function station(index: number, metric: string): Station {
  const provinces = ["28", "05", "40", "19"];
  const centers = [
    [-3.7, 40.4],
    [-4.7, 40.65],
    [-4.1, 41],
    [-2.7, 40.9],
  ];
  const center = centers[index % 4];
  return {
    id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    name: `SINTÉTICA ${String(index).padStart(4, "0")} · ${["Madrid Retiro", "Ávila", "Segovia", "Guadalajara"][index % 4]}`,
    municipality: null,
    province_code: provinces[index % 4],
    longitude: center[0] + Math.sin(index * 137.5) * 0.7,
    latitude: center[1] + Math.cos(index * 137.5) * 0.45,
    altitude_m: 600 + (index % 900),
    reading: index === 5 ? null : reading(index, metric),
    sources: [
      {
        id: `source-${index}`,
        provider: "aemet",
        external_id: `SYN-${index}`,
        source_url: "https://www.aemet.es/es/eltiempo/observacion/ultimosdatos",
        coordinate_precision: null,
        attribution: "AEMET",
        provider_status: "verified",
      },
    ],
    freshness: index === 4 ? "stale" : index === 5 ? "unknown" : "fresh",
    fallback: false,
  };
}
export async function mockApi(
  page: Page,
  count = 32,
  tiles: "ok" | "error" | "real" = "ok",
) {
  const state = {
    excluded: new Set<string>(),
    fail: false,
    requests: [] as string[],
    current: {} as Partial<Current>,
    mapReadings: new Map<number, Partial<Reading> | null>(),
  };
  if (tiles !== "real")
    await page.route("https://www.ign.es/**", (route) =>
      tiles === "error"
        ? route.abort()
        : route.fulfill({
            contentType: "image/png",
            body: Buffer.from(
              "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAIAAADTED8xAAACv0lEQVR4nO3TMQ0AMAzAsPJH2msYBqNHLBlAnsy+haw5L4BDBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxA2gcxEikj38cjQwAAAABJRU5ErkJggg==",
              "base64",
            ),
          }),
    );
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    state.requests.push(url.pathname + url.search);
    if (url.pathname === "/api/v1/auth/session")
      return route.fulfill({
        json: { authenticated: false, private_read: false },
      });
    if (state.fail)
      return route.fulfill({ status: 503, json: { detail: "test failure" } });
    if (url.pathname.endsWith("/providers"))
      return route.fulfill({
        json: {
          items: [
            {
              code: "aemet",
              name: "AEMET",
              status: "verified",
              attribution: "AEMET",
              license_url: null,
            },
            {
              code: "meteoclimatic",
              name: "Meteoclimatic",
              status: "pending_access",
              attribution: "Meteoclimatic y sus colaboradores",
              license_url: "https://creativecommons.org/licenses/by-nc-nd/3.0/",
            },
          ],
        },
      });
    if (url.pathname.endsWith("/current")) {
      const id = url.pathname.split("/")[4],
        index = Number(id.split("-").at(-1));
      if (state.excluded.has(id) || !Number.isInteger(index) || index >= count)
        return route.fulfill({ status: 404, json: {} });
      return route.fulfill({
        json: {
          ...station(index, "temperature"),
          generated_at: observed,
          readings: ["temperature", "humidity", "wind_speed", "rain"].map((m) =>
            reading(index, m),
          ),
          ...state.current,
        },
      });
    }
    const metric = url.searchParams.get("metric") || "temperature";
    const items = Array.from({ length: count }, (_, i) => {
      const item = station(i, metric);
      if (state.mapReadings.has(i)) {
        const override = state.mapReadings.get(i);
        item.reading = override === null ? null : { ...reading(i, metric), ...override };
      }
      return item;
    }).filter(
      (s) =>
        !state.excluded.has(s.id) &&
        (!url.searchParams.get("network") || s.sources.some((source) => source.provider === url.searchParams.get("network"))) &&
        (!url.searchParams.get("province") ||
          url.searchParams.get("province") === s.province_code) &&
        (!url.searchParams.get("q") ||
          s.name
            .toLowerCase()
            .includes(url.searchParams.get("q")!.toLowerCase())) &&
        (["all", null].includes(url.searchParams.get("freshness")) ||
          s.freshness === url.searchParams.get("freshness")),
    );
    return route.fulfill({
      json: {
        items,
        total: items.length,
        registered: items.length,
        counts: {
          fresh: items.filter((s) => s.freshness === "fresh").length,
          stale: items.filter((s) => s.freshness === "stale").length,
          unknown: 1,
        },
        truncated: false,
        generated_at: observed,
        extremes: {
          comparable: false,
          eligible: items.filter((s) => s.freshness === "fresh").length,
          population: items.length,
          reason: "different_periods",
          minimum: null,
          maximum: null,
          observed_from: observed,
          observed_to: observed,
        },
      },
    });
  });
  return state;
}
