# Estrategias de QQQ

Todas las estrategias cumplen estas reglas:

- Son deterministas.
- Solo compran (sin cortos).
- Solo operan en el horario regular de Nueva York.
- Nunca dejan una posición abierta de un día para otro.

Cada una propone; el crítico y el `RiskEngine` deciden. Cuando no hay operación, deja sus motivos (`last_no_trade`, §121) para poder medir qué filtros ayudan.

| Estrategia | Versión | Estado por defecto | Especificación |
|---|---|---|---|
| `trend_pullback` | 1.0.0 | **Activa en paper** | [trend_pullback.md](trend_pullback.md) |
| `opening_range_breakout` | 1.0.0 | Apagada hasta tener evidencia | [opening_range_breakout.md](opening_range_breakout.md) |
| `mean_reversion` | 1.0.0 | Apagada hasta tener evidencia | [mean_reversion.md](mean_reversion.md) |
| `trend_momentum` | 1.0.0 | Apagada: línea base solo de precios | — |
| `hyverion_strategy` | 0.1.0 | Apagada: estrategia propia en desarrollo | [hyverion_strategy.md](hyverion_strategy.md) |

## Cómo se validan

```bash
uv run python -m trading_bot replay --sessions 20            # datos reales (claves de Alpaca Paper)
uv run python -m trading_bot replay --sessions 5 --fixture   # datos sintéticos, sin conexión
```

Cada replay:

1. Audita los datos (§99): barras faltantes o duplicadas, fuera de sesión o con saltos imposibles. Si falla, no se ejecuta.
2. Usa solo el pasado en cada barra, sin mirar el futuro.
3. Aplica costes en tres escenarios: optimista, base y estrés. Si una estrategia solo gana en el optimista, se rechaza (§101).
4. Separa un 70 % dentro de muestra y un 30 % fuera de muestra, y además divide el periodo en 4 tramos consecutivos (walk-forward) para ver si el resultado es estable.
5. Reporta expectativa, profit factor, R medio, drawdown máximo, costes y operaciones (§104). Nunca se elige una estrategia solo por su tasa de acierto.

Solo se activa una estrategia más cuando el replay con datos reales y un periodo en paper lo justifican. El cambio lo aprueba el dueño.
