# Auditoría reproducible del plan

El plan de Hyverion Quant AI tiene 43 requisitos numerados. La matriz escrita
se valida además con un comando que comprueba sus rutas de evidencia, las
secciones obligatorias de cada `agents/*/AGENT.md` y las guías completadas.

```bash
uv run python -m trading_bot plan-audit
```

El resultado es JSON y contiene:

- `overall=PASS` cuando la matriz tiene exactamente los requisitos `0–42`, no
  falta ninguna ruta local de evidencia y todos los `AGENT.md` cumplen el
  contrato.
- `status_counts` para distinguir `COMPLETE`, `FOUNDATION` y `GATED`.
- `evidence_missing_count`, que no debe ignorarse aunque los tests estén en
  verde.
- el detalle por requisito y su `remaining_gate`.

`PASS` significa que el handoff documental es coherente. No significa que
LIVE esté autorizado: los requisitos `FOUNDATION` y `GATED` continúan sujetos
a sus pruebas de exchange, proveedor, VPS y revisión humana.
