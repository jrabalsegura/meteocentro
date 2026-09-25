import { test, expect } from "@playwright/test";
import { mockApi } from "./fixtures";

for (const width of [1440, 390]) {
  test(`estaciones coincidentes se separan con espacio libre a zoom intermedio (${width}px)`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    const state = await mockApi(page, 2);
    for (let i = 0; i < 2; i++) state.mapStations.set(i, { longitude: -3.516667, latitude: 40.35 });
    await page.goto("/?view=-3.516667,40.35,10.69");
    await expect(page.locator(".map-number:not(.cluster)")).toHaveCount(2);
    await expect(page.locator(".map-marker-leader")).toHaveCount(2);
    await expect(page.locator(".map-number.cluster")).toHaveCount(0);
    // MapLibre can redraw between two separate boundingBox calls during load.
    // Read both rectangles together and wait for the actual rendered geometry.
    await expect(async () => {
      const boxes = await page.locator(".map-number").evaluateAll((elements) =>
        elements.map((element) => element.getBoundingClientRect().toJSON()),
      );
      expect(boxes).toHaveLength(2);
      const [first, second] = boxes;
      expect(first.width).toBeGreaterThan(0);
      expect(second.width).toBeGreaterThan(0);
      expect(first.x + first.width).toBeLessThan(second.x);
      expect(first.x).toBeGreaterThan(0);
      expect(second.x + second.width).toBeLessThan(width);
    }).toPass({ timeout: 5000 });
    const view = new URL(page.url()).searchParams.get("view")!.split(",").map(Number);
    expect(view[0]).toBeCloseTo(-3.516667, 5);
    expect(view[1]).toBeCloseTo(40.35, 5);
    expect(view[2]).toBe(10.69);
    await page.screenshot({ path: `test-results/coincident-${width}.png` });
    for (let i = 0; i < 2; i++) {
      const marker = page.getByRole("button", { name: new RegExp(`SINTÉTICA 000${i}.*Abrir resumen`) });
      await marker.focus();
      await page.keyboard.press("Enter");
      await expect(page.locator(".station-panel")).toContainText(`SINTÉTICA 000${i}`);
      await page.getByRole("button", { name: "Cerrar ficha" }).click();
      await expect(page.locator(".station-panel")).toHaveCount(0);
      // Closing restores focus on the next frame; wait before choosing another marker.
      await expect(width === 390
        ? page.locator(".filter-panel summary")
        : page.getByRole("searchbox", { name: "Buscar estación" })).toBeFocused();
    }
    await page.getByRole("button", { name: "Humedad", exact: false }).click();
    await expect(page.locator(".map-number:not(.cluster)")).toHaveCount(2);
    await expect(page.getByRole("button", { name: /SINTÉTICA 0000.*: 0,0 %/ })).toBeVisible();
    state.excluded.add("00000000-0000-4000-8000-000000000001");
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await expect(page.locator(".map-number")).toHaveCount(1);
    await expect(page.locator(".map-marker-leader")).toHaveCount(0);
  });
}

test("los marcadores separados conservan el orden oeste-este y norte-sur", async ({ page }) => {
  const state = await mockApi(page, 3);
  // IDs in the opposite order to geography: 0000 is east of 0001.
  state.mapStations.set(0, { longitude: -3.7, latitude: 40.45 });
  state.mapStations.set(1, { longitude: -3.706, latitude: 40.45 });
  state.mapStations.set(2, { longitude: -1.5, latitude: 42 });
  await page.goto("/?view=-3.703,40.45,10.69");
  await expect(page.locator(".map-marker-leader")).toHaveCount(2);
  const box = async (name: string) =>
    (await page.getByRole("button", { name: new RegExp(`${name}.*Abrir resumen`) }).boundingBox())!;
  await expect(async () => {
    const east = await box("SINTÉTICA 0000");
    const west = await box("SINTÉTICA 0001");
    expect(west.x + west.width).toBeLessThan(east.x);
  }).toPass({ timeout: 5000 });
  // Same stations one above the other: the northern one stays on top.
  state.mapStations.set(0, { longitude: -3.7, latitude: 40.4497 });
  state.mapStations.set(1, { longitude: -3.7, latitude: 40.4503 });
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(async () => {
    const south = await box("SINTÉTICA 0000");
    const north = await box("SINTÉTICA 0001");
    expect(north.y <= south.y || north.x + north.width < south.x).toBe(true);
    if (Math.abs(north.y - south.y) > 5) expect(north.y).toBeLessThan(south.y);
  }).toPass({ timeout: 5000 });
});

