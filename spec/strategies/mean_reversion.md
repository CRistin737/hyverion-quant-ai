# mean_reversion@1.0.0

## Hipótesis

En una sesión **lateral**, una caída muy estirada bajo el VWAP, con RSI bajo y que empieza a girar, tiende a volver hacia el VWAP. **Nunca** se usa contra una tendencia fuerte.

## Reglas de entrada (todas)

- Régimen `ranging`: el mismo clasificador determinista que usan el motor y el replay.
- El precio está a **1,5 ATR o más por debajo del VWAP**.
- RSI(14) de 35 o menos.
- La última barra cierra al alza (la caída se detiene).
- Hay recorrido: el objetivo queda por debajo del VWAP.

## Salida

| Parámetro | Valor |
|---|---|
| Stop | 0,35 % bajo la entrada (y al menos 0,8 ATR) |
| Objetivo | 1,5R |
| Horizonte | 45 min, recortado al cierre de la sesión |

## Motivos de NO_TRADE

`regime_not_ranging`, `stretch_unavailable`, `not_stretched_below_vwap`, `not_oversold`, `still_falling`, `not_enough_room_to_vwap`, además de los comunes.

## Fuentes

La reversión de corto plazo en índices y ETF está documentada. Por ejemplo, Lo y MacKinlay (1990), *When Are Contrarian Profits Due to Stock Market Overreaction?*, así como la literatura sobre reversión intradía hacia el VWAP. Aquí solo se habilita en régimen lateral y se valida fuera de muestra.
