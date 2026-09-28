# Auditoría de migración: cripto/Binance → QQQ

Fecha: 2026-09-27. Rama: `migration/qqq-equities`. Estado inicial verificado antes de tocar código: `ruff` y `mypy` limpios, `pytest` 534 pasados y 6 omitidos.

Respaldo previo (fuera de Git, en `backups/pre-qqq/`):
- la base SQLite, verificada con `trading_bot backup`;
- `config/` del repositorio;
- `config/local.yaml` de la app (`~/Library/Application Support/Hyverion Quant AI/config`);
- `.env.example`.

## Conclusión

El núcleo de Hyverion ya es casi independiente del activo. Las tablas guardan `asset` como texto libre, y `RiskEngine`, `ExecutionEngine`, la escalera de beneficio, el ciclo de vida de las operaciones, la memoria de tres capas con `as_of`, las versiones de componentes y el `ModelRouter` no saben qué es Binance.

El acoplamiento cripto está en cuatro franjas:
1. **Datos y ejecución:** los adaptadores Binance, que se instancian a mano en `main.py` y en `control_api.py`.
2. **Configuración:** símbolos `BASE/QUOTE`, `exchange_id` y `market_type`.
3. **Supuestos 24/7:** día en UTC, volumen de 24 horas y ningún calendario.
4. **Derivados y noticias cripto.**

**Decisión del dueño (2026-09-27):** borrar el código Binance/cripto. Los datos históricos de la base no se tocan.

## Leyenda

| Clase | Significado |
|---|---|
| KEEP | Se conserva sin cambios de lógica. |
| GENERALIZE | Se conserva y se vuelve independiente del activo o del broker. |
| REPLACE | Se sustituye por un módulo de renta variable de EE. UU. |
| DELETE | Se borra en esta tanda (decisión del dueño). |
| DEPRECATE | Queda, pero fuera de la ruta activa. |

## Inventario

### Ejecución y broker (`src/trading_bot/exchange/`)

| Módulo | Clase | Motivo y destino |
|---|---|---|
| `exchange/execution.py` `ExecutionEngine` | GENERALIZE | Es el único que envía órdenes y tiene autorización por decisión, idempotencia y `SubmissionStateUnknown`. Gana la lista blanca de ejecución (`SYMBOL_NOT_EXECUTION_WHITELISTED`) y pasa a `broker/`. |
| `exchange/base.py` (`ExecutionResultPort`, `MarketDataPort`, `CancelReplacePort`) | GENERALIZE → `BrokerAdapter` y `EquityMarketDataProvider` | Protocolos de la §9 y de la §64, con capacidades declaradas. |
| `exchange/paper.py` `PaperExecutionAdapter` | GENERALIZE → `broker/simulator.py` | Simulador interno con capacidades y el modelo de costes unificado. |
| `exchange/lifecycle.py` | KEEP (se mueve) | Máquina de estados de la operación, SAFE_MODE y RECOVERY_REQUIRED. |
| `exchange/reconciliation.py` | KEEP (se mueve) | Compara saldos, órdenes, posiciones y fills. |
| `exchange/models.py` | GENERALIZE | `Fill` y `OrderResult` sirven; se quita la conversión de comisiones cripto. |
| `exchange/binance_private.py` | DELETE | Adaptador Spot Testnet. Lo sustituye `broker/alpaca.py` (Paper). |
| `exchange/binance_public.py` | DELETE | Datos públicos de Binance. Lo sustituye `market_data/alpaca.py` más el fixture. |
| `exchange/sandbox_verification.py` | DELETE | Verificación de testnet y `USDT→USD`. La sustituye la reconciliación del broker. |

### Configuración (`src/trading_bot/config/`, `config/*.yaml`, `.env.example`)