test("la densidad conserva el grupo y al liberar espacio lo separa sin más zoom", async ({ page }) => {
  const state = await mockApi(page, 6);
  // Two coincident stations surrounded by four markers at zoom 10.
  const degreesPerPixel = 360 / (512 * 2 ** 10);
  [[0, 0], [0, 0], [-100, 0], [100, 0], [0, -60], [0, 60]].forEach(([x, y], i) => {
    state.mapStations.set(i, {
      longitude: -3.516667 + x * degreesPerPixel,
      latitude: 40.35 + y * degreesPerPixel * Math.cos(40.35 * Math.PI / 180),
    });
    state.mapReadings.set(i, { freshness: "fresh" });
  });
  await page.goto("/?view=-3.516667,40.35,10");
  await expect(page.locator(".map-number")).toHaveCount(5);
  await page.getByRole("button", { name: "Ver 2 estaciones", exact: true }).click();
  const list = page.locator(".group-list");
  await expect(list).toContainText("comparten coordenadas publicadas");
  await expect(list).toContainText("SYN-0");
  await expect(list).toContainText("SYN-1");
  await expect(list).toContainText("-5,0 °C");
  await expect(list).toContainText("-4,0 °C");
  await list.getByRole("button", { name: /SINTÉTICA 0001/ }).click();
  await expect(page.locator(".station-panel")).toContainText("SINTÉTICA 0001");
  await page.getByRole("button", { name: "Cerrar ficha" }).click();
  await page.getByRole("button", { name: "Ver 2 estaciones", exact: true }).click();
  state.excluded.add("00000000-0000-4000-8000-000000000002");
  state.excluded.add("00000000-0000-4000-8000-000000000003");
  state.excluded.add("00000000-0000-4000-8000-000000000004");
  state.excluded.add("00000000-0000-4000-8000-000000000005");
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(list).toHaveCount(0);
  await expect(page.locator(".map-number:not(.cluster)")).toHaveCount(2);
  await expect(page.locator(".map-number.cluster")).toHaveCount(0);
  expect(Number(new URL(page.url()).searchParams.get("view")!.split(",")[2])).toBe(10);
});

test("coordenadas próximas y coincidentes permiten leer ambos valores incluso al máximo zoom", async ({ page }) => {
  const state = await mockApi(page, 2);
  for (const zoom of [10, 18]) {
    state.mapStations.set(0, { longitude: -3.516667, latitude: 40.35 });
    state.mapStations.set(1, { longitude: -3.516667 + (zoom === 10 ? 0.005 : 0), latitude: 40.35 });
    await page.goto(`/?view=-3.516667,40.35,${zoom}`);
    await expect(page.locator(".map-number:not(.cluster)")).toHaveCount(2);
    await expect(page.locator(".map-marker-leader")).toHaveCount(2);
    await expect(page.locator(".map-number.cluster")).toHaveCount(0);
  }
  await expect(page.getByRole("button", { name: "Acercar", exact: true })).toBeDisabled();
});

