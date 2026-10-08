import { test, expect, type Page } from "@playwright/test";
import { mockApi } from "./fixtures";

// SINTÉTICO: shapes follow /api/v1/forecasts; values are invented for the interface.
const HOUR = 3600;
const start = Math.floor(Date.now() / 1000 / 21600) * 21600 - 21600;
const steps = (every: number, count: number) =>
  Array.from({ length: count }, (_, i) => start + (i + 1) * every);
const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Madrid" });
const nextDay = (n: number) =>
  new Date(Date.parse(`${today}T12:00:00Z`) + n * 86400000).toISOString().slice(0, 10);

function model(code: string, label: string) {
  const time = steps(3 * HOUR, 40);
  const wave = (base: number) => time.map((_, i) => base + Math.round(Math.sin(i / 3) * 50) / 10);
  const rain = steps(6 * HOUR, 20);
  return {
    model: code,
    label,
    run: new Date(start * 1000).toISOString(),
    grid: { latitude: 40.5, longitude: -3.75, elevation: 666 },
    time,
    series: {
      temperature_2m: wave(18),
      dew_point_2m: wave(8),
      temperature_850hPa: wave(12),
      pressure_msl: wave(1018),
      wind_speed_10m: wave(12),
      wind_gusts_10m: wave(25),
      wind_direction_10m: wave(180),
      cloud_cover_low: wave(20),
      cloud_cover_mid: wave(40),
      cloud_cover_high: wave(60),
    },
    accumulated_6h: {
      time: rain,
      precipitation: rain.map((_, i) => (i === 3 ? null : i % 4)),
      snowfall: rain.map(() => 0),
    },
  };
}

function ensemble(code: string, label: string, members: number) {
  const t = steps(6 * HOUR, 20);
  const rows = Array.from({ length: members }, (_, m) => t.map((_, i) => 10 + m / 10 + i / 5));
  const rain = Array.from({ length: members }, (_, m) => t.map(() => m % 3));
  return {
    model: code,
    label,
    run: null,
    grid: { latitude: 40.5, longitude: -3.75, elevation: 666 },
    members,
    temperature_850hPa: { time: t, members: rows, mean: t.map((_, i) => 11 + i / 5) },
    precipitation_6h: { time: t, members: rain, mean: t.map(() => 1) },
  };
}

const snapshot = <T,>(data: T, stale = false) => ({
  issued_at: new Date(start * 1000).toISOString(),
  fetched_at: new Date().toISOString(),
  stale,
  data,
});

// Official-time instant of a day offset, as AEMET writes it (+02:00 in October).
const at = (n: number, time: string) => `${nextDay(n)}T${time}+02:00`;

function warnings(code: string) {
  const rain = {
    phenomenon: "Lluvias",
    phenomenon_code: "PR",
    level: "amarillo",
    onset: at(1, "22:00:00"),
    expires: at(2, "05:59:59"),
    threshold: "Precipitación acumulada en una hora: 15 mm",
    probability: "40%-70%",
    description: "Precipitación acumulada en una hora: 15 mm. Chubascos tormentosos.",
    instruction: "Esté atento.",
  };
  const heat = {
    ...rain,
    phenomenon: "Temperaturas máximas",
    phenomenon_code: "AT",
    level: "naranja",
    onset: at(1, "13:00:00"),
    expires: at(1, "20:59:59"),
    threshold: "Temperatura máxima: 39 ºC",
    description: null,
  };
  const madrid = code === "madrid";
  return snapshot(
    {
      zone: madrid ? "722802" : "611801",
      zone_name: madrid ? "Metropolitana y Henares" : "Cuenca del Genil",
      link: "https://www.aemet.es/es/eltiempo/prediccion/avisos?w=hoy&l=722802",
      days: madrid
        ? [
            { date: nextDay(0), level: "verde", warnings: [] },
            { date: nextDay(1), level: "naranja", warnings: [heat, rain] },
            { date: nextDay(2), level: "amarillo", warnings: [rain] },
          ]
        : [0, 1, 2].map((n) => ({ date: nextDay(n), level: null, warnings: [] })),
    },
    !madrid,
  );
}

