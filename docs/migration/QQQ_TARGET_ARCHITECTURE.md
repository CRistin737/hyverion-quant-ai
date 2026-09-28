# Arquitectura objetivo: Hyverion QQQ

Hyverion **opera un único instrumento, QQQ**, y **observa** el Nasdaq-100, la macro, los tipos de interés, la volatilidad y las noticias como sensores. Ningún otro símbolo se puede ejecutar.

## Flujo de una decisión

```text
                    MARKET CLOCK (XNYS, America/New_York)
                               │  ¿sesión regular? ¿corte de fin de día?
                               ▼
      EquityMarketDataProvider ──► FEATURES (VWAP de sesión, ATR, RSI, EMA, régimen)
      (Alpaca IEX │ fixture)            │
                                        ▼
                                 EVENT DETECTOR ── nada relevante → sin LLM
                                        │
                                        ▼
                     AGENTES (solo lectura, salida estructurada)
                                        │
                                        ▼
                    STRATEGY (plugins cuantitativos) → TradeProposal
                                        │
                                        ▼
                                     CRITIC
                                        │
                                        ▼
         RISK ENGINE (determinista: sesión, lista blanca QQQ, escalera de beneficio,
                      límites de pérdida, máximo de operaciones, costes)
                                        │
                                        ▼
         EXECUTION ENGINE (el único que ordena; idempotente; lista blanca otra vez)
                                        │
                                        ▼
               BrokerAdapter ── SimulatedBroker │ AlpacaPaperBroker │ (IBKR paper, tanda 2+)
                                        │
                                        ▼
                                       QQQ
```

## Módulos

| Capa | Módulo | Responsabilidad |
|---|---|---|
| Instrumentos | `core/instruments.py` | `Instrument` y el registro. `EXECUTION_WHITELIST = {"QQQ"}`; el resto es `observation_only`. |
| Reloj de mercado | `market/clock.py` | `MarketClock` sobre `exchange_calendars` (XNYS): estados de sesión, franjas horarias, `session_id` en hora de Nueva York, próxima apertura y próximo cierre. Nunca usa desfases fijos. |
| Datos de mercado | `market_data/base.py`, `alpaca.py`, `fixture.py` | `EquityMarketDataProvider` con linaje (`provider`, `feed`, `is_delayed`). La factoría `build_market_data` es la única que crea proveedores. |
| Broker | `broker/base.py`, `simulator.py`, `alpaca.py` | `BrokerAdapter` y `BrokerCapabilities`. Paper y live son perfiles separados; el adaptador de Alpaca rechaza cualquier host que no sea el de paper. |
| Ejecución | `broker/execution.py` | `ExecutionEngine`: autorización por decisión, lista blanca, idempotencia por `client_order_id` y consulta al broker antes de reintentar. |
| Riesgo | `risk/*` | Determinista y con la autoridad final. Suma puertas de sesión y máximo de operaciones al día. |
| Estrategias | `strategies/*` | `trend_pullback`, `opening_range_breakout` y `mean_reversion`, cada una con su `SPEC.md` y versión. |
| Simulación | `simulation/*` | Replay y backtest de sesión regular con modelo de costes único, escenarios optimista, base y estrés, y auditoría de datos. |
| Memoria | `memory/*` | Tres capas (trabajo, histórica y estratégica), con `as_of` point-in-time. |
| IA | `providers/*`, `agents/*` | `ModelRouter` independiente del proveedor. Los agentes nunca reciben herramientas de broker. |

## Frontera de seguridad

- Los agentes, el optimizador y las noticias no importan `broker.*` (test de arquitectura).
- La lista blanca QQQ se comprueba dos veces: como motivo de rechazo en `RiskEngine` y como autorización en `ExecutionEngine`.
- El modo LIVE sigue bloqueado: `trading_bot live` sale con código 2 y ningún secreto de live tiene nombre en el Llavero.
- Por defecto el broker es `simulator`. Alpaca Paper solo se activa por elección explícita del dueño.
- Fuera de la sesión regular no hay entradas nuevas. Los stops y el cierre antes del final del día siguen activos.

## Almacenamiento

| Qué | Dónde |
|---|---|
| Estado operativo y auditoría | SQLite, y PostgreSQL cuando se configure |
| Series de alta frecuencia | Parquet y DuckDB (`data/storage.py`) |
| Memoria de trabajo | En proceso o Redis |
| Conocimiento | Bóveda Markdown (`data/knowledge`) |

No se añade ninguna base de datos nueva.
