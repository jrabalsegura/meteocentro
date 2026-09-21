// Offline meteorological data. Run against the isolated measure_phase5 --keep fixture.
import { chromium, expect } from "@playwright/test";
import fs from "node:fs/promises";
const evidence = JSON.parse(
  await fs.readFile("../runtime/phase5/annual-benchmark.json", "utf8"),
);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
const requests = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("response", (r) => {
  if (r.url().includes("/api/"))
    requests.push({ status: r.status(), path: new URL(r.url()).pathname });
});
await page.route("https://www.ign.es/**", (r) => r.abort());
await page.goto(
  `http://127.0.0.1:5175/historicos?station=${evidence.station_id}`,
);
await expect(page.getByLabel("Origen de la serie")).toHaveValue(
  evidence.source_id,
);
await page
  .getByRole("button", { name: "Fechas personalizadas", exact: true })
  .click();
await page.getByLabel("Desde (UTC)", { exact: true }).fill("2025-01-01");
const began = Date.now();
await page
  .getByLabel("Hasta (UTC, excluido)", { exact: true })
  .fill("2026-01-01");
await expect(page.locator(".history-chart svg")).toBeVisible();
await expect(page.locator(".coverage-strip")).toContainText(
  "Agregados diarios",
);
const yearChartMs = Date.now() - began;
await page.locator(".history-chart").scrollIntoViewIfNeeded();
await page.screenshot({
  path: "../runtime/phase5/real-postgres-desktop.png",
  fullPage: true,
});
await page
  .getByRole("button", { name: "Tabla de registros", exact: true })
  .click();
await expect(page.locator("tbody tr")).toHaveCount(365);
await page.getByRole("button", { name: "Gráfico", exact: true }).click();
await page.setViewportSize({ width: 390, height: 844 });
await expect(page.locator(".history-chart svg")).toBeVisible();
await expect
  .poll(() =>
    page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  )
  .toBe(true);
const noOverflow = await page.evaluate(
  () => document.documentElement.scrollWidth <= innerWidth,
);
await page.screenshot({
  path: "../runtime/phase5/real-postgres-mobile.png",
  fullPage: true,
});
await page.getByRole("link", { name: "Datos diarios", exact: true }).click();
await page.getByLabel("Día civil de Madrid").fill("2025-03-29");
await expect(page.locator(".daily-map .map-number").first()).toBeVisible();
await expect(page.locator("tbody tr")).toHaveCount(20);
await page.screenshot({
  path: "../runtime/phase5/real-postgres-daily-mobile.png",
  fullPage: true,
});
if (!noOverflow || errors.length)
  throw new Error(JSON.stringify({ noOverflow, errors }));
const result = {
  fixture: "1051200 synthetic observations, real PostgreSQL API",
  yearChartMs,
  noOverflow,
  errors,
  requests,
};
await fs.writeFile(
  "../runtime/phase5/browser-postgres.json",
  JSON.stringify(result, null, 2),
);
console.log(JSON.stringify(result));
await browser.close();
