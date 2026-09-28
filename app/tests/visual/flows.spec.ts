import { expect, test } from "@playwright/test";

// Functional flows against the demo fixture; one viewport is enough.
test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "1440x900", "functional flows run once");
});

test("engine can be stopped and started from the status bar", async ({ page }) => {
  await page.goto("/?fixture=demo#/inicio");
  const bar = page.getByRole("contentinfo", { name: "Estado del sistema" });
  await expect(bar.getByText("Motor activo")).toBeVisible();
  await bar.getByRole("button", { name: "Detener" }).click();
  // Open positions: the owner is told they stay protected by their broker stop.
  const confirm = page.getByRole("dialog", { name: "¿Detener el motor con posiciones abiertas?" });
  await expect(confirm.getByText(/stop en Alpaca/)).toBeVisible();
  await confirm.getByRole("button", { name: "Detener igualmente" }).click();
  await expect(bar.getByText("Motor detenido")).toBeVisible();
  // Inicio warns that nothing is being evaluated and offers to start it.
  await expect(page.getByText("El motor de simulación está detenido")).toBeVisible();
  await bar.getByRole("button", { name: "Iniciar motor" }).click();
  await expect(bar.getByText(/Motor activo/)).toBeVisible();
});

test("emergency flatten asks for confirmation and can be cancelled", async ({ page }) => {
  await page.goto("/?fixture=demo#/inicio");
  const bar = page.getByRole("contentinfo", { name: "Estado del sistema" });
  await bar.getByRole("button", { name: "Cerrar posiciones" }).click();
  const dialog = page.getByRole("dialog", { name: "¿Cerrar todas las posiciones?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cerrar posiciones" }).click();
  await expect(bar.getByText("Cerrando posiciones…")).toBeVisible();
  await bar.getByRole("button", { name: "Cancelar" }).click();
  await expect(bar.getByText("Cerrando posiciones…")).toHaveCount(0);
});

