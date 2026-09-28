# Estrategias, plugins y replay

Hyverion no tiene una estrategia mágica. Cada familia es un plugin determinista
que produce una `TradeProposal`; ninguna puede enviar órdenes ni saltarse el
`Critic` o `RiskEngine`.

## Plugins disponibles (QQQ, desde el 2026-09-27)

Todas las estrategias operan solo QQQ, solo en largo y solo en el horario regular. La especificación de cada una está en [`spec/strategies/`](../strategies/README.md).

| Plugin | Estado | Qué hace |
|---|---|---|
| `trend_pullback` | **Único habilitado por defecto** | Compra un retroceso confirmado sobre el VWAP con la EMA rápida por encima de la lenta. |
| `opening_range_breakout` | Deshabilitado hasta tener evidencia | Compra la primera ruptura del rango de los primeros 30 minutos, con volumen alto. |
| `mean_reversion` | Deshabilitado hasta tener evidencia | Compra una caída estirada bajo el VWAP, solo en régimen lateral. |
| `trend_momentum` | Línea base, solo precios | Sirve de comparación. |

Cuando no hay operación, cada plugin deja sus motivos (`last_no_trade`) y el motor los guarda como evento `NO_TRADE`.

Cuando hay varios plugins habilitados, `StrategyEnsemble` desempata por el orden de la configuración y elige la propuesta con la puntuación determinista más alta. El ensemble no aumenta el presupuesto de riesgo. `breakout` y `regime_mean_reversion` se retiraron en la migración, aunque su historial de versiones se conserva.

## Habilitación segura

1. Ejecuta replay con datos separados en train, validation y test.
2. Incluye fees, spread, slippage y latencia; nunca uses fills perfectos.
3. Compara expectancy, drawdown, profit factor, MFE/MAE y coste sobre beneficio.
4. Registra el experimento y una `ChangeProposal`; no edites prompts ni pesos en
   producción desde el plugin.
5. Revisa el resultado y modifica `config/strategies.yaml` solo después de que
   exista evidencia OOS reproducible.

La configuración puede reconocer los tres plugins, pero la validación no implica
que sean rentables ni que deban activarse. Si no hay evidencia suficiente, el
resultado correcto es `NO_TRADE`.

## Per-strategy evidence on real data (2026-09-26, cripto retirado — histórico)

`POST /api/v1/backtest/strategies` (sin pantalla propia desde 2026-09-27: lo usa el optimizador diario, `learning/optimizer_job.py`, y sus pruebas se ven como evidencia en Aprendizaje › Cambios) downloads up to
1000 public 1-minute candles and replays every enabled plugin bar by bar with the live cycle's feature
window, the same `ShadowSimulator` costs and one position at a time. Only candles up to each bar are
visible (a test mutates the future and asserts identical earlier decisions). Headline numbers are the
out-of-sample 30%. Critic and RiskEngine gates are not applied: it measures the strategy alone.
`/api/v1/backtest/run` no longer replays invented prices; without `prices` it returns `prices_required`.

First run (2026-09-26, BTC/USDT and ETH/USDT, ~16 h of 1m candles): `trend_momentum` lost in both
samples (0-20% win rate) and every exit was the 1 h horizon: a 1% stop / 2% target is rarely reached in
an hour on this data, so fees decide the result. Any parameter change goes through a reviewed
`ChangeProposal`; nothing is tuned automatically.

## Champion / challenger (2026-09-26, cripto retirado — histórico)

`POST /api/v1/backtest/challenger` (API de investigación; en la app, el optimizador hace esta comparación de forma automática y acotada) replays the
production exit geometry (`ExitParams`: stop %, reward multiple, horizon; defaults unchanged in each
plugin's `DEFAULT_EXIT`) and a challenger on the same candles. The verdict uses
`learning.optimizer.ChampionChallenger` on out-of-sample numbers only (≥ 100 OOS trades, higher
expectancy, no worse drawdown or false-positive rate) and lists the failing reasons. It never changes
configuration; a winning challenger is only eligible for a reviewed `ChangeProposal`.

Stops and targets are checked against each candle's low/high with the adverse extreme first, so a bar
touching both counts as a stop. First real runs (quiet market): tighter stops lost more, because a closer
stop means a larger position and higher fees for the same risk budget.

Longer history (2026-09-26): `candles` accepts up to 20 160 (14 days); above 1000 the core pages the
public klines backwards (`fetch_candle_history`, tested for gaps/duplicates at page boundaries). A 7-day
run downloads in ~5 s and replays in under a second.

## Replay de sesión (QQQ)

```bash
uv run python -m trading_bot replay --sessions 20            # Alpaca (necesita las claves paper)
uv run python -m trading_bot replay --sessions 5 --fixture   # datos sintéticos, sin conexión
```

Qué hace el replay (`simulation/strategy_replay.py`):
- Usa solo barras del horario regular, filtradas con `regular_session_only`.
- Audita los datos antes de empezar (`simulation/data_audit.py`); si la auditoría falla, lanza `data_audit_failed` y no se ejecuta.
- Nunca mantiene una posición de un día para otro: la operación termina como muy tarde con `session_close`.
- Calcula los costes en tres escenarios (`simulation/costs.py`): optimista, base y estrés. El resultado principal es el base. `cost_verdict` rechaza una estrategia que solo gana en el optimista (`rejected_only_optimistic`).
- Además del 70/30 dentro y fuera de muestra, divide el periodo en 4 tramos consecutivos por sesión (walk-forward).
- Reporta expectativa, profit factor, R medio, drawdown máximo, costes y número de operaciones.
- Cada ejecución queda registrada como experimento.
