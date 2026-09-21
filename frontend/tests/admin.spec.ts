import { test, expect } from "@playwright/test";

async function setup(page: import("@playwright/test").Page) {
  const state = {
    authenticated: false,
    excluded: false,
    reason: "",
    mutations: [] as string[],
  };
  const station = () => ({
    id: "station-1",
    name: "Estación sintética de Madrid",
    status: state.excluded ? "excluded" : "active",
    province_code: "28",
    latitude: 40.4,
    longitude: -3.7,
    altitude_m: 650,
    exclusion: state.excluded
      ? {
          reason: state.reason,
          created_at: "2026-09-21T12:00:00Z",
          actor: "owner",
        }
      : null,
    sources: [
      {
        id: "source-1",
        provider: "aemet",
        external_id: "SYN1",
        status: "enabled",
        eligible: !state.excluded,
        excluded: state.excluded,
        own_exclusion: false,
        review_reason: null,
        latitude: 40.4,
        longitude: -3.7,
        capabilities: {},
      },
    ],
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const session = {
      authenticated: state.authenticated,
      private_read: true,
      username: "owner",
      csrf_token: "synthetic-csrf",
    };
    if (path.endsWith("/auth/session")) return route.fulfill({ json: session });
    if (path.endsWith("/auth/login")) {
      state.authenticated = true;
      return route.fulfill({ json: { ...session, authenticated: true } });
    }
    if (!state.authenticated)
      return route.fulfill({
        status: 401,
        json: { detail: { code: "authentication_required" } },
      });
    if (route.request().method() === "POST") {
      expect(route.request().headers()["x-csrf-token"]).toBe("synthetic-csrf");
      state.mutations.push(path);
      if (path.endsWith("/exclude")) {
        state.excluded = true;
        state.reason = route.request().postDataJSON().reason;
      }
      if (path.endsWith("/restore")) state.excluded = false;
      if (path.endsWith("/logout")) state.authenticated = false;
      return route.fulfill({ json: { changed: true } });
    }
    if (path.endsWith("/archive"))
      return route.fulfill({
        json: {
          items: [
            {
              source_id: "source-1",
              product: "synthetic",
              observed_at: "2026-09-21T10:00:00Z",
              metrics: { temperature: { value: 20, unit: "°C" } },
            },
          ],
        },
      });
    if (path.endsWith("/stations"))
      return route.fulfill({ json: { items: [station()], total: 1 } });
    if (path.endsWith("/providers"))
      return route.fulfill({
        json: {
          items: [
            {
              code: "aemet",
              name: "AEMET",
              status: "verified",
              credential: "gestionada_por_worker",
              day_calls: 10,
              daily_call_budget: 400,
              observation_age_seconds: 1200,
              capabilities: { current: true },
            },
          ],
        },
      });
    return route.fulfill({ json: { items: [] } });
  });
  return state;
}

async function login(page: import("@playwright/test").Page) {
  await page.goto("/gestion");
  await expect(
    page.getByRole("heading", { name: "Acceso privado" }),
  ).toBeVisible();
  await page.getByLabel("Usuario", { exact: true }).fill("owner");
  await page
    .getByLabel("Contraseña", { exact: true })
    .fill("synthetic-test-only");
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Administración", exact: true }),
  ).toBeVisible();
}

test("gestión: confirmar eliminación, conservar archivo y restaurar", async ({
  page,
}) => {
  const state = await setup(page);
  await login(page);
  await page
    .getByRole("button", { name: "Eliminar de la app", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Estación sintética de Madrid");
  await expect(dialog).toContainText("AEMET · SYN1");
  await expect(dialog).toContainText(
    "no modifica la estación en los servicios externos",
  );
  await dialog.getByLabel("Motivo opcional").fill("Sensor defectuoso");
  await dialog.getByRole("button", { name: "Confirmar eliminación" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByText("Sensor defectuoso", { exact: false }),
  ).toBeVisible();
  expect(state.excluded).toBe(true);
  await page.getByRole("button", { name: "Excluidas", exact: true }).click();
  await page
    .getByRole("button", { name: "Estación sintética de Madrid", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Archivo privado conservado" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Restaurar", exact: true }).click();
  await expect(dialog).toContainText("puede contener huecos");
  await dialog.getByRole("button", { name: "Confirmar restauración" }).click();
  await expect(
    page.getByRole("button", { name: "Eliminar de la app", exact: true }),
  ).toBeVisible();
  expect(state.excluded).toBe(false);
  await page.getByRole("button", { name: "Salir", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Acceso privado" }),
  ).toBeVisible();
  await expect(page.getByText("Sensor defectuoso")).not.toBeVisible();
});

test("gestión móvil: cancelar sin mutación y lanzar búsqueda en segundo plano", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await setup(page);
  await login(page);
  await page
    .getByRole("button", { name: "Eliminar de la app", exact: true })
    .click();
  await page.getByRole("button", { name: "Cancelar", exact: true }).click();
  expect(state.mutations).toHaveLength(0);
  await page
    .getByRole("button", { name: "Descubrimiento", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Buscar nuevas estaciones · AEMET" })
    .click();
  await expect.poll(() => state.mutations.length).toBe(1);
  expect(state.mutations[0]).toBe("/api/v1/admin/discovery/aemet");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("sesión revocada elimina la gestión abierta", async ({ page }) => {
  const state = await setup(page);
  await login(page);
  state.authenticated = false;
  await page.getByRole("button", { name: "Fuentes", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Acceso privado" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Administración", exact: true }),
  ).not.toBeVisible();
});
