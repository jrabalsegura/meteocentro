import { test, expect } from "@playwright/test";
import { mockApi } from "./fixtures";

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
    .poll(() => Number(new URL(page.url()).searchParams.get("view")?.split(",")[2] ?? 99))
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
    .poll(() => Number(new URL(page.url()).searchParams.get("view")?.split(",")[2] ?? 99))
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
  expect(before).toBe(2);
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
  await expect.poll(() => state.requests.length).toBe(before + 2);
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
  await expect.poll(() => state.requests.length).toBe(before + 3);
  await expect(source).toBeFocused();
});
