import { expect, test } from "@playwright/test";

const ROUTES = [
  ["inicio", "/inicio"],
  ["trading-mercado", "/trading/mercado"],
  ["trading-mercado-spy", "/trading/mercado/SPY"],
  ["trading-operaciones", "/trading/operaciones"],
  ["trading-informe", "/trading/informe"],
  ["noticias-calendario", "/noticias/calendario"],
  ["noticias-noticias", "/noticias/noticias"],
  ["noticias-fuentes", "/noticias/fuentes"],
  ["inteligencia-agentes", "/inteligencia/agentes"],
  ["inteligencia-historial", "/inteligencia/agentes/trend_momentum"],
  ["inteligencia-modelos", "/inteligencia/modelos"],
  ["inteligencia-aprendizaje", "/inteligencia/aprendizaje"],
  ["inteligencia-memoria", "/inteligencia/memoria"],
  ["riesgo-limites", "/riesgo/limites"],
  ["riesgo-decisiones", "/riesgo/decisiones"],
  ["ajustes", "/ajustes"],
  ["ajustes-salud", "/ajustes/salud"],
] as const;

// Old addresses (docs, alerts, habits) and where they land now.
const MOVED = [
  ["/trading/posiciones", "/trading/operaciones"],
  ["/trading/ordenes", "/trading/operaciones"],
  ["/laboratorio/estrategias", "/inteligencia/aprendizaje"],
  ["/laboratorio/boveda", "/inteligencia/memoria"],
  ["/aprendizaje/cambios", "/inteligencia/aprendizaje"],
  ["/aprendizaje/informes", "/trading/informe"],
  ["/inteligencia/mercado", "/noticias/calendario"],
  ["/ajustes/cuenta-ia", "/inteligencia/modelos"],
  ["/ajustes/fuentes", "/noticias/fuentes"],
  ["/ajustes/riesgo", "/riesgo/limites"],
  ["/ajustes/sistema", "/ajustes"],
  ["/riesgo/salud", "/ajustes/salud"],
] as const;

// Freeze time so relative labels ("hace 5 h") and clocks are stable.
test.beforeEach(async ({ page }, testInfo) => {
  await page.clock.install({ time: new Date("2026-09-25T14:02:11Z") });
  if (testInfo.project.name.endsWith("-light")) {
    await page.addInitScript(() => window.localStorage.setItem("hyverion.theme", "light"));
  }
});

for (const [name, route] of ROUTES) {
  test(`${name} renders without errors`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
    await page.goto(`/?fixture=demo#${route}`);
    await expect(page.locator("h1")).toBeVisible();
    await page.waitForLoadState("networkidle");
    await page.clock.runFor(1_000);
    // Nothing may overflow the viewport horizontally.
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: false });
    expect(errors).toEqual([]);
  });
}

test("first run shows onboarding instead of the workspace", async ({ page }) => {
  await page.goto("/?fixture=first-run#/inicio");
  await expect(page.getByRole("heading", { name: "Configura Hyverion" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Principal" })).toHaveCount(0);
  await expect(page).toHaveScreenshot("onboarding.png");
});

test("offline core shows a recoverable error, never a blank screen", async ({ page }) => {
  await page.goto("/?fixture=offline#/inicio");
  await expect(page.getByText("El núcleo local no responde")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "Reintentar" })).toBeVisible();
});

test("command palette opens with ⌘K and navigates", async ({ page }) => {
  await page.goto("/?fixture=demo#/inicio");
  await expect(page.locator("h1")).toHaveText("Inicio");
  await page.keyboard.press("Meta+k");
  await page.getByPlaceholder("Ir a, ejecutar o buscar un símbolo…").fill("riesgo límites");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/riesgo\/limites$/);
});

test("old addresses redirect to where things live now", async ({ page }) => {
  for (const [from, to] of MOVED) {
    await page.goto(`/?fixture=demo#${from}`);
    await expect(page).toHaveURL(new RegExp(`#${to.replace(/\//g, "\\/")}$`));
  }
});

test("mode badge is always visible", async ({ page }) => {
  for (const [, route] of ROUTES.slice(0, 6)) {
    await page.goto(`/?fixture=demo#${route}`);
    await expect(page.locator("header").getByText("SIMULACIÓN")).toBeVisible();
  }
});
