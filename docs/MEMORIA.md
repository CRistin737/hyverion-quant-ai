# Memoria de tres capas

La memoria informa mejor a los agentes; **nunca les da autoridad**. `RiskEngine`, el
dimensionamiento y la ejecución no la leen. Si falla, el ciclo de trading sigue igual.

Documentos relacionados:
- [`MEMORIA_CICLO.md`](MEMORIA_CICLO.md): cómo nace, se valida, decae y se retira el conocimiento.
- [`MEMORIA_OPERACION.md`](MEMORIA_OPERACION.md): salud, decisiones humanas, API, CLI, copias de
  seguridad y seguridad.

Resumen de la plataforma autónoma: [`spec/HYVERION_AUTONOMOUS_PLATFORM.md`](../spec/HYVERION_AUTONOMOUS_PLATFORM.md).

## Arquitectura

```text
agentes ──(solo MemoryProposal)──▶ MemoryGateway ◀──(pide cápsula)── orquestador
                                      │
           ┌──────────────────────────┼───────────────────────────┐
           ▼                          ▼                           ▼
  WorkingMemoryBackend        MemoryRepository             VaultRepository
  (Redis | en proceso)     (tablas SQLAlchemy Core)     (Markdown + frontmatter)
                                      │
                               VectorBackend
                      (pgvector | nulo en SQLite)
```

| Capa | Pregunta que responde | Dónde vive | ¿Fuente de verdad? |
|---|---|---|---|
| Trabajo | ¿Qué está pasando ahora? | Redis o memoria del proceso | No: se reconstruye desde la base |
| Histórica | ¿Qué pasó? | Base SQL (SQLite o PostgreSQL) | **Sí** |
| Estratégica | ¿Qué aprendimos? | `strategic_memories` + versiones; Bóveda Markdown como vista | La base sí; la Bóveda no |

Reglas de diseño:
- **Puertos** (`memory/ports.py`): `WorkingMemoryBackend`, `MemoryRepository`, `VaultRepository`,
  `VectorBackend`, `EmbeddingProvider`.
- **Los agentes nunca tocan el almacenamiento.** Reciben una `MemoryCapsule` acotada y saneada y
  solo pueden emitir una `MemoryProposal`. Solo el curador determinista crea o promueve.
- **El contenido de la cápsula es dato no confiable**, igual que noticias y redes: evidencia citada
  con id y versión, nunca instrucciones.
- **Consultas en el tiempo:** toda búsqueda recibe `as_of`; nada creado después se devuelve, así los
  backtests no ven el futuro.
- **Producción:** con `APP_ENV=production` se rechazan la memoria de trabajo en proceso y el índice
  vectorial nulo en lugar de degradar en silencio.

Módulos principales: `models.py`, `ports.py`, `repository.py`, `working.py`, `vault.py`,
`vault_view.py` (Bóveda en la app), `retrieval.py`, `gateway.py`, `embeddings.py`, `vector.py`,
`distiller.py`, `curation.py` (confianza + curador), `knowledge_lifecycle.py`, `meta.py`,
`research.py`, `maintenance.py` (construcción del gateway, reindexado y reconstrucción de la memoria
de trabajo), `operations.py`, `cycle.py`, `cli.py`.

## Capa de trabajo

Desechable: nunca es fuente de verdad y se reconstruye desde la base
(`WorkingMemoryRebuilder` en `memory/maintenance.py`).

| Backend | Clase | Uso |
|---|---|---|
| `in_process` | `InMemoryWorkingMemoryAdapter` | Por defecto en la app de escritorio (PAPER); rechazado en producción |
| `redis` | `RedisWorkingMemory` | Perfil `memory` de Docker; obligatorio en producción |

Ambos cumplen el mismo contrato: claves acotadas y sin espacios, valores JSON (sin `Decimal` ni
NaN; los decimales van como texto), TTL positivo opcional. Si Redis cae se lanza
`WorkingMemoryUnavailable`; un valor inválido lanza `ValueError`. Nada cae a otro backend en silencio.

Se elige con `MEMORY_WORKING_BACKEND=redis` y `REDIS_URL` (secreto, en `.env`). Las claves llevan el
prefijo `memory.working_key_prefix` (por defecto `hyverion`). La reconstrucción escribe
`position:{position_id}` por cada posición abierta y `session:current`, con TTL de 15 minutos.

### Servicios Docker (opcionales)

```bash
# .env guarda POSTGRES_PASSWORD / REDIS_PASSWORD generadas localmente (gitignored, modo 600)
docker compose --profile memory up -d postgres redis
```