test("a change proposal is created with its evidence and needs a reason to move", async ({ page }) => {
  await page.goto("/?fixture=demo#/inteligencia/aprendizaje");
  await page.getByRole("button", { name: "Proponer un cambio" }).click();
  const dialog = page.getByRole("dialog", { name: "Nueva propuesta de cambio" });
  await dialog.getByRole("button", { name: "Crear propuesta" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Elige el agente.");

  await dialog.getByLabel("Agente").click();
  await page.getByRole("option").first().click();
  await dialog.getByLabel("Versión candidata").fill("9.9.9");
  await dialog.getByLabel("Cambio propuesto").fill("Exigir tendencia confirmada");
  await dialog.getByLabel("Motivo").fill("Demasiadas entradas en rango lateral");
  await dialog.getByLabel("Evidencia").fill("12 pérdidas en lateral");
  await dialog.getByLabel("Reglas afectadas").fill("Elegibilidad por régimen");
  await dialog.getByLabel("Mejora esperada").fill("Menos entradas falsas");
  await dialog.getByLabel("Riesgo").fill("Menos operaciones");
  await dialog.getByRole("button", { name: "Crear propuesta" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Exigir tendencia confirmada").or(page.getByText("Demasiadas entradas en rango lateral")).first()).toBeVisible();
});

test("subscription login shows progress and connects", async ({ page }) => {
  await page.goto("/?fixture=demo#/inteligencia/modelos");
  // The demo has one paused subscription (quota) and one that still needs login.
  await expect(page.getByText(/En pausa: se agotó el cupo/)).toBeVisible();
  const login = page.getByRole("button", { name: "Iniciar sesión" }).first();
  await login.click();
  await expect(page.getByText(/Se abrió el navegador|Preparando el inicio de sesión/)).toBeVisible();
  await page.waitForTimeout(4_500);
  await expect(page.getByText(/conectado/).first()).toBeVisible({ timeout: 10_000 });
});

test("an optimizer change is approved, shows in the history and can be undone", async ({ page }) => {
  await page.goto("/?fixture=demo#/inteligencia/aprendizaje");
  const card = page.getByRole("listitem").filter({ hasText: "Cierre por tiempo a 120 min" });
  await expect(card.getByLabel("Resultado de la prueba antes y después del cambio")).toBeVisible();
  await card.getByRole("button", { name: "Aprobar y aplicar" }).click();
  const approve = page.getByRole("dialog", { name: /Aprobar y aplicar/ });
  await expect(approve.getByLabel("Motivo")).toHaveValue("Aprobado tras revisar la prueba");
  await approve.getByRole("button", { name: "Aprobar y aplicar" }).click();
  await expect(page.getByText("Cambio aplicado", { exact: true })).toBeVisible();
  await expect(page.getByText("Nada por revisar")).toBeVisible();

  // The agent history shows the new version in use, with what changed and why.
  await page.getByRole("link", { name: "Ver historial" }).first().click();
  const sheet = page.getByRole("dialog", { name: /Retroceso en tendencia/ });
  await expect(sheet.getByText("v1.2.0", { exact: true })).toBeVisible();
  await expect(sheet.getByText("Cierre por tiempo a 120 min (antes 60 min)")).toBeVisible();
  await sheet.getByRole("button", { name: "Deshacer este cambio" }).click();
  const undo = page.getByRole("dialog", { name: /Deshacer/ });
  await undo.getByLabel("Motivo").fill("Prefiero la versión anterior");
  await undo.getByRole("button", { name: "Deshacer" }).click();
  await expect(page.getByText("Cambio deshecho", { exact: true })).toBeVisible();
});

test("the optimizer can be asked to look for improvements", async ({ page }) => {
  await page.goto("/?fixture=demo#/inteligencia/aprendizaje");
  await expect(page.getByText("Encontró una mejora y la dejó para que la revises.")).toBeVisible();
  await page.getByRole("button", { name: "Buscar mejoras" }).click();
  await expect(page.getByText("Buscando mejoras").first()).toBeVisible();
});

test("a news source is switched on from Noticias with its address and review", async ({ page }) => {
  await page.goto("/?fixture=demo#/noticias/fuentes");
  await page.getByRole("switch", { name: "Usar noticias" }).click();
  await page.getByRole("switch", { name: "Usar Reserva Federal · noticias" }).click();
  const dialog = page.getByRole("dialog", { name: "Activar Reserva Federal · noticias" });
  const activate = dialog.getByRole("button", { name: "Activar" });
  await dialog.getByLabel("Dirección RSS").fill("https://www.federalreserve.gov/feeds/press_all.xml");
  await expect(activate).toBeDisabled(); // the terms review is required
  await dialog.getByRole("checkbox").click();
  await activate.click();
  await expect(page.getByText("https://www.federalreserve.gov/feeds/press_all.xml")).toBeVisible();
  await page.getByRole("button", { name: "Validar y aplicar" }).click();
  await expect(page.getByText("Configuración aplicada", { exact: true })).toBeVisible();
});

test("a risk profile is chosen with its dollar amounts on the real account", async ({ page }) => {
  await page.goto("/?fixture=demo#/riesgo/limites");
  const profiles = page.getByRole("radiogroup", { name: "Perfil de riesgo" });
  await expect(profiles.getByRole("radio", { name: /Medio/ })).toHaveAttribute("aria-checked", "true");
  await expect(profiles.getByRole("radio", { name: /Medio/ })).toContainText("$501");
  await profiles.getByRole("radio", { name: /Alto/ }).click();
  await page.getByRole("button", { name: "Validar y aplicar" }).click();
  await expect(page.getByText("Configuración aplicada", { exact: true })).toBeVisible();
});

test("the price chart switches type and interval like a trading terminal", async ({ page }) => {
  await page.goto("/?fixture=demo#/trading/mercado");
  await page.getByRole("radiogroup", { name: "Temporalidad" }).getByRole("radio", { name: "1H" }).click();
  await expect(page.getByText(/en \d+ días/)).toBeVisible();
  for (const type of ["Barras", "Heikin Ashi", "Línea", "Área", "Velas"]) {
    await page.getByRole("radiogroup", { name: "Tipo de gráfica" }).getByRole("radio", { name: type }).click();
    await expect(page.getByRole("img", { name: "Precio de QQQ" })).toBeVisible();
  }
});

test("positions and operations are one view with open, closed and attention", async ({ page }) => {
  await page.goto("/?fixture=demo#/trading/operaciones");
  const filter = page.getByRole("radiogroup", { name: "Mostrar operaciones" });
  await expect(filter.getByRole("radio", { name: /Requieren atención \(1\)/ })).toBeChecked();
  await filter.getByRole("radio", { name: /Cerradas/ }).click();
  await expect(page.getByText("Objetivo alcanzado")).toBeVisible();
  await filter.getByRole("radio", { name: /Abiertas/ }).click();
  await page.getByRole("row").filter({ hasText: "QQQ" }).first().click();
  const sheet = page.getByRole("dialog", { name: /QQQ · abierta/ });
  await expect(sheet.getByText("Resultado hasta ahora")).toBeVisible();
  await expect(sheet.getByRole("img", { name: /entre el stop y el objetivo/ })).toBeVisible();
});

test("orders and risk rejections live inside Operaciones", async ({ page }) => {
  await page.goto("/?fixture=demo#/trading/operaciones");
  const filter = page.getByRole("radiogroup", { name: "Mostrar operaciones" });
  await filter.getByRole("radio", { name: /Órdenes/ }).click();
  await expect(page.getByText("5 de 5")).toBeVisible();
  await page.getByRole("radiogroup", { name: "Lado" }).getByRole("radio", { name: "Ventas" }).click();
  await expect(page.getByText(/^2 de 5/)).toBeVisible();
  await filter.getByRole("radio", { name: /Frenadas/ }).click();
  await expect(page.getByText("Puntuación por debajo del mínimo del nivel")).toBeVisible();
});

test("the header model button switches the decision model", async ({ page }) => {
  await page.goto("/?fixture=demo#/inicio");
  await page.getByRole("button", { name: /Modelo de IA: Opus 5\.5/ }).click();
  const dialog = page.getByRole("dialog", { name: "Modelo de IA" });
  await expect(dialog.getByText("Sesión de 5 h · disponible")).toBeVisible();
  await dialog.getByRole("combobox", { name: "Modelo de Decisión" }).click();
  await page.getByRole("option", { name: "Sonnet 5" }).click();
  await dialog.getByRole("button", { name: "Guardar" }).click();
  await expect(page.getByText("Modelo guardado", { exact: true })).toBeVisible();
});

test("system health lists what works and offers a fix for the rest", async ({ page }) => {
  await page.goto("/?fixture=demo#/ajustes");
  await page.getByRole("button", { name: "Salud del sistema" }).click();
  const dialog = page.getByRole("dialog", { name: "Salud del sistema" });
  await expect(dialog.getByText("Cuenta de IA")).toBeVisible();
  await dialog.getByRole("button", { name: "Activar" }).click();
  await expect(dialog.getByText("Sigue operando aunque cierres la app", { exact: false })).toBeVisible();
});

test("the monthly report shows business KPIs and exports the trades", async ({ page }) => {
  await page.goto("/?fixture=demo#/trading/informe");
  await expect(page.getByText("Profit factor")).toBeVisible();
  await expect(page.getByText("2.44")).toBeVisible();
  await page.getByRole("button", { name: "Excel (CSV)" }).click();
  await expect(page.getByText("Operaciones exportadas", { exact: true })).toBeVisible();
});

test("the report period is chosen with the app's own pickers (no native date input)", async ({ page }) => {
  await page.goto("/?fixture=demo#/trading/informe");
  await expect(page.locator('input[type="date"], input[type="month"]')).toHaveCount(0);
  await page.getByRole("button", { name: /^Mes:/ }).click();
  await page.getByRole("button", { name: "Anterior" }).click();
  await page.getByRole("button", { name: "ago" }).click();
  await expect(page.getByRole("heading", { name: /agosto de 2025/i })).toBeVisible();
  await page.getByRole("radiogroup", { name: "Periodo" }).getByRole("radio", { name: "Día" }).click();
  await page.getByRole("button", { name: /^Día:/ }).click();
  await page.getByRole("button", { name: "Anterior" }).click();
  await page.getByRole("button", { name: "15", exact: true }).click();
  await expect(page.getByRole("heading", { name: /15 de/ })).toBeVisible();
  await page.getByRole("radiogroup", { name: "Periodo" }).getByRole("radio", { name: "Año" }).click();
  await page.getByRole("button", { name: /^Año:/ }).click();
  await page.getByRole("button", { name: "2025", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Año 2025" })).toBeVisible();
});
