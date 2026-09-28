# Hyverion Strategy (`hyverion_strategy`)

Es la estrategia propia de Hyverion. Se construye **por prueba y error** a partir de estrategias conocidas de QQQ, y cada cambio sube la versión: `0.1.0`, `0.2.0`, etc.

**Arranca apagada.** Para activarla hay que añadirla a `enabled` en `config/strategies.yaml`, y solo cuando haya evidencia fuera de muestra y en paper.

## Hipótesis (0.1.0)

Una estrategia conocida gana más cuando se usa **solo en el régimen para el que fue pensada** y **solo cuando el resto de la evidencia está de acuerdo**:

- la amplitud del Nasdaq-100;
- las megacaps;
- la macro;
- la volatilidad;
- las noticias.

## Reglas

1. **Régimen** (`strategies/regime.py`):
   - `TRENDING_UP`: retroceso en tendencia (`trend_pullback`) y, en las dos primeras horas, ruptura del rango de apertura (`opening_range_breakout`);
   - `RANGING`: reversión al VWAP (`mean_reversion`);
   - cualquier otro régimen: **NO_TRADE** (`regime_not_tradable:<régimen>`).
2. **Confluencia** (`intelligence/evidence.py`, pesos `confluence-v1`): la puntuación debe ser **≥ 55/100**. Si no, NO_TRADE (`low_confluence:<puntuación>`).
   - La amplitud y los componentes cuentan como **un solo voto**, porque son las mismas acciones.
   - Una familia sin datos cuenta como «no disponible», no como neutral.
3. **Tamaño, stop, objetivo y costes**: los del setup elegido (`strategies/equity.py`). Luego pasan el crítico y el RiskEngine como cualquier propuesta: regla macro, topes en USD, máximo de operaciones al día, cierre antes del final.

## Invalidaciones

Las mismas del setup elegido: cierre bajo el VWAP, EMA rápida bajo la lenta o toque del stop protector. Además, toda posición se cierra antes del final de la sesión.

## Limitaciones conocidas

- **Replay.** No hay inteligencia guardada de días pasados (amplitud, macro, noticias), así que en el replay la confluencia solo usa lo que dan las barras: estructura de precio y volumen. El informe de replay lo indica.
- **Umbrales sin validar.** El 55 de confluencia y las dos horas de la ruptura son **puntos de partida**. Se validan con replay, walk-forward y paper antes de cambiarlos.
- **Muestra pequeña.** Con poca muestra no se concluye nada.

## Cómo evoluciona

1. **Proponer.** Toda idea nueva entra como `ChangeProposal` en Aprendizaje, con la evidencia que la sostiene.
2. **Validar.** El cambio pasa por el histórico, fuera de muestra, walk-forward con costes en los tres escenarios, paper y shadow.
3. **Aprobar.** Solo el dueño aprueba. El optimizador nunca promociona nada solo (`auto_promote_agent_changes=false`), y nunca se relaja un límite de riesgo para buscar más ganancia.

## Historial

- **0.1.0:** router por régimen con tres setups conocidos y filtro de confluencia.