test("aprovecha el espacio vertical y el zoom libera grupos rodeados de estaciones", async ({ page }) => {
  const state = await mockApi(page, 6);
  const degreesPerPixel = 360 / (512 * 2 ** 10);
  const positions = [[0, 0], [2, 0], [-100, 0], [100, 0], [0, -60], [0, 60]];
  positions.forEach(([x, y], i) => {
    state.mapStations.set(i, {
      longitude: -3.516667 + x * degreesPerPixel,
      latitude: 40.35 + y * degreesPerPixel * Math.cos(40.35 * Math.PI / 180),
    });
    state.mapReadings.set(i, { freshness: "fresh" });
  });
  // The stations on the sides leave room for two vertically stacked values.
  state.excluded.add("00000000-0000-4000-8000-000000000004");
  state.excluded.add("00000000-0000-4000-8000-000000000005");
  await page.goto("/?view=-3.516667,40.35,10");
  await expect(page.locator(".map-number:not(.cluster)")).toHaveCount(4);
  await expect(async () => {
    const boxes = await page.locator(".map-number").evaluateAll((elements) =>
      elements.map((element) => element.getBoundingClientRect().toJSON()),
    );
    expect(boxes).toHaveLength(4);
    const [top, bottom] = boxes;
    expect(top.height).toBeGreaterThan(0);
    expect(bottom.height).toBeGreaterThan(0);
    expect(top.y + top.height).toBeLessThan(bottom.y);
  }).toPass({ timeout: 5000 });
  state.excluded.clear();
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  const cluster = page.getByRole("button", { name: "Ampliar grupo de 2 estaciones" });
  await expect(cluster).toBeVisible();
  await cluster.click();
  await expect(page.locator(".map-number.cluster")).toHaveCount(0);
  await expect(page.locator(".map-number:not(.cluster)")).toHaveCount(6);
  const zoom = Number(new URL(page.url()).searchParams.get("view")!.split(",")[2]);
  expect(zoom).toBeGreaterThan(10);
  expect(zoom).toBeLessThan(18);
});

test("un grupo denso coincidente permite elegir estaciones sin seguir ampliando", async ({ page }) => {
  const state = await mockApi(page, 12);
  for (let i = 0; i < 12; i++) {
    state.mapStations.set(i, { longitude: -3.516667, latitude: 40.35, freshness: "fresh" });
    state.mapReadings.set(i, { freshness: "fresh" });
  }
  await page.goto("/?view=-3.516667,40.35,10");
  await page.getByRole("button", { name: "Ver 12 estaciones", exact: true }).click();
  await expect(page.locator(".group-list button")).toHaveCount(12);
  await page.getByRole("button", { name: "Humedad", exact: false }).click();
  await expect(page.locator(".group-list")).toHaveCount(0);
  await page.getByRole("button", { name: "Ver 12 estaciones", exact: true }).click();
  await expect(page.locator(".group-list")).toContainText("0,0 %");
});

test("límite de una hora solo en mapa: fuentes, ausencia, URL y tabla", async ({ page }) => {
  const state = await mockApi(page, 4);
  state.mapReadings.set(0, { age_seconds: 3600, value: 0 });
  // AEMET still reports fresh until 90 min; the map must enforce 60 min.
  state.mapReadings.set(1, { age_seconds: 3601, freshness: "fresh" });
  // Meteoclimatic reports stale after 45 min, but this value is within one hour.
  state.mapReadings.set(2, { age_seconds: 3000, freshness: "stale", provider: "meteoclimatic" });
  state.mapReadings.set(3, null);
  await page.goto("/?view=-3.7,40.5,7");
  const checkbox = page.getByRole("checkbox", { name: /Ocultar en el mapa/ });
  await expect(checkbox).toBeChecked();
  await expect(page.locator(".map-number")).toHaveCount(2);
  await expect(page.locator("tbody tr")).toHaveCount(4);
  const oldRow = page.locator("tbody tr").filter({ hasText: "SINTÉTICA 0001" });
  await expect(oldRow).toHaveClass(/outdated-row/);
  await expect(oldRow).toContainText("Más de 1 h sin actualizar");
  await expect(oldRow).toContainText("Oculta en el mapa");
  await expect(oldRow).not.toContainText("Reciente");
  const requests = state.requests.length;
  await checkbox.uncheck();
  await expect(page).toHaveURL(/hide_old=0/);
  await expect(page.locator(".map-number")).toHaveCount(4);
  await expect(page.locator(".map-number").filter({ hasText: "-4,0" })).toHaveClass(/stale/);
  await expect(oldRow).not.toContainText("Oculta en el mapa");
  expect(state.requests.length).toBe(requests);
  await page.reload();
  await expect(checkbox).not.toBeChecked();
  await expect(page.locator(".map-number")).toHaveCount(4);
  await page.getByRole("button", { name: "Humedad", exact: false }).click();
  await expect(page.locator(".map-number")).toHaveCount(4);
  await checkbox.check();
  await expect(page.locator(".map-number")).toHaveCount(2);
  await expect(page.locator("tbody tr")).toHaveCount(4);
  await page.reload();
  await expect(checkbox).toBeChecked();
  await expect(page.locator(".map-number")).toHaveCount(2);
  // Future observations are returned by the API as unknown with a clamped age of zero.
  state.mapReadings.set(0, { age_seconds: 0, freshness: "unknown" });
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.locator(".map-number")).toHaveCount(1);
  await expect(page.locator("tbody tr")).toHaveCount(4);
});

