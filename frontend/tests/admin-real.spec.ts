import { test, expect } from "@playwright/test";

test("navegador → API → PostgreSQL: excluir y restaurar con sesión real", async ({
  page,
}) => {
  test.skip(
    !process.env.E2E_REAL_ADMIN,
    "Requires an isolated seeded *_ui_test database and API",
  );
  const id = "00000000-0000-4000-8000-000000000006";
  expect((await page.request.get("/api/v1/admin/stations")).status()).toBe(401);
  expect((await page.request.get("/api/v1/stations")).status()).toBe(401);
  await page.goto("/gestion");
  await page.getByLabel("Usuario", { exact: true }).fill("fixture-owner");
  await page
    .getByLabel("Contraseña", { exact: true })
    .fill(process.env.E2E_ADMIN_PASSWORD!);
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
  await expect(
    page.getByRole("button", {
      name: "SINTÉTICA · administración",
      exact: true,
    }),
  ).toBeVisible();
  expect((await page.request.get(`/api/v1/stations/${id}`)).status()).toBe(200);
  await page.screenshot({
    path: "../runtime/phase6-tests/admin-desktop.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Eliminar de la app", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("AEMET · SYN6");
  await dialog
    .getByLabel("Motivo opcional")
    .fill("Sensor sintético en revisión");
  await page.screenshot({
    path: "../runtime/phase6-tests/admin-confirmation.png",
    fullPage: true,
  });
  await dialog.getByRole("button", { name: "Confirmar eliminación" }).click();
  await expect(
    page.getByRole("button", { name: "Restaurar", exact: true }),
  ).toBeVisible();
  expect((await page.request.get(`/api/v1/stations/${id}`)).status()).toBe(404);
  await expect(
    (await page.request.get("/api/v1/stations")).json().then((d) => d.total),
  ).resolves.toBe(0);
  const archived = await page.request.get(
    `/api/v1/admin/stations/${id}/archive`,
  );
  expect((await archived.json()).items).toHaveLength(1);
  await page.getByRole("button", { name: "Excluidas", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../runtime/phase6-tests/admin-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Restaurar", exact: true }).click();
  await dialog.getByRole("button", { name: "Confirmar restauración" }).click();
  await expect(
    page.getByText("No hay estaciones para este filtro."),
  ).toBeVisible();
  expect((await page.request.get(`/api/v1/stations/${id}`)).status()).toBe(200);
  const audit = await (await page.request.get("/api/v1/admin/audit")).json();
  expect(audit.items.map((e: { action: string }) => e.action)).toEqual([
    "restore_station",
    "exclude_station",
  ]);
  await page.getByRole("button", { name: "Salir", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Acceso privado" }),
  ).toBeVisible();
  expect(
    (await page.request.get(`/api/v1/admin/stations/${id}/archive`)).status(),
  ).toBe(401);
});
