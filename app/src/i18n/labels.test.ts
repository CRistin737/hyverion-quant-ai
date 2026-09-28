import { describe, expect, it } from "vitest";

import { alertMessage, apiError, circuitReason, configError, engineErrorMessage, loginFailure, readinessDetail, whyNow } from "./labels";

describe("backend codes are always shown in Spanish", () => {
  it("translates venue region readiness details, including dynamic ones", () => {
    expect(readinessDetail("venue_region", "No operating region declared; required before any LIVE review.")).toMatch(/No has indicado tu país/);
    expect(readinessDetail("venue_region", "Region DO declared; verify alpaca live eligibility from its official terms before LIVE.")).toMatch(
      /^País DO indicado; antes del modo real/,
    );
  });

  it("maps engine error codes", () => {
    for (const code of ["broker_not_connected", "engine_already_running", "live_not_allowed", "invalid_interval", "spawn_failed", "engine_not_running"]) {
      expect(engineErrorMessage(code)).not.toBe(code);
    }
  });

  it("explains paused subscriptions and failed logins", () => {
    expect(circuitReason("provider_quota_exhausted")).toMatch(/cupo/);
    expect(circuitReason("provider_auth_required")).toMatch(/sesión/);
    expect(loginFailure("login_timeout")).toMatch(/5 minutos/);
  });

  it("translates the new runtime alerts and keeps the detail suffix", () => {
    expect(alertMessage("Open position could not be protected: HTTPStatusError")).toBe("No se pudo proteger una posición abierta: HTTPStatusError");
    expect(alertMessage("Open positions without a running engine")).toMatch(/motor está detenido/);
  });

  it("translates config validation, including dynamic and pydantic-wrapped messages", () => {
    expect(configError("daily loss cap cannot exceed weekly loss cap")).toBe("La pérdida diaria máxima no puede superar la semanal.");
    expect(configError("fallback provider is not supported: foo")).toBe("El respaldo «foo» no está disponible.");
    expect(configError("Value error, operating_region must be a two-letter ISO country code")).toMatch(/código ISO/);
    expect(configError("daily loss cap cannot exceed weekly loss cap; collection backoff cannot be shorter than its interval")).toBe(
      "La pérdida diaria máxima no puede superar la semanal. La espera máxima entre reintentos no puede ser menor que el intervalo.",
    );
  });

  it("routes any API error detail to the right translator", () => {
    expect(apiError("note not found")).toMatch(/ya no existe/);
    expect(apiError("capital_missing")).toMatch(/capital/);
    expect(apiError("memory not found")).toBe("La memoria ya no existe.");
    expect(apiError("SYMBOL_NOT_EXECUTION_WHITELISTED")).toMatch(/solo puede operar QQQ/);
    expect(apiError("market_data_credentials_missing")).toMatch(/Alpaca Paper/);
    expect(apiError("something new")).toBe("something new");
  });

  it("translates legacy English strategy rationale", () => {
    expect(whyNow("Positive trend and momentum alignment on fresh public market data.")).toMatch(/^Tendencia y momentum/);
    expect(whyNow("Texto nuevo")).toBe("Texto nuevo");
  });

});
