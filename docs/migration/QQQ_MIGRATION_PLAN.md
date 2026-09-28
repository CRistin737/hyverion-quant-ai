# Plan de migración a QQQ

La migración se hace en tres tandas. Cada fase termina operativa y con las comprobaciones en verde: `ruff`, `mypy`, `pytest`, la verificación del plan, `pnpm typecheck/test/visual/e2e` y `cargo test`.

## Tanda 1: fases 0–5 (hecha el 2026-09-27; falta la verificación con claves de Alpaca)

| Fase | Entrega | Hecho cuando |
|---|---|---|
| 0. Auditoría y respaldo | `CRYPTO_TO_QQQ_AUDIT.md`, este plan, la arquitectura objetivo y un respaldo en `backups/pre-qqq/` | Documentos en el repositorio y línea base de tests registrada. |
| 1. Generalización del núcleo | `Instrument`, `BrokerAdapter`, `EquityMarketDataProvider`, configuración v2 con migración, lista blanca QQQ y borrado de Binance y derivados | Hyverion arranca sin Binance (§147.1). QQQ es el único símbolo ejecutable (§147.2). El broker es intercambiable (§147.3). Las credenciales de paper y de live no se pueden confundir (§147.20). |
| 2. Sesión de EE. UU. | `MarketClock` (XNYS), día de PnL en hora de Nueva York, puertas de sesión en `RiskEngine`, cierre antes del final del día y máximo de operaciones al día | Pasan los tests de horario de verano, festivos y cierres tempranos (§147.6). |
| 3. Datos de QQQ | `AlpacaMarketData`, fixture, VWAP/ATR/RSI/EMA y auditoría de datos | La gráfica y el motor usan la factoría. Sin claves, el sistema falla cerrado y lo dice claro. |
| 4. Alpaca Paper | `AlpacaPaperBroker`, reconciliación al arrancar, `broker status`/`reconcile`, `doctor` y documentación de IBKR | Con las claves del dueño, `broker status` muestra PAPER y la reconciliación sale OK (§147.4 y §147.21). |
| 5. Estrategias base | Tres plugins de sesión, régimen ampliado, costes unificados, backtest con walk-forward y motivos de NO_TRADE | La línea base corre con el fixture y, con claves, con 20 sesiones reales (§147.12, en parte). |

**Pendiente del dueño en esta tanda:** crear la cuenta de Alpaca Paper y guardar sus claves en el Llavero desde la app. Los pasos están en `spec/guides/broker-paper.md`.

## Tanda 2: fases 6–11 (hecha el 2026-09-28)

| Fase | Contenido |
|---|---|
| 6. Nasdaq-100 | Universo point-in-time (`index_constituent_membership`), pesos, `NasdaqBreadthEngine`, `QQQContributionEngine` y `MegaCapLeadershipAgent`. |
| 7. Macro | `MacroCalendarService` con Fed, BLS y BEA; `MacroRiskGate`; marcas de tiempo de publicación. |
| 8. Noticias, SEC y resultados | `SECFilingService`, `EarningsAgent`, deduplicación (`news_cluster_id`), resolución de entidades, decaimiento y `SourceTrustRegistry`. |
| 9. Multiagente | Especificaciones nuevas, `EvidenceGraph`, `ConfluenceEngine`, `DataQualityScore` y activación por eventos. |
| 10. Volatilidad y opciones | `VolatilityAgent` y `OptionsDataProvider`, que funciona también sin datos (estado `unavailable`). Sin ejecución de opciones. |
| 11. Memoria | Metadatos de renta variable, memoria por eventos, `HistoricalAnalogEngine` y fiabilidad de los agentes por contexto. |

## Tanda 3: fases 12–15 (hecha el 2026-09-28)

| Fase | Contenido |
|---|---|
| 12. QQQ Command Center | Paneles de pulso, amplitud, macro, evidencia, heatmap, agentes, broker y laboratorio de investigación. |
| 13. Replay y backtest | Point-in-time, supervivencia, costes, walk-forward, Monte Carlo y `PaperRealityAdjustment`. |
| 14. Campaña paper | Un experimento sostenido, con incidentes e informe diario de paper frente a la realidad. |
| 15. Preparación para live | Solo un informe. LIVE nunca se activa automáticamente. |

### Cómo quedó cada fase (2026-09-28)

| Fase | Entrega |
|---|---|
| A. Capital del broker | El patrimonio es el de Alpaca Paper (`BROKER_ACCOUNT_SYNC` en cada conciliación); `broker_equity_unavailable` y `broker_not_connected`; el simulador pasa a ser interno; la CLI y la app comparten configuración y base |
| B. Fuentes gratuitas | `sources/` con `SafeFetcher`, registro de confianza, claves en el Llavero, Ajustes › Fuentes de datos con «Probar», y guía `spec/guides/fuentes-de-datos.md` |
| 6. Nasdaq-100 | `universe/nasdaq100.py` (N-PORT de la SEC, *point-in-time* por fecha de presentación, pesos estimados) y `universe/breadth.py` (amplitud, contribución, divergencias) |
| 7. Macro | `macro/calendar.py` (Fed JSON, BLS ICS, BEA), `macro/gate.py` (MacroRiskGate), `macro/service.py` (con `first_seen_at`) y `macro/rates.py` (FRED) |
| 8. Noticias, SEC y resultados | `news/pipeline.py` (clústeres, relevancia por peso, decaimiento) y `news/filings.py` (8-K con ítem 2.02 = resultados; Finnhub) |
| 9. Multiagente | 17 `AGENT.md`, `agents/tools.py`, `intelligence/evidence.py` (EvidenceGraph, Confluence, DataQuality, ConsensusMatrix), crítico con inteligencia y `hyverion_strategy@0.1.0` |
| 10. Volatilidad y opciones | `options/volatility.py`: realizada frente a implícita, ATM, skew 25Δ, estructura temporal, movimiento esperado y VIX. Estado `unavailable` si faltan datos |
| 11. Memoria | `intelligence/analogs.py` (análogos y memoria de eventos) y fiabilidad de agentes por régimen y franja horaria |
| 12. Centro QQQ | Inteligencia › Centro QQQ y Aprendizaje › Informes |
| 13. Replay | Monte Carlo, intervalo de confianza de la expectativa, regla macro en el replay, Hyverion Strategy incluida (hasta 120 sesiones) y `simulation/reality.py` |
| 14. Campaña paper | `reports/campaign.py` (informe diario §98, §133 y §134), API y CLI `report daily` |
| 15. Preparación para live | Lista §105 con evidencia; el veredicto es siempre `NOT_READY` y LIVE no se puede activar |

**Pendiente del dueño:**
- Escribir el **email de contacto** (sin él no se leen el calendario del BLS ni la SEC, y la regla macro no deja abrir operaciones).
- Opcional: las claves gratuitas de **FRED** y **Finnhub**.

## Reglas de la migración

- No se borran datos. Las filas cripto de la base son historia auditada.
- Los valores de riesgo se migran sin cambios. Solo se añaden límites que restringen más.
- No se habilita LIVE, ni cortos, ni horario extendido, ni opciones.
- Las credenciales nunca se piden por chat. El dueño las guarda en el Llavero.
