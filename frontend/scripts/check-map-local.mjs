// Explicit manual check: real IGN tiles, observations exclusively from local API.
import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
const output = new URL("../../runtime/phase4/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const tiles = [],
    errors = [];
  page.on("response", (response) => {
    if (new URL(response.url()).hostname === "www.ign.es")
      tiles.push({
        service: new URL(response.url()).pathname,
        status: response.status(),
      });
  });
  page.on("pageerror", (error) => errors.push(error.message));
  const start = performance.now();
  await page.goto("http://127.0.0.1:5174/");
  await page.locator(".map-number").first().waitFor({ state: "visible" });
  const usefulMs = Math.round(performance.now() - start);
  await page
    .waitForResponse((r) => r.url().includes("mapa-raster") && r.ok())
    .catch(() => {});
  await page.waitForTimeout(1000);
  await page.screenshot({
    path: new URL("desktop-real-topo.png", output).pathname,
  });
  const switchStart = performance.now();
  await page.getByRole("button", { name: "Humedad", exact: false }).click();
  await page.waitForResponse(
    (r) => r.url().includes("/api/v1/map?metric=humidity") && r.ok(),
  );
  await page.locator(".map-number").first().waitFor({ state: "visible" });
  const switchMs = Math.round(performance.now() - switchStart);
  const clearLoaded = page.waitForResponse(
    (r) => r.url().includes("ign-base") && r.ok(),
  );
  await page.getByRole("button", { name: "Claro", exact: true }).click();
  await clearLoaded;
  await page.waitForTimeout(1000);
  await page.screenshot({
    path: new URL("desktop-real-light.png", output).pathname,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.locator(".map-number").first().waitFor({ state: "visible" });
  await page.screenshot({
    path: new URL("mobile-real-map.png", output).pathname,
  });
  await page.locator("tbody .station-name").first().click();
  await page
    .locator(".station-panel h2")
    .filter({ hasText: "SINTÉTICA" })
    .waitFor();
  await page.screenshot({
    path: new URL("mobile-real-panel.png", output).pathname,
  });
  const fits = await page.evaluate(
    () => document.documentElement.scrollWidth <= innerWidth,
  );
  const panel = await page.locator(".station-panel").boundingBox();
  const result = {
    synthetic_population: 2000,
    usefulMs,
    switchMs,
    tiles,
    pageErrors: errors,
    mobileNoOverflow: fits,
    mobilePanelHeight: panel.height,
    browser: browser.version(),
  };
  await writeFile(
    new URL("browser-real-measurements.json", output),
    JSON.stringify(result, null, 2),
  );
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
}
