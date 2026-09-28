# trend_pullback@1.0.0

## Hipótesis

En una sesión alcista, un retroceso corto hasta la EMA rápida o el VWAP que vuelve a subir suele continuar la tendencia. Perseguir un movimiento ya extendido no tiene ventaja.

## Reglas de entrada (todas)

- Al menos 30 barras de la sesión, y VWAP y ATR disponibles.
- El último precio está **por encima del VWAP** de la sesión.
- La **EMA(9) está por encima de la EMA(21)** (tendencia intradía).
- La distancia al VWAP es de **1,5 ATR o menos** (no extendido).
- En las 5 barras previas, el mínimo tocó la zona de soporte, `max(EMA9, VWAP) + 0,25 ATR` (retroceso).
- La última barra cierra por encima de la anterior (el retroceso vuelve a subir).
- RSI(14) por debajo de 70.

## Salida

| Parámetro | Valor |
|---|---|
| Stop | 0,4 % bajo la entrada, y al menos 0,8 ATR (fuera del ruido) |
| Objetivo | 2R |
| Horizonte | 60 min, recortado al cierre de la sesión |

Además, la posición se cierra antes del final del día.

## Invalidaciones

- Cierre por debajo del VWAP.
- EMA rápida por debajo de la lenta.
- Stop alcanzado.
- Cierre de la sesión.

## Motivos de NO_TRADE

`insufficient_session_bars`, `vwap_unavailable`, `atr_unavailable`, `ema_unavailable`, `price_below_vwap`, `no_intraday_uptrend`, `extended_from_vwap`, `no_pullback`, `pullback_not_resuming`, `overbought`, `stop_inside_noise`, `expected_edge_below_cost`.

## Fuentes

El patrón «retroceso en tendencia» y el VWAP como referencia intradía de valor medio están ampliamente documentados en la literatura de microestructura. Por ejemplo, Berkowitz, Logue y Noser (1988), *The Total Cost of Transactions on the NYSE*, introdujeron el VWAP como referencia de ejecución.

Esta hipótesis **no** tiene rentabilidad demostrada en QQQ. Se valida con replay y paper antes de ampliarla.