test("el refresco automático oculta datos envejecidos y recupera los nuevos", async ({ page }) => {
  const state = await mockApi(page, 1);
  state.mapReadings.set(0, { age_seconds: 3600 });
  await page.clock.install();
  await page.goto("/?view=-3.7,40.85,10");
  await page.clock.runFor(1000);
  await expect(page.locator(".map-number")).toHaveCount(1);
  state.mapReadings.set(0, { age_seconds: 3660, fetched_at: new Date().toISOString() });
  await page.clock.runFor(60000);
  await expect(page.locator(".map-number")).toHaveCount(0);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator(".empty-map")).toContainText("siguen en el listado");
  await expect(page.locator("tbody")).toContainText("Más de 1 h sin actualizar");
  await page.getByRole("button", { name: "Mostrar también las desactualizadas" }).click();
  await expect(page.locator(".map-number")).toHaveCount(1);
  await page.getByRole("checkbox", { name: /Ocultar en el mapa/ }).check();
  await expect(page.locator(".map-number")).toHaveCount(0);
  state.mapReadings.set(0, { age_seconds: 60 });
  await page.clock.runFor(60000);
  await expect(page.locator(".map-number")).toHaveCount(1);
  await expect(page.locator("tbody tr")).not.toHaveClass(/outdated-row/);
  await expect(page.locator(".empty-map")).toHaveCount(0);
});

