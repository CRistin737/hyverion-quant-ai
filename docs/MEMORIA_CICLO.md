# Ciclo de vida del conocimiento

Cómo una operación evaluada se convierte (o no) en conocimiento, y cómo ese conocimiento se valida,
decae y se retira. Todo es determinista: ningún LLM decide la confianza ni promueve nada.
Arquitectura general en [`MEMORIA.md`](MEMORIA.md).

```text
MemoryProposal de un agente ─▶ candidato PENDING ─▶ MemoryCurator ─▶ REJECTED | PENDING | VALIDATING
                                                                 └─▶ PROMOTED → StrategicMemory (+ nota en la Bóveda)
```

`uv run python -m trading_bot memory-cycle` (o **Ejecutar ciclo de memoria** en la app) corre en una
pasada: revisión diaria → síntesis semanal → curación → ciclo de vida → meta-memoria → exportación
de la Bóveda (`memory/cycle.py`).

## Destilado (`memory/distiller.py`)

`TradeReviewDistiller` convierte operaciones evaluadas en candidatos, sin LLM:

| Pasada | Ventana | Mínimo de operaciones |
|---|---|---:|
| revisión diaria | 7 días | 3 |
| síntesis semanal | 30 días | 5 |

Las operaciones reales se agrupan por activo y motivo de salida: si la mayoría pierde nace un
candidato `FAILURE`; si no, un `LESSON`. Las propuestas rechazadas por riesgo cuya réplica sombra
ganó en su mayoría generan una `OBSERVATION` informativa, que **nunca** relaja un límite de riesgo.
Las operaciones citadas quedan como `source_ids`, así el curador vuelve a puntuar exactamente esa
evidencia. Un candidato abierto con el mismo título nunca se duplica.

Los agentes también pueden proponer, pero solo tipos débiles (observación, hipótesis, lección,
fallo): `MemoryGateway.propose` los guarda como PENDING con puntuación cero.

## Evidencia y confianza (`memory/curation.py`)

`AuditEvidenceProvider` lee las `trade_evaluations` citadas: las reales usan `realized_net_pnl`
(calidad de fuente 1.0) y las réplicas sombra `shadow_comparison.shadow_trade_pnl_usd` (0.6). Filas
ausentes o ilegibles cuentan contra la calidad de datos. La confianza que declare un LLM nunca se usa.

La dirección esperada se aprende en el 70 % inicial (por tiempo; los `FAILURE` esperan pérdidas) y
debe mantenerse en el 30 % final (al menos 2 muestras).

| Componente | Peso | Definición |
|---|---:|---|
| sample_size | 0.20 | `min(1, n / 30)` |
| statistical_strength | 0.20 | `min(1, |t| / 3)` en la dirección esperada |
| source_quality | 0.10 | media de 1.0 real / 0.6 sombra |
| recency | 0.10 | media de `0.5^(días / 90)` |
| oos_confirmation | 0.15 | 1 si la media fuera de muestra tiene el signo esperado |
| regime_consistency | 0.10 | parte de la evidencia en el régimen del candidato (0.5 si se desconoce) |
| data_quality | 0.15 | muestras válidas / muestras citadas |
| contradiction | −0.30 | parte de las muestras con el signo contrario |

Cada componente se guarda en el candidato (`confidence_components`, revisión `0006`) y en un evento
`MEMORY_CURATION`.

## Escalera de promoción (`MemoryCurator`, `memory/curation.py`)

| Decisión | Regla |
|---|---|
| REJECTED | calidad de datos < 0.8, contradicción > 0.4 o duplicado de conocimiento activo |
| PENDING | menos de 3 muestras válidas: una operación no es conocimiento |
| HYPOTHESIS | ≥ 3 muestras y confianza ≥ 0.35 |
| PATTERN | ≥ 10 muestras, confirmado fuera de muestra y confianza ≥ 0.55 |
| VALIDATING | hay evidencia pero no alcanza ningún escalón |

`CONFIRMED_KNOWLEDGE` y `RULE` nunca se conceden solos: requieren revisión humana (ver
[`MEMORIA_OPERACION.md`](MEMORIA_OPERACION.md)). El conocimiento promovido empieza con
`reliability = confidence` y `validation_count = 1`, y se exporta a la Bóveda.

La app muestra los candidatos pendientes y los conflictos abiertos en **Aprendizaje › Memoria ›
Cola de revisión**.

## Uso, resultados, decaimiento y conflictos (`memory/knowledge_lifecycle.py`)

- **Uso → operación:** cada corrida del pipeline registra qué memorias se mostraron y las enlaza a la
  propuesta (`MemoryGateway.link_decision`).
- **Resultados:** cuando una operación real se evalúa, cada memoria mostrada recibe una fila en
  `memory_outcomes` (nunca dos). El conocimiento positivo suma un acierto si la operación gana y un
  fallo si pierde; las advertencias `FAILURE` se registran pero no se acreditan. Las réplicas sombra
  nunca validan conocimiento.
- **Fiabilidad:** `(confianza × 5 + aciertos) / (5 + usos)`, multiplicada por un factor de
  antigüedad que se reduce a la mitad cada 180 días tras 90 días sin validar.

| Transición | Regla |
|---|---|
| ACTIVE → NEEDS_REVALIDATION | fiabilidad < 0.35 con ≥ 5 usos, o > 90 días sin validar |
| NEEDS_REVALIDATION → ACTIVE | fiabilidad ≥ 0.50 y más aciertos que fallos |
| cualquiera → RETIRED | fiabilidad < 0.20 con ≥ 10 usos, o > 365 días sin validar |

Retirar es definitivo, fija `valid_until`, mueve la nota a `13-Retired-Knowledge` y no borra nada.

**Conflictos:** dos memorias ACTIVE con el mismo símbolo, régimen y estrategia cuyos títulos afirman
lo contrario sobre lo mismo se registran una vez en `memory_conflicts` (tipo `opposing_claims`); la
menos fiable pasa a NEEDS_REVALIDATION. Nada se sobrescribe.

## Meta-memoria (`memory/meta.py`)

Puntuaciones consultivas de qué agentes y qué memorias ayudan de verdad. Ninguna la lee
`RiskEngine`, el dimensionamiento ni un límite.

- **Atribución de decisiones:** por cada propuesta se guarda en `decision_attributions` (revisión
  `0007`) la postura de cada especialista: estrategia (`LONG`/`SHORT` según el lado), veredicto del
  crítico, estructura de mercado, régimen (`TRENDING_UP`→LONG, `TRENDING_DOWN`→SHORT), sentimiento de
  noticias y redes. Posturas neutras o desconocidas no se puntúan.
- **Fiabilidad de agentes por régimen:** cada postura se compara con el resultado evaluado (PnL real,
  o sombra si riesgo la rechazó). Suavizado de Laplace: `(aciertos + 1) / (muestras + 2)`.
- **Memory Value Score:** `0.7 × tasa de acierto + 0.3 × lift`, donde el lift es el PnL medio de las
  decisiones donde se mostró la memoria menos la media general, comprimido con
  `0.5 + 0.5 × tanh(lift / media |PnL|)`.
- **Contrafactual:** en propuestas rechazadas por riesgo, la réplica sombra estima la pérdida que la
  memoria ayudó a evitar y la ganancia que se dejó pasar.

Cada ciclo añade un evento `MEMORY_META` con las puntuaciones.