function place(code: string, name: string, withEnsemble: boolean) {
  const hours = Array.from({ length: 30 }, (_, i) => ({
    time: new Date((Math.floor(Date.now() / 3600000) + i) * 3600000).toISOString(),
    sky_code: i % 2 ? "43" : "11",
    sky: i % 2 ? "Intervalos nubosos con lluvia escasa" : "Despejado",
    night: false,
    temperature: 20 - i / 2,
    feels_like: 19,
    humidity: 50,
    precipitation: i === 1 ? null : 0,
    precipitation_trace: i === 1,
    snow: null,
    snow_trace: false,
    wind_direction: "NE",
    wind_speed: 12,
    gust: 20,
  }));
  return {
    code,
    name,
    latitude: 40.4,
    longitude: -3.7,
    aemet_url: `https://www.aemet.es/es/eltiempo/prediccion/municipios/${code}`,
    aemet: {
      daily: snapshot(
        {
          issued_at: new Date().toISOString(),
          link: "",
          days: [0, 1, 2].map((n) => ({
            date: nextDay(n),
            periods: [
              {
                period: "00-24",
                sky_code: n === 1 ? "25" : "12",
                sky: n === 1 ? "Muy nuboso con lluvia" : "Poco nuboso",
                precipitation_probability: n === 1 ? 90 : 0,
                snow_level: n === 2 ? 1800 : null,
                wind_direction: "SO",
                wind_speed: 15,
                gust: null,
              },
            ],
            temperature: { max: 31 - n, min: n === 2 ? null : 17, at: [] },
            feels_like: { max: null, min: null },
            humidity: { max: null, min: null },
            uv_max: n === 0 ? 6 : null,
          })),
        },
        code !== "madrid",
      ),
      hourly: snapshot({ issued_at: "", link: "", hours, windows: [] }),
    },
    warnings: warnings(code),
    models: {
      gfs: snapshot(model("gfs", "GFS (NOAA) 0,25°")),
      ecmwf: code === "madrid" ? snapshot(model("ecmwf", "ECMWF IFS 0,25°")) : null,
    },
    climate_850hPa: {
      period: "1991-2020",
      dataset: "SINTÉTICO",
      values: Array.from({ length: 365 }, (_, i) => 11 + 7 * Math.sin(((i - 110) / 365) * 2 * Math.PI)),
    },
    ensembles: withEnsemble
      ? {
          gfs: snapshot(ensemble("gfs", "GEFS (NOAA) 0,5° · 31 miembros", 31)),
          ecmwf: snapshot(ensemble("ecmwf", "ECMWF ENS 0,25° · 51 miembros", 51)),
        }
      : null,
  };
}

async function mockForecasts(page: Page) {
  const state = await mockApi(page, 3);
  await page.route("**/api/v1/forecasts", (route) => {
    state.requests.push("/api/v1/forecasts");
    return route.fulfill({
      json: {
        locations: [
          place("madrid", "Madrid", true),
          place("huetor-santillan", "Huétor de Santillán", true),
        ],
        attribution: [
          { source: "aemet", text: "© AEMET.", url: "https://www.aemet.es/es/nota_legal" },
          { source: "open_meteo", text: "Open-Meteo.com (CC BY 4.0).", url: "https://open-meteo.com/" },
        ],
      },
    });
  });
  return state;
}