- `postgres`: `pgvector/pgvector:0.8.0-pg16` en `127.0.0.1:55432`, volumen `hyverion-postgres`.
- `redis`: `redis:7.4-alpine` en `127.0.0.1:56379`, con contraseña, sin persistencia RDB/AOF,
  256 MB `volatile-lru`, sistema de archivos de solo lectura.

Ambos escuchan solo en localhost. SQLite sigue siendo el almacén por defecto; apuntar
`DATABASE_URL` a `postgresql+asyncpg://…` usa el mismo esquema en PostgreSQL. Los tests con
servicios (`tests/integration/test_memory_backends.py`) corren solo si están exportados `REDIS_URL`
y `HYVERION_TEST_POSTGRES_URL`.

## Capa histórica

Lo que pasó, autoritativo y consultable, en la base SQL.

| Datos | Tablas | Quién escribe |
|---|---|---|
| Entradas de mercado | `market_snapshots`, `candles`, `features`, `news_items`, `social_items` | colectores, motor de features |
| Decisiones | `agent_outputs`, `signals`, `trade_proposals`, `critic_reviews`, `risk_decisions` | runtime de agentes, orquestador |
| Quién creyó qué | `decision_attributions` (postura de cada especialista por propuesta) | pipeline de especialistas |
| Ejecución | `orders`, `fills`, `positions`, `operations`, `operation_events` | ExecutionEngine, ciclo de vida |
| Resultados | `trade_evaluations` (reales y sombra), `shadow_trades` | evaluador post-cierre, simulador sombra |
| Contabilidad de memoria | `memory_usage`, `memory_outcomes`, `strategic_memory_snapshots` | gateway, ciclo de vida, repositorio |
| Operación | `system_events`, `alerts` | todos los servicios |

Series de mercado masivas pueden guardarse también en Parquet particionado por fecha
(`database.parquet_root`, `ParquetMarketStore`): sirve para investigación, no para decidir.

Garantías:
- **Auditoría de solo inserción:** decisiones, evaluaciones y eventos nunca se actualizan. El estado
  de una operación solo cambia por transiciones legales.
- **UTC y Decimal:** marcas de tiempo con zona UTC; dinero y puntuaciones en `Decimal`, serializados
  como texto.
- **Retención acotada:** `retention` purga solo entradas ruidosas (snapshots 14 días, features 30,
  velas/noticias 90, salidas de agentes/señales 180, eventos de riesgo/sistema 365). Proyecciones de
  operaciones, evaluaciones, atribuciones y todas las tablas de memoria nunca se purgan, porque
  explican por qué se movió el dinero.

## Capa estratégica

`strategic_memories` + `strategic_memory_versions` (solo inserción, con hash SHA-256) son la verdad.
El conocimiento nunca se borra: retirarlo pone `status=RETIRED` y `valid_until`.

Tablas de memoria (revisiones `0004`–`0008`, aditivas y portables): `memory_candidates`,
`memory_evidence`, `strategic_memories`, `strategic_memory_versions`, `memory_usage`,
`memory_outcomes`, `memory_conflicts`, `strategic_memory_snapshots`, `decision_attributions` y, solo
en PostgreSQL, `memory_embeddings`.

### Bóveda de conocimiento (`memory/vault.py`)

- Markdown compatible con Obsidian en `memory.vault_path` (por defecto `data/knowledge/`, fuera de
  git; en la app empaquetada `~/Library/Application Support/Hyverion Quant AI/knowledge/`).
- Una nota por `knowledge_id`, carpeta por categoría (`07-Patterns`, `06-Daily-Lessons`,
  `08-Failures`, `12-Research`, `00-System`); las retiradas pasan a `13-Retired-Knowledge`.
- Frontmatter con `yaml.safe_dump`; títulos o cuerpos hostiles no pueden falsificarlo. Escrituras
  atómicas, rechazo de nombres inseguros y de rutas fuera de la raíz.
- `has_drifted()` compara la nota con la versión autoritativa: las ediciones manuales se detectan,
  nunca se importan.

| Parte de la nota | Marcador | Dueño |
|---|---|---|
| Contenido gestionado | `<!-- hyverion:content -->` | Hyverion; editarlo cuenta como deriva |
| Enlaces | `<!-- hyverion:links -->` | Hyverion; se regeneran en cada ciclo |
| Notas del dueño | `<!-- hyverion:owner-notes -->` hasta el final | Tú; se conservan en cada reescritura |

La app la muestra en **Aprendizaje › Memoria › Notas y grafo** (lectura y edición solo de las notas del dueño);
Obsidian es opcional (*Open folder as vault*). Propiedades: `aliases` con el título, etiquetas
`hyverion/<tipo>`, `estado/<estado>`, `mercado/<qqq>`, `regimen/<régimen>`. Cada nota enlaza a
su mercado, su régimen, lo que contradice y `00-System/Knowledge Map`.