| Elemento | Clase | Motivo y destino |
|---|---|---|
| `AppConfig.timezone: Literal["UTC"]` | KEEP | Todo se guarda en UTC. La hora de Nueva York solo se usa en `MarketClock`. |
| `TradingConfig.market_type`, `leverage_enabled` | REPLACE | `asset_class: us_equity`, `shorting_enabled=false`, `allow_overnight=false`, `premarket_trading=false`, `after_hours_trading=false`, `options_trading=false`. |
| `TradingConfig.allowed_symbols` (BTC/ETH) | REPLACE | `primary_instrument: QQQ`. Los símbolos pasan a ser tickers simples dentro de la lista blanca. |
| `ExchangeConfig` (binance, URLs de binance.vision) | REPLACE | `BrokerConfig {provider: simulator\|alpaca, environment: paper}` y `MarketDataConfig`. |
| `RiskConfig` | KEEP y amplía | Sus valores no cambian (§45). Se añaden límites que restringen más: `max_trades_per_day=3`, `opening_no_trade_minutes` y `eod_flatten_minutes_before_close`. |
| `config/manager.py` (`BASE/QUOTE`, «solo binance») | GENERALIZE | Valida tickers y el proveedor de broker. |
| — | nuevo | `config_version: 2` y `config/migrate.py`, que migra v1 → v2 con copia `.bak`. |
| `SecretSettings.exchange_api_*` y el Llavero `exchange:api_*` | REPLACE | `broker:alpaca_paper:key_id` y `broker:alpaca_paper:secret_key`. No existe ningún nombre de secreto de live. |

### Supuestos 24/7

| Lugar | Clase | Destino |
|---|---|---|
| `db/state.py` (`session_date = ts.date()` UTC), `db/repositories.py:177`, `control_api.py` (día de PnL) | REPLACE | La sesión de NYSE en hora de Nueva York, vía `MarketClock.session_id`. |
| `main.py` `_run_forever` y stream sin horario | REPLACE | Esperan a la sesión: sin nuevas entradas fuera de la sesión regular; la protección sigue activa. |
| `simulation/strategy_replay.py` `MINUTES_PER_DAY=1440` | REPLACE | 390 minutos de sesión regular. |
| `learning/optimizer_job.py` `HISTORY_MINUTES` (14×24×60) | REPLACE | `HISTORY_SESSIONS`. |
| `schemas/trading.py` `base/quote_volume_24h` | REPLACE | `session_volume` y `session_dollar_volume`. |
| `core/context.py` (30 s de antigüedad máxima) | KEEP | Correcto en sesión. Con el mercado cerrado no se piden decisiones de entrada. |
| `risk/profit_ladder.py` y `session_guardian.py` (latch por fecha UTC) | GENERALIZE | El latch va por sesión de Nueva York. |

### Inteligencia

| Módulo | Clase | Motivo y destino |
|---|---|---|
| `agents/*` salvo `derivatives` | KEEP | Especificaciones neutrales respecto al proveedor. En la tanda 2 se añaden breadth, mega-caps, macro, rates, earnings/SEC y volatilidad. |
| `agents/derivatives/`, `DerivativesAssessment`, `DerivativesSnapshot`, `DerivativesIntelligence`, `SignalComponents.derivatives`, `external_data.derivatives_provider` | DELETE | Funding, open interest y liquidaciones son cripto. El hueco lo cubre Volatilidad/Opciones como sensor (fase 10). |
| `strategies/trend_momentum.py`, `breakout.py`, `regime_mean_reversion.py` | REPLACE | `trend_pullback`, `opening_range_breakout` y `mean_reversion`, con VWAP y ATR de sesión y costes del broker. |
| `strategies/regime.py` | GENERALIZE | Régimen primario, etiquetas secundarias y confianza, con los estados de la §6, unificado con `MarketRegime`. |
| `data/features.py` (SMA 3/5) | GENERALIZE | Añade VWAP de sesión, ATR, RSI y EMA, todo determinista. |
| `data/sources.py` `NEWS_ALLOWLIST` | GENERALIZE | Quedan SEC, CFTC, Fed, Reuters, AP y FT. Salen bitcoin, ethereum, binance, coinbase, coindesk y the-block. |
| `data/intelligence.py` (palabras «airdrop», «liquidation») | GENERALIZE | Vocabulario de renta variable. |
| `data/external_collector.py` (`asset.split("/")[0]`) | GENERALIZE | Usa el ticker directamente. |
| `providers/*` `ModelRouter` | KEEP | Independiente del activo (§58). |
| `memory/*` (tres capas, `as_of`) | KEEP | Se amplía con metadatos de renta variable en la tanda 2 (§54). |
| `learning/*` (versiones, aplicar y deshacer, optimizador) | KEEP y generaliza la fuente de datos | El optimizador recibe barras de la factoría de datos. |
| `simulation/costs.py` y cuatro modelos de costes dispersos | REPLACE | Un único modelo de costes, con escenarios optimista, base y estrés (§101). |

