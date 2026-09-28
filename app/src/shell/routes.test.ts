import { describe, expect, it } from "vitest";

import { DOMAIN_LIST, legacyRedirect, tabValues } from "./routes";

describe("information architecture", () => {
  it("has six areas and twelve tabs, each tab with a plain purpose", () => {
    expect(DOMAIN_LIST.map((d) => d.id)).toEqual(["inicio", "trading", "noticias", "inteligencia", "riesgo", "ajustes"]);
    const tabs = DOMAIN_LIST.flatMap((d) => d.tabs ?? []);
    expect(tabs).toHaveLength(12);
    for (const tab of tabs) expect(tab.purpose.length).toBeGreaterThan(20);
    expect(tabValues("trading")).toEqual(["mercado", "operaciones", "informe"]);
    expect(tabValues("noticias")).toEqual(["calendario", "noticias", "fuentes"]);
    expect(tabValues("inteligencia")).toEqual(["agentes", "modelos", "aprendizaje", "memoria"]);
    expect(tabValues("riesgo")).toEqual(["limites", "decisiones"]);
  });

  it("sends old addresses to where each thing lives now", () => {
    expect(legacyRedirect("/trading/posiciones")).toBe("/trading/operaciones");
    expect(legacyRedirect("/trading/ordenes")).toBe("/trading/operaciones");
    expect(legacyRedirect("/laboratorio/estrategias")).toBe("/inteligencia/aprendizaje");
    expect(legacyRedirect("/laboratorio/boveda")).toBe("/inteligencia/memoria");
    expect(legacyRedirect("/aprendizaje/cambios")).toBe("/inteligencia/aprendizaje");
    expect(legacyRedirect("/aprendizaje/memoria")).toBe("/inteligencia/memoria");
    expect(legacyRedirect("/aprendizaje/informes")).toBe("/trading/informe");
    expect(legacyRedirect("/inteligencia/mercado")).toBe("/noticias/calendario");
    expect(legacyRedirect("/inteligencia/fuentes")).toBe("/noticias/fuentes");
    expect(legacyRedirect("/ajustes/cuenta-ia")).toBe("/inteligencia/modelos");
    expect(legacyRedirect("/ajustes/fuentes")).toBe("/noticias/fuentes");
    expect(legacyRedirect("/ajustes/riesgo")).toBe("/riesgo/limites");
    expect(legacyRedirect("/ajustes/ayuda")).toBe("/ajustes");
    expect(legacyRedirect("/riesgo/salud")).toBe("/ajustes/salud");
    expect(legacyRedirect("/ajustes/salud")).toBeNull();
    expect(legacyRedirect("/trading/operaciones")).toBeNull();
    expect(legacyRedirect("/inteligencia/agentes")).toBeNull();
    expect(legacyRedirect("/ajustes")).toBeNull();
  });
});
