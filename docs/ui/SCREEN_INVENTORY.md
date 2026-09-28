# Inventario de pantallas

**Estados:** NOT_STARTED · AUDITED · DESIGNED · IMPLEMENTING · VISUAL_REVIEW · FUNCTIONAL_REVIEW · DONE

`VISUAL_REVIEW` significa: implementada en `app/src/screens/`, revisión visual hecha en modo fixture
(Chromium, Playwright, 4 tamaños) y **revisión funcional contra el core real pendiente** de las
pruebas del dueño.

**Alcance:** un solo operador, solo escritorio. Tamaños soportados de 1024×700 a 2560×1440. Teléfono
y tablet no son plataformas objetivo; quedan documentados como fuera de alcance, pero redimensionar
la ventana no debe romper nada.

**Rol:** un único operador local. No hay RBAC multiusuario. Las acciones protegidas se aplican en el
servidor:
- configuración validar → aplicar
- LIVE bloqueado
- la UI nunca envía órdenes

## Pantallas heredadas (PySide6, `desktop/app.py`) → nuevo destino

| # | Pantalla heredada | Línea | Datos (claves del snapshot / endpoints) | Acciones | Nuevo destino |
|---|---|---|---|---|---|
| 1 | Overview | 1899 | `pnl`, `pnl_history`, `protection_recovery`, `markets`, `positions`, `agents`, `audit`, `alerts` | — | **Inicio** |
| 2 | Markets | 2028 | `markets[].history/candles` | Elegir símbolo | **Trading › Mercado** |
| 3 | Agents | 2085 | `agents`, `proposals` | — | **Inteligencia › Agentes** |
| 4 | Risk | 2117 | `risk`, `session`, `config.risk` | — | **Riesgo › Decisión** |
| 5 | Positions | 2149 | `positions`, `position_history` | — | **Trading › Posiciones** |
| 6 | Operations | 2159 | `operations`, `unresolved_operations`, `orders`, `fills`, `GET /operations/{id}/events` | Resolver (`POST /operations/{id}/resolve`, con motivo) | **Trading › Operaciones** |
| 7 | Providers | 2280 | `providers`, `usage`, `GET /providers` | Login, prueba, suscripción, sondeo de cadena, API key → llavero | **Inteligencia › Modelos** |
| 8 | Sources | 2473 | `sources`, `source_runs`, `GET /sources` | — | **Inteligencia › Fuentes** |
| 9 | Paper / Shadow | 2504 | `orders`, `shadow_trades` | — | **Trading › Órdenes simuladas** |
| 10 | Backtest | 2523 | `POST /backtest/strategies`, `/backtest/challenger` | Evidencia real por estrategia, comparación de configuraciones | **Laboratorio › Probar estrategias** |
| 11 | Learning | 2555 | `learning_metrics`, `agent_memory`, `change_proposals`, `/learning/proposals*` | Revisar transición (con motivo) | **Laboratorio › Aprendizaje** |
| 12 | Memory | 2610 | `/memory`, `/memory/knowledge*`, `/memory/candidates`, `/memory/conflicts` | Refrescar, ejecutar ciclo, registrar decisión | **Laboratorio › Memoria** |
| 13 | Audit | 2732 | `/readiness`, `decision_trace`, `/observability/*` | Auditoría de preparación, exportar métricas, revisión de retención | **Riesgo › Auditoría** |
| 14 | Settings | 2802 | `/config/public` | Validar → aplicar, secretos en llavero, reconciliación sandbox, copia de seguridad | **Ajustes** |
| 15 | Guides | 3012 | manual estático, `/plan-audit` | Auditoría completa del plan | **Ajustes › Ayuda** + ayuda contextual |

## Estado de las pantallas nuevas