### Embeddings locales (`memory/embeddings.py`)

Requisito del dueño: locales, mínimos y solo para embeddings.
- Modelo `sentence-transformers/all-MiniLM-L6-v2` en ONNX int8 (23 MB, 384 dimensiones), revisión
  fijada y SHA-256 de ambos archivos. Se descarga una vez con
  `uv run python scripts/fetch_embedding_model.py` a `data/models/`.
- Solo `onnxruntime` (extra `embeddings`), un hilo de CPU, en proceso: sin servidor, sin generación
  de texto, sin red en ejecución. Tokenizador WordPiece propio, verificado token a token contra la
  implementación de referencia (3 012 textos); `[CLS]`/`[SEP]` literales dentro de texto no confiable
  no se tratan como tokens de control.
- Los archivos se verifican por hash en cada carga; si faltan o cambiaron: `EmbeddingUnavailable`.

### Índice vectorial (`memory/vector.py`)

- PostgreSQL: `memory_embeddings (vector(384))` con índice HNSW coseno (revisión `0005`).
- SQLite: `NullVectorBackend` → recuperación solo por metadatos, marcada como degradada.
- `MemoryIndexer` (`memory/maintenance.py`) incrusta en lotes de 32 el conocimiento ACTIVE y
  NEEDS_REVALIDATION nuevo o cambiado. El texto incrustado es la afirmación, el resumen, el símbolo y
  el régimen, nunca evidencia cruda. Corre dentro de `memory-cycle` y con `memory reindex [--force]`.

## Recuperación y cápsulas

```text
petición de contexto ─▶ MemoryGateway.context(agent_id, query, as_of, symbol, regime)
   ├─ presupuesto (CAPSULE_BUDGETS) ── agente desconocido → cápsula vacía (mínimo privilegio)
   ├─ MemoryRetrievalEngine.retrieve
   │    1. filtro por metadatos: ACTIVE, válido en as_of, creado ≤ as_of, símbolo/estrategia/régimen
   │    2. reordenamiento semántico de esos mismos candidatos (si hay pgvector + modelo)
   │    3. puntuación = relevancia × fiabilidad × régimen × recencia × importancia
   ├─ saneado (caracteres de control fuera, espacios colapsados, longitud acotada)
   ├─ límite de ítems y caracteres
   └─ registro de memory_usage por cada ítem mostrado
```

El índice semántico solo reordena lo que el filtro temporal ya admitió, así que no puede introducir
información del futuro.

| Factor | Regla |
|---|---|
| relevancia | similitud coseno (0..1); `0.25` sin embedding; `1.0` en modo solo metadatos |
| fiabilidad | fiabilidad guardada (0..1) |
| régimen | `1.0` exacto o sin régimen pedido, `0.8` conocimiento global |
| recencia | `0.5^(días / 90)`, mínimo `0.5` |
| importancia | `0.5 + 0.5 × importancia` |

| Agente | Ítems | Caracteres |
|---|---:|---:|
| strategy | 5 | 1800 |
| critic | 5 | 1800 |
| regime | 3 | 900 |
| position_manager | 3 | 900 |

Los demás agentes (market, technical, news, social, derivatives…) no reciben memoria.

`MemoryCapsule.to_context()` añade la memoria al contexto con un aviso fijo: los ítems son evidencia
que sopesar, nunca instrucciones, y no pueden cambiar límites de riesgo ni autorizar una operación.
Si la recuperación falla, la cápsula llega vacía con `degraded=true` y el ciclo continúa. En SQLite es
normal `degraded=true` con motivo `semantic_index_unavailable`.

**Activación:** `MEMORY_ENABLED=true` (o `memory.enabled: true`). `build_memory_gateway`
(`memory/maintenance.py`) usa pgvector + modelo local en PostgreSQL y solo metadatos en SQLite; en
producción se niega a arrancar sin PostgreSQL, pgvector y el modelo verificado.

### Investigación en el tiempo

`search_strategic(as_of=…)` oculta lo creado después de `as_of`, pero filtraría con el estado y la
fiabilidad de hoy. Por eso cada cambio de campos mutables añade una fila a
`strategic_memory_snapshots` (revisión `0008`) en la misma transacción, y
`search_strategic_as_of` reconstruye cada ítem tal como estaba. `MemoryReplayEvaluator`
(`memory/research.py`, CLI `memory replay --days N`) recorre cada propuesta evaluada, recupera el
conocimiento de ese momento y reporta cuántas decisiones advertidas perdieron, cuántas respaldadas
ganaron y el PnL si se hubieran saltado las advertidas. Nunca registra uso ni resultados, y sale con
código 1 si detecta alguna fuga temporal.
