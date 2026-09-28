# opening_range_breakout@1.0.0

## Hipótesis

El primer cierre por encima del máximo de los primeros 30 minutos de la sesión, con volumen alto y sobre el VWAP, tiende a continuar. Sin confirmación de volumen, la respuesta es NO_TRADE.

## Reglas de entrada (todas)

- El rango de apertura ya está formado: el máximo y el mínimo entre las 09:30 y las 10:00 ET, con al menos la mitad de las barras presentes.
- Menos de 180 minutos desde la apertura (antes de las 12:30 ET).
- Es una ruptura **nueva**: el cierre anterior está dentro del rango y el último fuera.
- El volumen de la barra es al menos **1,5 veces** la media de la sesión.
- El precio está sobre el VWAP y a no más de 0,5 ATR del máximo del rango.

## Salida

| Parámetro | Valor |
|---|---|
| Stop | 0,35 % bajo la entrada (y al menos 0,8 ATR) |
| Objetivo | 2R |
| Horizonte | 90 min, recortado al cierre de la sesión |

## Motivos de NO_TRADE

`opening_range_not_formed`, `too_late_for_opening_breakout`, `no_fresh_breakout`, `breakout_extended`, `breakout_without_volume`, `price_below_vwap`, además de los comunes.

## Fuentes

Las rupturas del rango de apertura están documentadas como familia de estrategias intradía. Por ejemplo, Zarattini, Barbon y Aziz (2023), *A Profitable Day Trading Strategy For The U.S. Equity Market* (SSRN 4729284), estudian el ORB de 5 minutos en QQQ. Sus resultados no se asumen: aquí se miden con costes y fuera de muestra.