| Pantalla | Ruta | Archivo | Estado |
|---|---|---|---|
| Onboarding (primer arranque) | se monta antes del espacio de trabajo | `screens/onboarding/Onboarding.tsx` | VISUAL_REVIEW |
| Inicio | `/inicio` | `screens/inicio/Inicio.tsx` | VISUAL_REVIEW |
| Trading › Mercado · Posiciones · Operaciones | `/trading/*` | `screens/trading/Trading.tsx` | VISUAL_REVIEW |
| Inteligencia › Agentes · Modelos · Fuentes | `/inteligencia/*` | `screens/inteligencia/Inteligencia.tsx` | VISUAL_REVIEW |
| Riesgo › Decisión · Límites · Auditoría | `/riesgo/*` | `screens/riesgo/Riesgo.tsx` | VISUAL_REVIEW |
| Laboratorio › Probar estrategias · Aprendizaje · Memoria · Bóveda | `/laboratorio/*` | `screens/laboratorio/*.tsx` | VISUAL_REVIEW |
| Laboratorio › Bóveda | `/laboratorio/boveda` | `screens/laboratorio/Boveda.tsx` | VISUAL_REVIEW |
| Ajustes › Cuenta IA · Exchange · Fuentes · Riesgo · Sistema · Ayuda | `/ajustes/*` | `screens/ajustes/Ajustes.tsx` | VISUAL_REVIEW |

## Pantallas nuevas

### Onboarding (primer arranque) — VISUAL_REVIEW
- **Propósito:** configurar la app sin la CLI (sustituye el requisito de `setup`).
- **Acción principal:** "Iniciar en modo simulación".
- **Pasos (4):**
  1. Capital
  2. Mercado (exchange y símbolos aprobados)
  3. Inteligencia artificial (proveedor)
  4. Límites de riesgo (revisar y confirmar)
- **Datos:** `GET /api/v1/setup/status`, `/config/public`, `/config/validate`, `/config/apply`, llavero.
- **Estados:** errores de validación del servidor por paso, fallo del llavero, API inaccesible.
- **Fixture:** `?fixture=first-run`.

### Inicio — VISUAL_REVIEW (pantalla prioritaria)
- **Propósito:** mostrar lo que importa ahora y si la protección está activa.
- **Acción principal:** contextual. Por ejemplo, "Resolver operación" si
  `unresolved_operations > 0`, o "Completar configuración" si la preparación tiene huecos.
- **Datos clave:**
  - equity (marcado/caja), PnL de hoy, drawdown frente al límite
  - acción de la sesión
  - recuperación de protección, reconciliación
  - posiciones abiertas
  - alertas
  - presupuesto de IA
  - siguientes pasos (preparación)
- **Estados:** esqueleto de carga, vacío (sin operaciones → pasos guiados), API caída, modo seguro.

### Trading — VISUAL_REVIEW
- **Pestañas:** Mercado · Posiciones · Operaciones.
- **Diseño:** Mercado usa una lista de seguimiento y un gráfico. Operaciones usa una tabla y un
  inspector del ciclo de vida en un panel lateral (sheet).

### Inteligencia — VISUAL_REVIEW
- **Pestañas:** Agentes · Modelos · Fuentes.
- Los agentes deterministas de solo especificación se etiquetan como "Determinista".

### Riesgo — VISUAL_REVIEW
- **Pestañas:** Decisión · Límites · Auditoría.
- La traza de decisión es una línea de tiempo, no una tabla.
- Límites es de solo lectura (`config/public` incluye `risk`); se editan en Ajustes › Riesgo con
  validar → aplicar.

### Laboratorio — VISUAL_REVIEW
- **Pestañas:** Simulación (backtest + paper/shadow) · Aprendizaje · Memoria · Bóveda.

### Bóveda — VISUAL_REVIEW
- **Propósito:** leer y anotar la bóveda de conocimiento dentro de la app, sin necesitar Obsidian
  (Obsidian queda opcional; la exportación Markdown se mantiene).
- **Datos:** `GET /api/v1/memory/vault`, `GET /api/v1/memory/vault/note?path=`.
- **Acción:** `POST /api/v1/memory/vault/note/owner`, que edita **solo la sección del dueño**; el
  contenido gestionado no se toca (`src/trading_bot/memory/vault_view.py`).

### Ajustes — VISUAL_REVIEW
- **Secciones:** Cuenta IA · Exchange · Fuentes · Riesgo · Sistema · Ayuda.
- Usa filas de ajustes con navegación por secciones, no tarjetas.
- Los secretos se guardan en el Llavero de macOS mediante comandos Rust; solo se muestra presencia.

## Superficies globales
- **Barra lateral:** 6 dominios, plegable.
- **Barra de estado:** modo, API, motor, última actualización, reconciliación.
- **Paleta ⌘K:** navegación y acciones.
- **Toasts:** solo para confirmaciones que cruzan contextos.
- **Insignia de modo:** "SIMULACIÓN" siempre visible.