test("escritorio: filtros, variables, ficha, vuelta, URL, orden y ausencia", async ({
  page,
}) => {
  const state = await mockApi(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  // These fixtures cover the whole region; Madrid is now the default opening view.
  await page.getByRole("button", { name: "Ver las cuatro provincias" }).click();
  await expect
    .poll(() =>
      Number(new URL(page.url()).searchParams.get("view")?.split(",")[2] ?? 99),
    )
    .toBeLessThan(8);
  await expect(page.locator(".map-number").first()).toBeVisible();
  await expect(page.locator(".provider-notice")).toContainText(
    "Acceso pendiente",
  );
  await page.getByLabel("Provincia", { exact: true }).selectOption("28");
  await expect(page.locator("tbody tr")).toHaveCount(8);
  await page.getByRole("button", { name: "Humedad", exact: false }).click();
  await expect(page).toHaveURL(/metric=humidity/);
  const beforeZoom = new URL(page.url()).searchParams.get("view");
  await page.locator(".maplibregl-ctrl-zoom-in").click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("view"))
    .not.toBe(beforeZoom);
  const view = new URL(page.url()).searchParams.get("view");
  await page.locator("tbody .station-name").first().click();
  await expect(page.locator(".station-panel")).toContainText(
    "Humedad relativa",
  );
  await expect(page.locator(".station-panel")).toContainText("10:00");
  await page.getByRole("button", { name: "Ver ficha completa" }).click();
  await expect(page).toHaveURL(/\/estaciones\/00000000/);
  await expect(page.locator(".reading-card")).toHaveCount(4);
  await page.reload();
  await expect(page.locator(".reading-card")).toHaveCount(4);
  await page.getByRole("link", { name: /Volver a mapa/ }).click();
  expect(new URL(page.url()).searchParams.get("view")).toBe(view);
  await page.getByRole("button", { name: "Cerrar ficha" }).click();
  await page.getByRole("link", { name: "Estaciones", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(8);
  await page.getByRole("button", { name: /Humedad \(%\)/ }).click();
  expect(
    state.requests.filter((r) => r.includes("/current")).length,
  ).toBeLessThan(8);
  expect(state.requests.every((r) => !r.includes("api_key"))).toBeTruthy();
  expect(errors).toEqual([]);
});

test("móvil 390: filtros plegables, panel inferior, teclado y sin desbordamiento", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto("/");
  await expect(page.locator(".filter-panel")).not.toHaveAttribute("open", "");
  await page.locator(".filter-panel summary").click();
  await expect(page.getByRole("checkbox", { name: /Ocultar en el mapa/ })).toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/mobile-age-filter.png", fullPage: false });
  await page.getByLabel("Provincia", { exact: true }).selectOption("05");
  await page.locator(".filter-panel summary").click();
  await page.locator("tbody .station-name").first().click();
  const panel = await page.locator(".station-panel").boundingBox();
  expect(panel!.height).toBeLessThan(844 / 2);
  expect(panel!.y).toBeGreaterThan(844 / 2);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.keyboard.press("Escape");
  await expect(page.locator(".station-panel")).toHaveCount(0);
  await page.screenshot({ path: "test-results/mobile.png", fullPage: false });
});

test("fallos de cartografía y API, exclusión tras refresco y URL directa", async ({
  page,
}) => {
  const state = await mockApi(page, 32, "error");
  await page.goto("/");
  await expect(page.locator(".map-error")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(32);
  await page.locator("tbody .station-name").first().click();
  const id = new URL(page.url()).searchParams.get("station")!;
  await expect(page.locator(".station-panel")).toContainText("SINTÉTICA");
  state.excluded.add(id);
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(page.locator(".station-panel")).toContainText(
    "Estación no disponible",
  );
  await expect(page.locator("tbody tr")).toHaveCount(31);
  await page.goto(`/estaciones/${id}`);
  await expect(page.locator(".station-panel")).toContainText(
    "Estación no disponible",
  );
  state.fail = true;
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(page.locator(".notice.error")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(0);
  state.fail = false;
  await page.getByRole("button", { name: "Reintentar consulta" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(31);
});

test("2.000 sintéticas: colisiones, cambio de variable y carga útil medidos", async ({
  page,
}, testInfo) => {
  const state = await mockApi(page, 2000);
  await page.addInitScript(() => {
    (window as unknown as { longTasks: number[] }).longTasks = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries())
        (window as unknown as { longTasks: number[] }).longTasks.push(
          entry.duration,
        );
    }).observe({ type: "longtask", buffered: true });
  });
  const start = performance.now();
  await page.goto("/");
  await page.getByRole("button", { name: "Ver las cuatro provincias" }).click();
  await expect
    .poll(() =>
      Number(new URL(page.url()).searchParams.get("view")?.split(",")[2] ?? 99),
    )
    .toBeLessThan(8);
  await expect(page.locator(".map-number").first()).toBeVisible();
  await expect(page.locator(".summary-strip")).toContainText(
    "2000 registradas",
  );
  const usefulMs = performance.now() - start;
  const rects = await page.locator(".map-number").evaluateAll((elements) =>
    elements.map((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    }),
  );
  let collisions = 0;
  rects.forEach((a, i) =>
    rects.slice(i + 1).forEach((b) => {
      if (
        a.x < b.x + b.w &&
        a.x + a.w > b.x &&
        a.y < b.y + b.h &&
        a.y + a.h > b.y
      )
        collisions++;
    }),
  );
  expect(collisions).toBe(0);
  const switchStart = performance.now();
  await page.getByRole("button", { name: "Humedad", exact: false }).click();
  await expect(page.locator(".map-caption")).toContainText("Humedad relativa");
  await expect(page.locator("tbody tr")).toHaveCount(50);
  await expect(page.locator(".map-number").first()).toBeVisible();
  const switchMs = performance.now() - switchStart;
  const metrics = {
    usefulMs: Math.round(usefulMs),
    switchMs: Math.round(switchMs),
    markers: rects.length,
    collisions,
    apiRequests: state.requests.length,
    longTasks: await page.evaluate(
      () => (window as unknown as { longTasks: number[] }).longTasks,
    ),
  };
  console.log("PHASE4_METRICS", JSON.stringify(metrics));
  await testInfo.attach("measurements", {
    body: JSON.stringify(metrics, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({
    path: "test-results/desktop-2000.png",
    fullPage: false,
  });
});

test("pausa en pestaña oculta y recuperación al volver sin consultas por marcador", async ({
  page,
}) => {
  const state = await mockApi(page, 32);
  await page.clock.install();
  await page.goto("/");
  await page.clock.runFor(1000);
  await expect(page.locator("tbody tr")).toHaveCount(32);
  const before = state.requests.length;
  expect(before).toBe(3); // Session plus the two batched weather reads.
  await page.evaluate(() =>
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    }),
  );
  await page.clock.runFor(61000);
  expect(state.requests.length).toBe(before);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor(1000);
  await expect.poll(() => state.requests.length).toBe(before + 3);
});

test("origen no disponible: alternativa explícita y unidad de la ficha", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto(
    "/estaciones/00000000-0000-4000-8000-000000000000?source=origen-retirado&metric=wind_speed",
  );
  await expect(page.locator(".station-panel .provider-notice")).toContainText(
    "ya no está disponible",
  );
  await expect(page.getByLabel("Fuente del dato", { exact: true })).toHaveValue(
    "",
  );
  await expect(
    page.locator(".reading-card").filter({ hasText: "Velocidad del viento" }),
  ).toContainText("km/h");
});

test("el refresco conserva el foco de la ficha y sus controles", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/?station=00000000-0000-4000-8000-000000000000");
  const source = page.getByLabel("Fuente del dato", { exact: true });
  await expect(source).toBeVisible();
  await source.focus();
  const before = state.requests.length;
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect.poll(() => state.requests.length).toBe(before + 4);
  await expect(source).toBeFocused();
});

test("la tabla comparte todos los filtros sin limitarse al encuadre", async ({ page }) => {
  await mockApi(page, 80);
  await page.goto("/");
  await expect(page.locator("tbody tr")).toHaveCount(50);
  await page.getByRole("button", { name: "Siguiente" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(30);
  await page.getByLabel("Provincia", { exact: true }).selectOption("28");
  await expect(page.locator("tbody tr")).toHaveCount(20);
  await page.getByLabel("Estado", { exact: true }).selectOption("stale");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator("tbody")).toContainText("SINTÉTICA 0004");
  await page.getByLabel("Estado", { exact: true }).selectOption("fresh");
  await page.getByLabel("Buscar estación", { exact: true }).fill("0012");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator("tbody")).toContainText("SINTÉTICA 0012");
  await page.getByLabel("Fuente", { exact: true }).selectOption("meteoclimatic");
  await expect(page.locator("tbody tr")).toHaveCount(0);
  await page.getByLabel("Fuente", { exact: true }).selectOption("aemet");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "Ver las cuatro provincias" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.reload();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator(".map-scale, .map-note")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Leyenda", exact: true })).toBeVisible();
});

test("resumen diario visible en móvil: cero, cobertura y ausencia de lluvia", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await mockApi(page);
  state.current.day_summaries = [{
    metric: "temperature", unit: "°C", source_id: "source-0", provider: "aemet", external_id: "SYN-0",
    minimum: 0, maximum: 18, minimum_at: "2026-09-19T00:30:00Z", maximum_at: "2026-09-19T06:30:00Z", total: null, coverage: .5, partial: true,
    period_start: "2026-09-18T22:00:00Z", period_end: "2026-09-19T08:00:00Z", observed_at: "2026-09-19T07:00:00Z",
  }];
  await page.goto("/?station=00000000-0000-4000-8000-000000000000");
  const summary = page.getByRole("region", { name: "Resumen del día" });
  await expect(summary).toBeVisible();
  await expect(summary.locator(".daily-stat").nth(0)).toContainText("0,0 °C");
  await expect(summary.locator(".daily-stat").nth(1)).toContainText("18,0 °C");
  await expect(summary.locator(".daily-stat").nth(0)).toContainText("02:30");
  await expect(summary.locator(".daily-stat").nth(1)).toContainText("08:30");
  await expect(summary.locator(".daily-stat").nth(0)).not.toContainText("09:00");
  await expect(summary.locator(".daily-stat").nth(0)).toContainText("Parcial · cobertura 50 %");
  await expect(summary.locator(".daily-stat").nth(2)).toContainText("Sin datos de hoy");
  const panel = await page.locator(".station-panel").boundingBox();
  const daily = await summary.locator(".daily-grid").boundingBox();
  expect(daily!.y + daily!.height).toBeLessThan(panel!.y + panel!.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(page.locator("tbody tr")).toHaveCount(32);
  await expect(page.locator(".map-canvas canvas")).toBeVisible();
  await page.screenshot({ path: "test-results/mobile-daily-summary.png" });
});

test("diarios reportados respetan fuente, fecha y contador sin inventar día civil", async ({ page }) => {
  const { reading } = await import("./fixtures");
  const state = await mockApi(page);
  state.current.readings = [
    reading(0),
    ...["temperature_daily_min", "temperature_daily_max", "rain_daily"].map((metric, i) => ({
      ...reading(0), metric, value: [4, 23, 0][i], unit: i === 2 ? "mm" : "°C",
      kind: i === 2 ? "daily_counter" : i === 0 ? "daily_minimum" : "daily_maximum",
      period_basis: "provider_day_timezone_unknown", provider: "meteoclimatic", source_id: "meteo-0",
    })),
  ];
  state.current.sources = [
    { id: "source-0", provider: "aemet", external_id: "SYN-0", source_url: null, coordinate_precision: null, attribution: "AEMET", provider_status: "verified" },
    { id: "meteo-0", provider: "meteoclimatic", external_id: "SYN-M0", source_url: null, coordinate_precision: null, attribution: "Meteoclimatic", provider_status: "verified" },
  ];
  await page.goto("/?station=00000000-0000-4000-8000-000000000000&source=meteo-0");
  const summary = page.getByRole("region", { name: "Resumen del día" });
  await expect(summary).toContainText("4,0 °C");
  await expect(summary).toContainText("23,0 °C");
  await expect(summary.getByText("Hora del extremo no disponible", { exact: true })).toHaveCount(2);
  await expect(summary).toContainText("0,0 mm");
  await expect(summary).toContainText("horario de reinicio desconocido");
  await page.getByLabel("Fuente del dato", { exact: true }).selectOption("source-0");
  await expect(summary.locator(".daily-stat").filter({ hasText: "Sin datos de hoy" })).toHaveCount(3);
  await page.getByLabel("Fuente del dato", { exact: true }).selectOption("meteo-0");
  state.current.generated_at = "2026-09-20T08:00:00Z";
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(summary.locator(".daily-stat").filter({ hasText: "Sin datos de hoy" })).toHaveCount(3);
});

for (const width of [1440, 390]) {
  test(`valores reportados con horas aproximadas del archivo de su propia fuente (${width}px)`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    const { reading } = await import("./fixtures");
    const state = await mockApi(page);
    state.current.readings = [
      reading(0),
      ...["temperature_daily_min", "temperature_daily_max"].map((metric, i) => ({
        ...reading(0), metric, value: [4, 23][i], kind: i === 0 ? "daily_minimum" : "daily_maximum",
        period_basis: "provider_day_timezone_unknown", provider: "meteoclimatic", source_id: "meteo-0",
      })),
    ];
    state.current.day_summaries = [
      { metric: "temperature", unit: "°C", source_id: "source-0", provider: "aemet", external_id: "SYN-0",
        minimum: 0, maximum: 18, minimum_at: "2026-09-19T01:00:00Z", maximum_at: "2026-09-19T05:00:00Z",
        total: null, coverage: .5, partial: true, period_start: "2026-09-18T22:00:00Z",
        period_end: "2026-09-19T08:00:00Z", observed_at: "2026-09-19T07:00:00Z" },
      { metric: "temperature", unit: "°C", source_id: "meteo-0", provider: "meteoclimatic", external_id: "SYN-M0",
        minimum: 5, maximum: 22, minimum_at: "2026-09-19T00:30:00Z", maximum_at: "2026-09-19T06:30:00Z",
        total: null, coverage: .4, partial: true, period_start: "2026-09-18T22:00:00Z",
        period_end: "2026-09-19T08:00:00Z", observed_at: "2026-09-19T07:00:00Z" },
    ];
    // Automatic selection prefers the report; its hours must not come from AEMET.
    await page.goto("/?station=00000000-0000-4000-8000-000000000000");
    const summary = page.getByRole("region", { name: "Resumen del día" });
    const low = summary.locator(".daily-stat").nth(0);
    const high = summary.locator(".daily-stat").nth(1);
    await expect(low).toContainText("4,0 °C");
    await expect(high).toContainText("23,0 °C");
    await expect(low).toContainText(/Hora aprox\.: 19\/0?9, 02:30/);
    await expect(high).toContainText(/Hora aprox\.: 19\/0?9, 08:30/);
    await expect(low).toContainText("Parcial · cobertura 40 %");
    await expect(low).not.toContainText("5,0 °C");
    await expect(high).not.toContainText("22,0 °C");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: `test-results/extreme-hours-${width}.png` });
    // No archive for this source: never use another source's hour or latest report time.
    state.current.day_summaries = state.current.day_summaries.slice(0, 1);
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await expect(summary.getByText("Hora del extremo no disponible", { exact: true })).toHaveCount(2);
    await expect(low).toContainText("4,0 °C");
  });
}

