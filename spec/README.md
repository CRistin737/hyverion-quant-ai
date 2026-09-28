# Hyverion Quant AI specification

This directory is the operational source of truth for the product. Code, tests and
documentation must preserve the decisions recorded here. Every completed phase updates
`STATUS.md`; any material product or security decision is added to `DECISIONS.md`.

Operator walkthroughs live in [`guides/`](guides/README.md) and are mirrored in the
native **Guías** workspace. The product direction is recorded in
[`HYVERION_AUTONOMOUS_PLATFORM.md`](HYVERION_AUTONOMOUS_PLATFORM.md), trading strategies in
[`strategies/`](strategies/README.md) and the Spanish user guide in
[`docs/es/GUIA.md`](../docs/es/GUIA.md).

## Safety boundary

```text
DATA -> AGENTS -> TRADE PROPOSAL -> CRITIC -> DETERMINISTIC RISK -> EXECUTION -> EXCHANGE
```

AI agents can interpret, compare, criticize and propose. `RiskEngine` is deterministic
and authoritative. `ExecutionEngine` is the only private-exchange boundary. External
news and social text is untrusted data and never an instruction.

## Operating defaults

- App de escritorio Tauri 2 + React en `app/` (build de release verificado; iconos con
  `cd app && pnpm icons`); sin dashboard de navegador. `terminal` (Rich) queda para SSH/VPS.
- Spanish is the single user-facing UI language; English identifiers remain
  stable internally for APIs, schemas and audit records.
- PAPER is the default mode; SHADOW is available; LIVE remains blocked.
- Initial capital is USD 100 and the only executable instrument is QQQ (since 2026-09-27; the
  crypto watchlist was retired, see `docs/migration/`).
- Spot only; leverage and derivatives are disabled by default.
- API secrets live in Keychain locally and a secret manager/environment on VPS.
- `AUTO_PROMOTE_AGENT_CHANGES=false`.

## Navigation and identity

The application content shows only `Hyverion Quant AI`. No descriptive tagline,
numbered page eyebrow or in-page logo is allowed. The logo is reserved for the native
application/Dock icon. Primary workspaces stay in the top bar; secondary workspaces are
available from the `Más`/`More` menu.

## Implementation order

1. Contracts and provider connection center.
2. Source governance and safe ingestion.
3. Context assembly and complete agent orchestration.
4. Paper/shadow evaluation and learning supervisor.
5. VPS/sandbox readiness. LIVE requires a separate audit and explicit authorization.