### Monitorización, API y CLI

| Elemento | Clase | Destino |
|---|---|---|
| `monitoring/readiness.py` (`VENUE_EXCLUDED_REGIONS` de Binance, `spot_no_leverage`) | GENERALIZE | Comprobaciones del broker paper, del instrumento y de «sin cortos». La elegibilidad por región del broker se verifica antes de LIVE (§142). |
| `POST /api/v1/exchange/sandbox/reconcile` | REPLACE | `GET /api/v1/broker/status` y `POST /api/v1/broker/reconcile`. |
| `GET /api/v1/market/candles` (Binance) | GENERALIZE | Usa la factoría de datos. |
| `main.py` `setup`, `doctor`, `collect` y `paper` (BTC/USDT) | GENERALIZE | Broker (Simulador / Alpaca Paper), QQQ fijo, `doctor` con el formato de la §96, y `broker status`, `broker reconcile` y `market status`. |

### Base de datos

| Elemento | Clase | Motivo |
|---|---|---|
| Tablas de auditoría (`asset` texto) | KEEP | Ya son genéricas. |
| `exchange_connections` | DEPRECATE | Queda sin uso. Se decidirá en la tanda 2, junto con `brokers` y `broker_accounts` (§61). |
| Filas con BTC/ETH | KEEP | Son historia auditada y no se borran. |
| `daily_pnl.session_date` | GENERALIZE | Las filas nuevas usan la sesión de Nueva York; las antiguas se quedan como están. |

### App (`app/`)

| Elemento | Clase | Destino |
|---|---|---|
| Onboarding (símbolos cripto, «Binance · testnet») | REPLACE | Capital, broker, IA y revisión, con QQQ fijo. |
| Ajustes › Cuenta de trading (claves testnet) | REPLACE | Broker y claves de Alpaca Paper en el Llavero. |
| Riesgo › `TestnetCheck` | REPLACE | «Verificar cuenta paper». |
| Fuentes («Precios públicos de Binance», derivados) | GENERALIZE | Fuente de mercado del broker; la sección de derivados sale. |
| `labels.ts`, `regions.ts`, fixtures (BTC/ETH) | GENERALIZE | QQQ. |
| `src-tauri/src/lib.rs` `FIXED_SECRET_NAMES` | REPLACE | Nombres de Alpaca Paper. |

### Tests

Unos 43 archivos usan BTC/USDT, y los que más dependen de ello son `test_trade_views`, `test_intelligence`, `test_pipeline_components` y `test_state`. Se migran a QQQ con precios de unos 500 USD. `test_binance_private.py` se borra y su cobertura pasa a los tests del adaptador de Alpaca, simulado con `respx`.

## Riesgos detectados

1. **Cantidades mínimas.** Con $100–$1000 de capital, un riesgo de $0,25–$2,50 y QQQ a unos 500 USD, el tamaño por riesgo puede quedar en menos de 1 acción. Solo se opera si el broker admite fraccionales con el tipo de orden elegido. Si no, se rechaza con `QUANTITY_BELOW_MINIMUM`, sin forzar el tamaño.
2. **Datos gratuitos.** El feed IEX de Alpaca cubre solo una parte del volumen consolidado. Hay que marcarlo en el linaje y no confundirlo con SIP.
3. **Día de PnL.** Al pasar de UTC a la hora de Nueva York, el día en curso durante el cambio puede partirse. Queda documentado y no se reescriben filas.
4. **Paper ≠ live** (§139). La corrección de realismo (`PaperRealityAdjustment`) llega en la fase 13.