test("el listado muestra mínima y máxima de hoy en temperatura, humedad y viento", async ({ page }) => {
  const state = await mockApi(page, 2);
  state.mapStations.set(0, {
    day: {
      provider: "aemet",
      coverage: 0.8,
      partial: true,
      minimum: { value: 8.4, at: "2026-09-19T05:10:00+00:00", origin: "archive" },
      maximum: { value: 27.1, at: "2026-09-19T14:40:00+00:00", origin: "reported" },
    },
  });
  state.mapStations.set(1, {
    day: {
      provider: "aemet",
      coverage: null,
      partial: true,
      minimum: { value: null, at: null, origin: null },
      maximum: { value: null, at: null, origin: null },
    },
  });
  await page.goto("/estaciones");
  const table = page.getByRole("table");
  await expect(table.getByRole("columnheader", { name: /Mín\. hoy/ })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: /Máx\. hoy/ })).toBeVisible();
  const first = table.getByRole("row", { name: /SINTÉTICA 0000/ });
  await expect(first).toContainText("8,4");
  await expect(first).toContainText("07:10");
  await expect(first).toContainText("27,1");
  await expect(first).toContainText("16:40");
  // No archive: an explicit dash, never the current reading presented as an extreme.
  const second = table.getByRole("row", { name: /SINTÉTICA 0001/ });
  await expect(second.locator(".day-cell").first()).toHaveText("—");
  await table.getByRole("button", { name: /Máx\. hoy/ }).click();
  await expect(table.getByRole("row").nth(1)).toContainText("SINTÉTICA 0000");
  await page.getByRole("button", { name: /Lluvia/ }).click();
  await expect(table.getByRole("columnheader", { name: /Mín\. hoy/ })).toHaveCount(0);
});
