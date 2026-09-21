import { test, expect, type Page } from "@playwright/test";
import { mockApi, station } from "./fixtures";
const id = station(0, "temperature").id;
async function mockHistory(page: Page) {
  const state = { empty: false, fail: false, excluded: false };
  await mockApi(page);
  await page.route("**/api/v1/stations/*/series*", async (route) => {
    if (state.excluded) return route.fulfill({ status: 404, json: {} });
    if (state.fail) return route.fulfill({ status: 503, json: {} });
    const url = new URL(route.request().url());
    const start = Date.parse(url.searchParams.get("from")!);
    const duration = Date.parse(url.searchParams.get("to")!) - start;
    const rain = url.searchParams.get("metric") === "rain";
    const resolution = url.searchParams.get("resolution");
    const method = resolution === "raw" ? "source_observation" : "local-v1";
    const items = Array.from({ length: 24 }, (_, i) => ({
      time: new Date(start + (duration * i) / 24).toISOString(),
      value: i === 12 ? null : rain ? i % 4 : 10 + Math.sin(i / 5) * 6,
      minimum: i === 12 ? null : 5 + Math.sin(i / 5) * 3,
      maximum: i === 12 ? null : 22 + Math.sin(i / 5) * 3,
      mean: i === 12 ? null : 10 + Math.sin(i / 5) * 6,
      total: rain ? i % 4 : null,
      unit: rain ? "mm" : "°C",
      metric: rain ? "rain" : "temperature",
      product: "SINTÉTICO · observaciones",
      channel: "synthetic-1",
      aggregation_method: method,
      coverage: 0.75,
      partial: true,
      break_before: i === 0 || i === 13,
      period_basis: "Europe/Madrid",
      flags: [],
    }));
    return route.fulfill({
      json: {
        items: state.empty ? [] : items,
        provider: "aemet",
        external_id: "SYN-0",
        next_offset: null,
        export_allowed: true,
        availability: {
          raw: { first: "2026-09-01T00:00:00Z", last: "2026-09-19T00:00:00Z" },
          hour: { first: "2026-09-01T00:00:00Z", last: "2026-09-19T00:00:00Z" },
          day: { first: "2026-08-01T00:00:00Z", last: "2026-09-19T00:00:00Z" },
          pending_days: 0,
        },
        events: [
          {
            time: "2026-09-10T00:00:00Z",
            kind: "location",
            latitude: 40.4,
            longitude: -3.7,
          },
        ],
      },
    });
  });
  await page.route("**/api/v1/stations/*/daily*", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            time: "2026-09-01T07:00:00Z",
            period_start: "2026-09-01T07:00:00Z",
            period_end: "2026-09-02T07:00:00Z",
            value: null,
            minimum: 10,
            maximum: 20,
            mean: 15,
            total: null,
            unit: "°C",
            coverage: null,
            partial: true,
            channel: "provider",
            product: "aemet_daily",
            aggregation_method: "provider",
            period_basis: "AEMET_provider_date",
          },
        ],
        export_allowed: true,
        provider: "aemet",
        external_id: "SYN-0",
        next_offset: null,
        availability: {
          raw: { first: null, last: null },
          hour: { first: null, last: null },
          day: { first: "2026-09-01T00:00:00Z", last: "2026-09-19T00:00:00Z" },
          pending_days: 0,
        },
      },
    }),
  );
  await page.route("**/api/v1/stations/*/records*", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            channel: "provider",
            aggregation_method: "provider",
            period_basis: "AEMET_provider_date",
            first: "2026-08-01T00:00:00Z",
            last: "2026-09-19T00:00:00Z",
            days_available: 30,
            minimum: {
              value: 3,
              period_start: "2026-08-01T00:00:00Z",
              coverage: null,
            },
            maximum: {
              value: 37,
              period_start: "2026-08-20T00:00:00Z",
              coverage: null,
            },
            total: null,
          },
        ],
      },
    }),
  );
  return state;
}
for (const width of [1440, 390])
  test(`históricos ${width}: gráfico, huecos, periodos, tablas y efemérides`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    const state = await mockHistory(page);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`/historicos?station=${id}`);
    await expect(page.locator(".history-chart svg")).toBeVisible();
    await expect(page.locator(".coverage-strip")).toContainText("1 sept 2026");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: `../runtime/phase5/history-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("button", { name: "7 días", exact: true }).click();
    await expect(page.locator(".coverage-strip")).toContainText(
      "Agregados horarios",
    );
    await page
      .getByRole("button", { name: "Tabla de registros", exact: true })
      .click();
    await expect(page.locator("tbody tr")).toHaveCount(24);
    await expect(page.locator("tbody tr").first()).toContainText("75,0 %");
    await page.getByRole("button", { name: "Diarios", exact: true }).click();
    await expect(page.locator("tbody")).toContainText("Desconocida");
    await expect(page.locator("tbody")).toContainText("Diario del proveedor");
    await page.getByRole("button", { name: "Efemérides", exact: true }).click();
    await expect(page.locator(".record-card")).toContainText(
      "30 diarios archivados",
    );
    await expect(page.locator(".record-card")).toContainText(
      "Cobertura desconocida",
    );
    await page.getByRole("button", { name: "Evolución", exact: true }).click();
    state.empty = true;
    await page.getByRole("button", { name: "48 h", exact: true }).click();
    await expect(page.locator(".history-empty")).toContainText(
      "Los diarios antiguos no generan curvas horarias",
    );
    expect(errors).toEqual([]);
  });
test("históricos: fallo, recuperación y exclusión no dejan gráfico anterior", async ({
  page,
}) => {
  const state = await mockHistory(page);
  await page.goto(`/historicos?station=${id}`);
  await expect(page.locator(".history-chart svg")).toBeVisible();
  state.fail = true;
  await page.getByRole("button", { name: "48 h", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator(".history-chart")).toHaveCount(0);
  state.fail = false;
  await page.getByRole("button", { name: "Reintentar", exact: true }).click();
  await expect(page.locator(".history-chart svg")).toBeVisible();
  state.excluded = true;
  await page.getByRole("button", { name: "72 h", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("no disponible");
  await expect(page.locator(".history-chart")).toHaveCount(0);
});

test("históricos: conserva el origen elegido al refrescar y señala su retirada", async ({
  page,
}) => {
  await page.clock.install();
  await mockHistory(page);
  let withdrawn = false;
  const original = station(0, "temperature");
  await page.route("**/api/v1/stations/*/current", (route) =>
    route.fulfill({
      json: {
        ...original,
        sources: [
          ...original.sources,
          ...(withdrawn
            ? []
            : [
                {
                  ...original.sources[0],
                  id: "source-alt",
                  provider: "meteoclimatic",
                  external_id: "ALT",
                },
              ]),
        ],
        readings: [],
        generated_at: new Date().toISOString(),
      },
    }),
  );
  await page.goto(`/historicos?station=${id}`);
  await expect(page.locator(".history-chart svg")).toBeVisible();
  await page.getByLabel("Origen de la serie").selectOption("source-alt");
  await page.clock.runFor(61000);
  await expect(page.getByLabel("Origen de la serie")).toHaveValue("source-alt");
  withdrawn = true;
  await page.clock.runFor(61000);
  await expect(page.getByLabel("Origen de la serie")).toHaveValue("source-alt");
  await expect(
    page.getByLabel("Origen de la serie").locator("option:checked"),
  ).toHaveText("Origen retirado · elige otro");
});