for (const width of [1440, 390]) {
  test(`previsiones ${width}: AEMET, meteogramas, conjuntos y cambio de localidad`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await mockForecasts(page);
    await page.goto("/");
    // Second tab, right after the map.
    await expect(page.getByRole("navigation", { name: "Principal" }).getByRole("link")).toHaveText([
      "Mapa",
      "Previsiones",
      "Estaciones",
      "Datos diarios",
      "Históricos",
    ]);
    await page.getByRole("link", { name: "Previsiones" }).click();
    await expect(page).toHaveURL(/\/previsiones/);
    await expect(page.getByRole("heading", { name: "Previsión para Madrid" })).toBeVisible();
    // The map may legitimately query before the click on a slow runner; only
    // requests made while the forecast tab is open count.
    const fromForecast = state.requests.length;
    const days = page.locator(".forecast-day");
    await expect(days).toHaveCount(3);
    await expect(days.first()).toContainText("Hoy");
    await expect(days.first()).toContainText("31°");
    // A missing minimum stays a dash, never zero.
    await expect(days.nth(2).locator(".min")).toHaveText("—");
    await expect(days.nth(2)).toContainText("1800 m");
    await expect(page.locator(".forecast-hours")).toContainText("Ip");
    // Warnings: green today, the worst level per day, hours in official time.
    const warningDays = page.locator(".warning-day");
    await expect(warningDays).toHaveCount(3);
    await expect(warningDays.first()).toHaveClass(/level-verde/);
    await expect(warningDays.first()).toContainText("Sin avisos");
    await expect(warningDays.nth(1)).toHaveClass(/level-naranja/);
    await expect(warningDays.nth(1)).toContainText("Temperaturas máximas");
    await expect(warningDays.nth(1)).toContainText("13:00–21:00");
    await expect(warningDays.nth(1)).toContainText("Temperatura máxima: 39 ºC");
    await expect(warningDays.nth(2)).toHaveClass(/level-amarillo/);
    await expect(warningDays.nth(2).locator(".warning-hours")).toContainText("06:00");
    await expect(warningDays.nth(2).locator(".warning-text")).toHaveText("Chubascos tormentosos.");
    await expect(page.getByText("Avisos · zona Metropolitana y Henares")).toBeVisible();
    await page.locator(".warning-days").screenshot({ path: `test-results/avisos-${width}.png` });
    await expect(page.locator(".forecast-chart svg")).toHaveCount(4);
    await expect(page.getByText("GEFS (NOAA) 0,5° · 31 miembros")).toBeVisible();
    await expect(page.locator(".forecast-chart svg text", { hasText: "Media 1991–2020" })).toHaveCount(2);
    await expect(page.getByRole("button", { name: "10 días" })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "16 días" }).click();
    await expect(page.getByRole("button", { name: "16 días" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".forecast-chart svg")).toHaveCount(4);
    await page.getByText("Ver datos en tabla (cada 6 h)").first().click();
    await expect(page.locator(".forecast-data table").first()).toBeVisible();
    expect(
      state.requests.slice(fromForecast).some((r) => r.startsWith("/api/v1/map")),
    ).toBe(false);

    await page.getByRole("tab", { name: "Huétor de Santillán" }).click();
    await expect(page).toHaveURL(/lugar=huetor-santillan/);
    await expect(page.getByRole("heading", { name: "Previsión para Huétor de Santillán" })).toBeVisible();
    await expect(page.getByText("Conjuntos (ensembles)")).toBeVisible();
    await expect(page.getByText("ECMWF: todavía no hay datos")).toBeVisible();
    // A stale warnings snapshot is unknown, never green.
    await expect(page.locator(".warning-day.level-unknown")).toHaveCount(3);
    await expect(page.locator(".warning-day.level-verde")).toHaveCount(0);
    await expect(page.locator(".forecast-meta.stale").first()).toContainText(
      "sin actualizar recientemente",
    );
    // GFS meteogram (ECMWF missing) and both ensembles.
    await expect(page.locator(".forecast-chart svg")).toHaveCount(3);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test("previsiones: un fallo de la API se muestra sin datos inventados", async ({ page }) => {
  await mockApi(page, 1);
  await page.route("**/api/v1/forecasts", (route) => route.fulfill({ status: 503, json: {} }));
  await page.goto("/previsiones");
  await expect(page.getByRole("status")).toContainText("No se pudo consultar la API");
  await expect(page.locator(".forecast-day")).toHaveCount(0);
});
