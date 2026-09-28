/**
 * Spanish labels for backend enums and codes. The backend speaks English codes;
 * everything the operator sees is Spanish. Unknown codes fall back to a
 * humanised version of the code so new backend values never render blank.
 */

import type { OperationState } from "@/api/types";

export type Tone = "neutral" | "positive" | "negative" | "warning" | "info" | "accent";

export function humanize(code: string | null | undefined): string {
  if (!code) return "—";
  const text = code.replace(/[_-]+/g, " ").toLowerCase().trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function lookup(map: Record<string, string>, code: string | null | undefined): string {
  if (!code) return "—";
  return map[code] ?? map[code.toUpperCase()] ?? map[code.toLowerCase()] ?? humanize(code);
}

export const DOMAINS = {
  inicio: "Inicio",
  trading: "Trading",
  inteligencia: "Inteligencia",
  riesgo: "Riesgo",
  aprendizaje: "Aprendizaje",
  ajustes: "Ajustes",
} as const;

const OPERATION_STATES: Record<OperationState, string> = {
  PROPOSED: "Propuesta",
  CRITIC_REVIEWED: "Revisada por el crítico",
  RISK_APPROVED: "Aprobada por riesgo",
  EXECUTION_PENDING: "Ejecución pendiente",
  SUBMITTED: "Enviada",
  ACKNOWLEDGED: "Confirmada por el broker",
  PARTIALLY_FILLED: "Parcialmente ejecutada",
  OPEN: "Abierta",
  EXIT_PENDING: "Salida pendiente",
  CLOSING: "Cerrando",
  CLOSED: "Cerrada",
  RECONCILED: "Conciliada",
  EVALUATED: "Evaluada",
  REJECTED: "Rechazada",
  CANCEL_PENDING: "Cancelación pendiente",
  CANCELED: "Cancelada",
  UNKNOWN: "Estado desconocido",
  SAFE_MODE: "Modo seguro",
  RECOVERY_REQUIRED: "Requiere recuperación",
};

export const operationState = (code: string | null | undefined) => lookup(OPERATION_STATES, code);

export function operationTone(code: string | null | undefined): Tone {
  switch (code) {
    case "UNKNOWN":
    case "SAFE_MODE":
    case "RECOVERY_REQUIRED":
      return "negative";
    case "OPEN":
    case "PARTIALLY_FILLED":
      return "accent";
    case "SUBMITTED":
    case "ACKNOWLEDGED":
    case "EXECUTION_PENDING":
    case "EXIT_PENDING":
    case "CLOSING":
    case "CANCEL_PENDING":
      return "warning";
    case "CLOSED":
    case "RECONCILED":
    case "EVALUATED":
      return "positive";
    default:
      return "neutral";
  }
}

const RISK_REASONS: Record<string, string> = {
  macro_event_pre_block: "Evento macro de alto impacto en menos de 15 minutos: no se abren operaciones.",
  macro_event_cooldown: "Enfriamiento tras un evento macro de alto impacto.",
  macro_calendar_unavailable: "Sin calendario económico reciente: no se abren operaciones hasta leerlo.",
  broker_equity_unavailable: "Sin el patrimonio actualizado de la cuenta Alpaca: no se abren operaciones.",
  market_closed: "El mercado de EE. UU. está cerrado.",
  premarket_trading_disabled: "Premercado: solo se opera en el horario regular.",
  after_hours_trading_disabled: "Fuera de horario: solo se opera en el horario regular.",
  market_session_unknown: "No se pudo determinar la sesión del mercado: no se opera.",
  opening_protection_window: "Primeros minutos tras la apertura: no se abren operaciones.",
  end_of_day_cutoff: "Cerca del cierre: no se abren operaciones nuevas.",
  max_trades_per_day_reached: "Se alcanzó el máximo de operaciones del día.",
  entry_price_deviates_from_market: "El precio de entrada se aleja del precio de mercado.",
  critic_rejected: "El crítico rechazó la propuesta.",
  SYMBOL_NOT_EXECUTION_WHITELISTED: "Hyverion solo puede operar QQQ; cualquier otro símbolo se rechaza.",
  a_plus_setup_required: "Este nivel exige una configuración A+",
  account_high_water_mark_drawdown_reached: "Drawdown máximo desde el pico de la cuenta alcanzado",
  ai_budget_exhausted: "Límite de la suscripción de IA alcanzado",
  macro_event_active: "Evento macro cercano (tenido en cuenta)",
  critic_revise_advisory: "El crítico pidió revisar (aviso)",
  max_profit_giveback_realized_reached: "Se devolvió demasiada ganancia del día",
  max_profit_giveback_total_reached: "Se devolvió demasiada ganancia del día",
  stopped_by_profit_protection: "Día cerrado para proteger la ganancia",
  candidate_notional_exceeds_asset_capacity: "El tamaño supera la exposición permitida por activo",
  candidate_notional_exceeds_exposure_capacity: "El tamaño supera la exposición total permitida",
  candidate_worst_case_loss_exceeds_allowed_risk: "La pérdida máxima supera el riesgo permitido",
  critic_did_not_approve: "El crítico no aprobó la propuesta",
  daily_loss_limit_reached: "Límite de pérdida diaria alcanzado",
  daily_profit_hard_cap_reached: "Tope de ganancia diaria alcanzado",
  estimated_slippage_too_high: "Deslizamiento estimado demasiado alto",
  exchange_reconciliation_failed: "La conciliación con el broker falló",
  expected_value_not_positive_after_costs: "Valor esperado no positivo tras costos",
  insufficient_independent_confirmations: "Confirmaciones independientes insuficientes",
  loss_cooldown_active: "Pausa tras pérdida activa",
  maximum_losing_streak_reached: "Racha máxima de pérdidas alcanzada",
  maximum_positions_reached: "Máximo de posiciones abiertas alcanzado",
  observed_liquidity_below_minimum: "Liquidez por debajo del mínimo",
  observed_spread_too_high: "Spread demasiado alto",
  protect_profit_default: "Protección de ganancias activa",
  session_within_limits: "Sesión dentro de los límites",
  signal_score_below_ladder_minimum: "Puntuación por debajo del mínimo del nivel",
  score_below_level_minimum: "Puntuación por debajo del mínimo del nivel",
  stale_market_data: "Datos de mercado desactualizados",
  unresolved_critical_conflicts: "Conflictos críticos sin resolver",
  unresolved_operation_requires_recovery: "Hay una operación sin resolver que requiere recuperación",
  weekly_loss_limit_reached: "Límite de pérdida semanal alcanzado",
  within_limits: "Dentro de los límites",
  liquidity_ok: "Liquidez suficiente",
  score_above_level_minimum: "Puntuación sobre el mínimo del nivel",
  spread_too_wide: "Spread demasiado amplio",
};

export const riskReason = (code: string | null | undefined) => lookup(RISK_REASONS, code);

const VERDICTS: Record<string, string> = { ALLOW: "Aprobada", DENY: "Rechazada", REDUCE: "Reducida" };
export const verdict = (code: string | null | undefined) => lookup(VERDICTS, code);
export const verdictTone = (code: string | null | undefined): Tone =>
  code === "ALLOW" ? "positive" : code === "DENY" ? "negative" : code === "REDUCE" ? "warning" : "neutral";

const ALERT_MESSAGES: Record<string, string> = {
  "Exchange reconciliation mismatch": "Discrepancia en la conciliación con el broker",
  "Exchange reconciliation failed": "La conciliación con el broker falló",
  "Control-plane backup failed": "Falló la copia de seguridad de la base de control",
  "External intelligence collection degraded": "Recolección de inteligencia externa degradada",
  "Market stream frame rejected": "Se rechazó un mensaje del flujo de mercado",
  "Market stream cycle failed closed": "El ciclo del flujo de mercado se cerró por seguridad",
  "All configured AI subscriptions failed": "Fallaron todas las suscripciones de IA configuradas",
  "Protective-stop recovery is in SAFE MODE": "La recuperación de stops protectores está en modo seguro",
  "Paper cycle failed closed": "Un ciclo de simulación se cerró por seguridad",
  "Open position could not be protected": "No se pudo proteger una posición abierta",
  "Open position state is invalid": "El estado de una posición abierta no es válido",
  "Position exit requires recovery": "El cierre de una posición requiere recuperación",
  "Operation lifecycle violation": "Violación del ciclo de vida de una operación",
  "Order state unknown; awaiting reconciliation": "Estado de la orden desconocido; esperando conciliación",
  "Open positions without a running engine": "Hay posiciones abiertas y el motor está detenido: nadie vigila stops ni el límite de tiempo",
  "Operation requires recovery before new entries": "Una operación requiere recuperación antes de nuevas entradas",
};

/** Alert messages may carry a ": detail" suffix appended by the backend. */
export function alertMessage(message: string): string {
  const [head, ...rest] = message.split(": ");
  const translated = ALERT_MESSAGES[head ?? ""];
  if (!translated) return message;
  return rest.length ? `${translated}: ${rest.join(": ")}` : translated;
}

const MARKET_SESSIONS: Record<string, string> = {
  CLOSED: "Mercado cerrado",
  PRE_MARKET: "Premercado",
  OPENING: "Apertura",
  REGULAR: "Mercado abierto",
  MIDDAY: "Mediodía",
  POWER_HOUR: "Última hora",
  CLOSING: "Cierre",
  AFTER_HOURS: "Después del cierre",
};
export const marketSession = (code: string | null | undefined) => lookup(MARKET_SESSIONS, code);

const SEVERITY: Record<string, string> = { CRITICAL: "Crítica", WARNING: "Advertencia", INFO: "Información" };
export const severity = (code: string | null | undefined) => lookup(SEVERITY, code);
export const severityTone = (code: string | null | undefined): Tone =>
  code === "CRITICAL" ? "negative" : code === "WARNING" ? "warning" : "info";

const READINESS: Record<string, { label: string; detail?: string }> = {
  paper_mode: { label: "Modo simulación", detail: "La simulación está activa y el modo real está deshabilitado." },
  long_only_session: { label: "QQQ, solo compras, horario regular", detail: "Solo QQQ, solo compras, en horario regular, sin posiciones de un día para otro y sin opciones." },
  database: { label: "Base de datos de control" },
  provider_gateway: { label: "Pasarela única de IA", detail: "Un proveedor principal con respaldos ordenados vía ModelRouter." },
  provider_accounts: { label: "Cuentas de IA seleccionadas" },
  protective_recovery: { label: "Recuperación de stops protectores" },
  broker_mutations: { label: "Órdenes en un broker real", detail: "Solo existen brokers de simulación (paper); no hay adaptador ni claves para el modo real." },
  venue_region: { label: "País de operación" },
  live_authorization: { label: "Autorización para modo real", detail: "Requiere revisión independiente y confirmación explícita." },
};

export function readinessLabel(checkId: string, fallback: string): string {
  return READINESS[checkId]?.label ?? fallback;
}

export function readinessDetail(checkId: string, fallback: string): string {
  if (checkId === "venue_region") {
    if (fallback.startsWith("No operating region")) return "No has indicado tu país; es obligatorio antes de revisar el modo real.";
    const declared = /^Region (\w{2}) declared/.exec(fallback);
    if (declared) return `País ${declared[1]} indicado; antes del modo real hay que confirmar en los términos oficiales del broker que da servicio ahí.`;
  }
  if (checkId === "provider_accounts") {
    if (fallback.startsWith("Connected: ")) return `Conectadas: ${fallback.slice(11)}.`;
    if (fallback.startsWith("No AI subscription selected")) return "No hay suscripción de IA elegida; los ciclos que dependen de la IA se detienen de forma segura.";
    if (fallback.startsWith("No selected subscription")) return "Ninguna suscripción elegida está verificada ahora mismo.";
    if (fallback.startsWith("Primary subscription authenticated")) return "La suscripción principal está conectada.";
  }
  if (checkId === "protective_recovery") {
    if (fallback.startsWith("Open positions have deterministic protection")) return "Todas las posiciones abiertas tienen su stop de protección.";
    if (fallback.startsWith("Protective recovery failed")) return "No se pudo comprobar la protección de las posiciones.";
  }
  return READINESS[checkId]?.detail ?? fallback;
}

const READINESS_STATUS: Record<string, string> = { PASS: "Correcto", INFO: "Aviso", GATED: "Bloqueado", FAIL: "Falla" };
export const readinessStatus = (code: string) => lookup(READINESS_STATUS, code);
export const readinessTone = (code: string): Tone =>
  code === "PASS" ? "positive" : code === "FAIL" ? "negative" : code === "GATED" ? "neutral" : "info";

const AUTH_STATES: Record<string, string> = {
  CONNECTED: "Conectado",
  AVAILABLE: "Disponible",
  NOT_CONFIGURED: "Sin configurar",
  AUTH_REQUIRED: "Requiere inicio de sesión",
  EXPIRED: "Sesión expirada",
  RATE_LIMITED: "Límite de uso alcanzado",
  DEGRADED: "Degradado",
  DISABLED: "Deshabilitado",
  ERROR: "Error",
  UNSUPPORTED: "No compatible",
  UNKNOWN: "Desconocido",
};
export const authState = (code: string | null | undefined) => lookup(AUTH_STATES, code);
export const authTone = (code: string | null | undefined): Tone =>
  code === "CONNECTED" || code === "AVAILABLE"
    ? "positive"
    : code === "AUTH_REQUIRED" || code === "NOT_CONFIGURED" || code === "EXPIRED"
      ? "warning"
      : code === "ERROR" || code === "RATE_LIMITED" || code === "DEGRADED"
        ? "negative"
        : "neutral";

const BILLING: Record<string, string> = { api: "API", subscription: "Suscripción", disabled: "Deshabilitado", unknown: "Desconocido" };
export const billingMode = (code: string | null | undefined) => lookup(BILLING, code);

const COMPLIANCE: Record<string, string> = {
  APPROVED: "Aprobada",
  PENDING_REVIEW: "Pendiente de revisión",
  REJECTED: "Rechazada",
};
export const compliance = (code: string | null | undefined) => lookup(COMPLIANCE, code);

const CATEGORIES: Record<string, string> = {
  market: "Mercado",
  news: "Noticias",
  social: "Social",
};
export const sourceCategory = (code: string | null | undefined) => lookup(CATEGORIES, code);

const SOURCE_NAMES: Record<string, string> = {
  "market-data": "Precios de QQQ",
  "sec-press-releases": "SEC · comunicados",
  "cftc-news": "CFTC · noticias",
  "federal-reserve-news": "Reserva Federal · noticias",
  "reuters-markets": "Reuters · mercados",
  "ap-news-business": "AP News · negocios",
  "financial-times-markets": "Financial Times · mercados",
  "social-official-api": "API social oficial (X / Reddit)",
};
export const sourceName = (id: string) => SOURCE_NAMES[id] ?? humanize(id);

const RECOVERY_ACTIONS: Record<string, string> = {
  "No action required.": "No requiere acción.",
  "Add the exact URL and explicit review acknowledgement.": "Agrega la URL exacta y confirma la revisión de la fuente.",
  "Configure an official Reddit/X API credential.": "Configura una credencial oficial de la API de Reddit o X.",
  "Configure the market-data provider credentials.": "Guarda las claves de Alpaca Paper en Ajustes para recibir precios.",
  "QQQ quotes and bars from the configured market-data provider.": "Cotizaciones y velas de QQQ del proveedor de datos configurado.",
};
export const sourceText = (text: string) => RECOVERY_ACTIONS[text] ?? text;

interface AgentCopy {
  name: string;
  role: string;
  deterministic?: boolean;
}

const AGENTS: Record<string, AgentCopy> = {
  market: { name: "Mercado", role: "Interpreta el estado normalizado del mercado: precio, spread, volumen, liquidez y estructura." },
  technical: { name: "Técnico", role: "Evalúa tendencia, momentum, reversión, volatilidad y patrones estadísticos." },
  regime: { name: "Régimen", role: "Clasifica el régimen de mercado y las familias de estrategia compatibles." },
  news: { name: "Noticias", role: "Analiza noticias públicas recolectadas legalmente: relevancia, credibilidad y severidad." },
  social: { name: "Social", role: "Analiza señales sociales públicas: sentimiento, actividad inusual, bots y manipulación." },
  strategy: { name: "Estrategia", role: "Combina evaluaciones en una propuesta de operación o en «no operar». Nunca genera órdenes." },
  critic: { name: "Crítico", role: "Intenta refutar cada propuesta: supuestos, contradicciones, liquidez, datos viejos y riesgo/beneficio." },
  master_orchestrator: { name: "Orquestador", role: "Coordina el pipeline determinista y los límites de contexto de cada agente.", deterministic: true },
  optimizer: { name: "Optimizador", role: "Evalúa resultados fuera de línea y propone cambios revisables. Nunca edita producción.", deterministic: true },
  position_manager: { name: "Gestor de posiciones", role: "Recomienda mantener, mover stop, cerrar parcial o total. No ejecuta por sí mismo.", deterministic: true },
  session_guardian: { name: "Guardián de sesión", role: "Vigila PnL, drawdown y rachas; puede recomendar reducir o detener entradas.", deterministic: true },
};

export const agentName = (id: string) => AGENTS[id]?.name ?? humanize(id);
export const agentRole = (id: string, fallback: string) => AGENTS[id]?.role ?? fallback;
export const agentDeterministic = (id: string) => Boolean(AGENTS[id]?.deterministic);

const AGENT_STATUS: Record<string, string> = { IDLE: "En espera", ACTIVE: "Analizando", ERROR: "Con error" };
export const agentStatus = (code: string) => lookup(AGENT_STATUS, code);
export const agentTone = (code: string): Tone => (code === "ACTIVE" ? "accent" : code === "ERROR" ? "negative" : "neutral");

const MEMORY_COMPONENTS: Record<string, string> = {
  working_memory: "Memoria de trabajo",
  historical_db: "Historial",
  timescale: "Series temporales",
  pgvector: "Búsqueda semántica",
  embeddings: "Modelo de embeddings",
  parquet: "Archivo Parquet",
  vault: "Bóveda de conocimiento",
};
export const memoryComponent = (code: string) => lookup(MEMORY_COMPONENTS, code);

const HEALTH: Record<string, string> = { HEALTHY: "Saludable", DEGRADED: "Degradado", FAILED: "Falla" };
export const health = (code: string) => lookup(HEALTH, code);
export const healthTone = (code: string): Tone => (code === "HEALTHY" ? "positive" : code === "FAILED" ? "negative" : "warning");

const KNOWLEDGE_STATUS: Record<string, string> = { ACTIVE: "Activo", NEEDS_REVALIDATION: "Por revalidar", RETIRED: "Retirado" };
export const knowledgeStatus = (code: string) => lookup(KNOWLEDGE_STATUS, code);

const MEMORY_TYPES: Record<string, string> = {
  OBSERVATION: "Observación",
  HYPOTHESIS: "Hipótesis",
  PATTERN: "Patrón",
  RULE: "Regla",
  LESSON: "Lección",
  FAILURE: "Falla",
  RESEARCH: "Investigación",
  CONFIRMED_KNOWLEDGE: "Conocimiento confirmado",
  RETIRED_KNOWLEDGE: "Conocimiento retirado",
};
export const memoryType = (code: string) => lookup(MEMORY_TYPES, code);

const TRACE_TYPES: Record<string, string> = {
  agent_runs: "Ejecución de agente",
  trade_proposals: "Propuesta",
  critic_reviews: "Revisión del crítico",
  risk_decisions: "Decisión de riesgo",
  orders: "Orden",
  fills: "Ejecución",
  system_events: "Evento del sistema",
  source_runs: "Recolección de fuente",
  model_usage: "Uso de modelo",
};
export const traceType = (code: string) => lookup(TRACE_TYPES, code);

const SESSION_ACTIONS: Record<string, string> = {
  CONTINUE: "Operando con normalidad",
  STOP_LIVE_FOR_DAY: "Entradas detenidas por hoy",
};
export const sessionAction = (code: string) => lookup(SESSION_ACTIONS, code);

const SIDES: Record<string, string> = { buy: "Compra", sell: "Venta", long: "Larga", short: "Corta" };
export const side = (code: string | null | undefined) => lookup(SIDES, code);


const CONFIG_ERRORS: Record<string, string> = {
  "max_base_risk_usd cannot exceed configured capital": "El riesgo máximo por operación no puede superar el capital.",
  "capital_usd must be positive": "El capital debe ser mayor que cero.",
  "at least one executable symbol is required": "Debe haber al menos un instrumento operable.",
  "primary_provider cannot be empty": "Elige un proveedor principal.",
  "primary_provider is not supported by this release": "Ese proveedor principal no está disponible en esta versión.",
  "fallback providers require one configured primary provider": "Para usar respaldos primero elige un proveedor principal.",
  "primary provider cannot also be a fallback provider": "El proveedor principal no puede ser también respaldo.",
  "primary_profile cannot be empty": "El perfil del modelo no puede estar vacío.",
  "every news feed must be explicitly reviewed": "Cada fuente de noticias debe estar revisada explícitamente.",
  "collection backoff cannot be shorter than its interval": "La espera máxima entre reintentos no puede ser menor que el intervalo.",
  "daily loss cap cannot exceed weekly loss cap": "La pérdida diaria máxima no puede superar la semanal.",
  "operating_region must be a two-letter ISO country code": "El país debe ser un código ISO de dos letras, por ejemplo US.",
  "fallback providers must be unique and ordered": "Los respaldos no pueden repetirse.",
  "authentication mode must be api or subscription": "El modo de acceso debe ser suscripción o API.",
};
const CONFIG_ERROR_PATTERNS: Array<[RegExp, (m: RegExpExecArray) => string]> = [
  [/^fallback provider is not supported: (.+)$/, (m) => `El respaldo «${m[1]}» no está disponible.`],
  [/^API mode provider is not supported: (.+)$/, (m) => `«${m[1]}» no admite el modo API.`],
  [/^subscription mode only accepts providers with official subscription login: (.+)$/, (m) => `«${m[1]}» no tiene inicio de sesión oficial por suscripción.`],
  [/^news feed is not allowlisted: (.+)$/, (m) => `La fuente «${m[1]}» no está en la lista permitida.`],
  [/^news feed URL is invalid: (.+)$/, (m) => `La URL de la fuente «${m[1]}» no es válida.`],
  [/^news source is not allowlisted: (.+)$/, (m) => `La fuente «${m[1]}» no está en la lista permitida.`],
];
/** Config validation messages (also inside pydantic "Value error, …" wrappers and "; " lists). */
export function configError(message: string): string {
  return message
    .split("; ")
    .map((part) => {
      const text = part.replace(/^Value error, /, "").trim();
      const known = CONFIG_ERRORS[text];
      if (known) return known;
      for (const [pattern, render] of CONFIG_ERROR_PATTERNS) {
        const match = pattern.exec(text);
        if (match) return render(match);
      }
      return text;
    })
    .join(" ");
}

const API_ERRORS: Record<string, string> = {
  broker_not_connected: "Conecta tu cuenta Alpaca Paper en Ajustes antes de iniciar el motor.",
  key_missing: "Falta la clave o el email de esta fuente.",
  robots_disallowed: "La web de la fuente no permite leer esa página de forma automática.",
  source_auth_failed: "La fuente rechazó la petición (clave o email no válidos).",
  source_rate_limited: "La fuente pidió esperar: se reintentará más tarde.",
  source_timeout: "La fuente tardó demasiado en responder.",
  source_unreachable: "No se pudo contactar con la fuente.",
  source_http_error: "La fuente devolvió un error.",
  universe_unavailable: "Aún no hay composición del Nasdaq-100 (hace falta el email de contacto para la SEC).",
  breadth_unavailable: "No hay precios suficientes de los componentes.",
  strategy_not_enabled: "Esa estrategia no está activa.",
  symbol_not_allowed: "Hyverion solo opera QQQ; ese símbolo es solo un sensor.",
  SYMBOL_NOT_EXECUTION_WHITELISTED: "Hyverion solo puede operar QQQ; cualquier otro símbolo se rechaza.",
  market_data_unavailable: "No se pudieron descargar datos de mercado; revisa tu conexión.",
  market_data_credentials_missing: "Faltan las claves de Alpaca Paper: guárdalas en Ajustes para ver precios reales de QQQ.",
  market_data_provider_not_ready: "El proveedor de datos de mercado aún no está disponible.",
  market_data_auth_failed: "Alpaca rechazó las claves: revisa que sean las de la cuenta paper.",
  market_data_rate_limited: "Alpaca limitó las consultas por un momento; se reintentará.",
  market_data_invalid_quote: "La cotización recibida no era válida; no se usa ningún precio inventado.",
  market_data_history_too_large: "Se pidió demasiado historial de una vez.",
  broker_credentials_missing: "Faltan las claves de Alpaca Paper en el Llavero.",
  broker_auth_failed: "Alpaca rechazó las claves: revisa que sean las de la cuenta paper.",
  broker_unreachable: "No se pudo conectar con Alpaca; se reintentará.",
  broker_live_host_forbidden: "Bloqueado: solo se permite el servidor paper de Alpaca.",
  broker_paper_only: "Esta versión solo opera en simulación (paper).",
  broker_is_simulator: "Usas el simulador interno: no hay cuenta externa que verificar.",
  QUANTITY_BELOW_MINIMUM: "La cantidad calculada por riesgo es menor que la mínima que acepta el broker; no se opera.",
  data_audit_failed: "Los datos históricos no pasaron la auditoría (faltan barras, hay duplicados o saltos imposibles).",
  market_closed: "El mercado de EE. UU. está cerrado.",
  insufficient_history: "No hay suficiente historial para una prueba fiable.",
  prices_required: "Hacen falta precios reales para esta prueba.",
  interval_not_allowed: "Esa temporalidad no está disponible.",
  limit_out_of_range: "Se pidieron demasiados datos de una vez.",
  forbidden_target: "Este cambio toca algo protegido (límites de riesgo o agentes de seguridad) y no se puede aplicar.",
  invalid_params: "Los valores del cambio están fuera de los rangos permitidos.",
  not_applicable: "Este cambio no se aplica solo: hay que hacerlo a mano en el código.",
  unknown_component: "El agente o la estrategia de este cambio ya no existe.",
  version_mismatch: "La versión escrita en el documento no coincide con la del cambio.",
  invalid_state: "Este cambio ya no está pendiente de revisión.",
  proposal_not_found: "El cambio ya no existe.",
  nothing_to_undo: "No hay una versión anterior a la que volver.",
  component_not_found: "No hay historial para este componente todavía.",
  agent_not_found: "Ese agente no existe.",
  optimizer_running: "La búsqueda de mejoras ya está en marcha.",
  optimizer_failed: "La búsqueda de mejoras falló (probablemente sin conexión a los datos de mercado).",
  "control API authentication required": "La sesión con el núcleo no es válida; reinicia la app.",
  "operation not found": "La operación ya no existe.",
  "unknown provider": "Proveedor desconocido.",
  "format must be json or prometheus": "Formato de exportación no válido.",
  "note cannot be opened safely": "La nota no se puede abrir de forma segura.",
  "not a vault note": "Eso no es una nota de la bóveda.",
  "path escapes the vault root": "La ruta sale de la bóveda.",
  "note not found": "La nota ya no existe; puede haberse movido al regenerar la bóveda.",
  "owner notes are too long": "Tus notas superan el máximo de 20 000 caracteres.",
  "owner notes cannot contain Hyverion markers": "Tus notas no pueden contener marcadores de Hyverion (<!-- hyverion:… -->).",
  "this note has no owner section": "Esta nota no tiene sección para tus notas.",
  "Configuration saved. Restart the engine to apply it everywhere.": "Configuración guardada. Reinicia el motor para aplicarla en todo.",
  "No configuration changes were supplied.": "No había cambios que guardar.",
};
/** Any backend error detail shown to the operator: known texts, engine codes, config and lab errors. */
export function apiError(message: string): string {
  const known = API_ERRORS[message];
  if (known) return known;
  const engine = engineErrorMessage(message);
  if (engine !== message) return engine;
  const lab = labErrorMessage(message);
  if (lab !== message) return lab;
  return configError(message);
}

const PROVIDER_DETAILS: Record<string, string> = {
  "Ready to launch the provider's official subscription authentication flow.": "Listo para abrir el inicio de sesión oficial del proveedor.",
  "Run the official subscription status check.": "Ejecuta la verificación oficial del estado de la suscripción.",
  "Install the provider's official CLI and sign in with its subscription flow.": "Instala la CLI oficial del proveedor e inicia sesión con tu suscripción.",
  "Install the provider's official subscription CLI to connect this account.": "Instala la CLI oficial del proveedor para conectar esta cuenta.",
  "No supported subscription CLI is configured for this provider.": "Este proveedor no tiene una CLI de suscripción compatible.",
  "This provider has no supported official subscription login flow.": "Este proveedor no tiene un inicio de sesión oficial por suscripción.",
  "Codex is authenticated with ChatGPT subscription access.": "Codex está conectado con tu suscripción de ChatGPT.",
  "Codex is using API-key authentication; sign in with ChatGPT to use the subscription.": "Codex está usando una API key; inicia sesión con ChatGPT para usar la suscripción.",
  "Codex returned no recognized subscription status.": "Codex no devolvió un estado de suscripción reconocible.",
  "Claude Code is authenticated with claude.ai subscription access.": "Claude Code está conectado con tu suscripción de claude.ai.",
  "Claude Code is authenticated without a claude.ai subscription session.": "Claude Code está conectado sin una sesión de suscripción de claude.ai.",
  "Claude Code returned no recognized subscription status.": "Claude Code no devolvió un estado de suscripción reconocible.",
  "Subscription status checked.": "Estado de la suscripción verificado.",
};
export function providerDetail(text: string): string {
  const known = PROVIDER_DETAILS[text];
  if (known) return known;
  const install = /^Install the official (\S+) CLI and sign in/.exec(text);
  if (install) return `Instala la CLI oficial «${install[1]}» e inicia sesión con tu suscripción.`;
  return text;
}

const ERROR_CODES: Record<string, string> = {
  provider_timeout: "El proveedor de IA no respondió a tiempo",
  provider_unavailable: "Proveedor de IA no disponible",
  provider_rate_limited: "Límite de uso del proveedor alcanzado",
  schema_validation_failed: "La respuesta no cumplió el esquema",
  budget_exhausted: "Límite de la suscripción de IA alcanzado",
  "reviewed provider URL is not configured": "Falta configurar la URL revisada de la fuente",
};
/** Backend error codes and short error messages shown to the operator. */
export const errorCode = (code: string | null | undefined) => (code ? ERROR_CODES[code] ?? humanize(code) : "—");

const TRACE_STATUS: Record<string, string> = {
  ALLOW: "Aprobada",
  DENY: "Rechazada",
  REDUCE: "Reducida",
  APPROVE: "Aprobada",
  REJECT: "Rechazada",
  PROPOSED: "Propuesta",
  FAILED: "Fallida",
  CANCELLED: "Cancelada",
  RUNNING: "En curso",
  SUCCEEDED: "Correcta",
  SUCCESS: "Correcta",
  FILLED: "Ejecutada",
};
export const traceStatus = (code: string | null | undefined) => lookup(TRACE_STATUS, code);
export const traceStatusTone = (code: string | null | undefined): Tone =>
  code === "ALLOW" || code === "APPROVE" || code === "SUCCEEDED" || code === "SUCCESS" || code === "FILLED"
    ? "positive"
    : code === "DENY" || code === "REJECT" || code === "FAILED"
      ? "negative"
      : code === "REDUCE"
        ? "warning"
        : "neutral";

const READINESS_OVERALL: Record<string, string> = {
  PAPER_READY: "Lista para simulación",
  PAPER_BLOCKED: "Simulación bloqueada",
};
export const readinessOverall = (code: string | null | undefined) => lookup(READINESS_OVERALL, code);
export const readinessOverallTone = (code: string | null | undefined): Tone =>
  code === "PAPER_READY" ? "positive" : code === "PAPER_BLOCKED" ? "negative" : "neutral";

// --- Aprendizaje ------------------------------------------------------------

const STRATEGY_NAMES: Record<string, string> = {
  trend_pullback: "Retroceso en tendencia",
  opening_range_breakout: "Ruptura del rango de apertura",
  mean_reversion: "Reversión al VWAP",
  trend_momentum: "Tendencia y momentum (línea base)",
  // Retired with the QQQ migration; kept so old history still reads well.
  breakout: "Ruptura (retirada)",
  regime_mean_reversion: "Reversión a la media (retirada)",
};
const RESEARCH_REGIMES: Record<string, string> = {
  trending_up: "Tendencia alcista",
  trending_down: "Tendencia bajista",
  ranging: "Lateral",
  unknown: "Sin clasificar",
};
export const researchRegime = (code: string) => lookup(RESEARCH_REGIMES, code);
export const strategyName = (code: string) => lookup(STRATEGY_NAMES, code);
/** Agent or strategy display name (versions and changes cover both). */
export const componentName = (id: string) => STRATEGY_NAMES[id] ?? agentName(id);
export const isStrategy = (id: string) => id in STRATEGY_NAMES;

const OPTIMIZER_OUTCOMES: Record<string, string> = {
  proposed: "Encontró una mejora y la dejó para que la revises.",
  not_proven: "Encontró una opción, pero no demostró ser mejor con datos que no usó para elegirla.",
  no_better_candidate: "La versión actual ya es la mejor de las opciones probadas.",
  pending_review: "Ya hay un cambio de esta estrategia esperando tu revisión.",
};
export const optimizerOutcome = (code: string) => lookup(OPTIMIZER_OUTCOMES, code);

const EXIT_REASONS: Record<string, string> = {
  TIME_EXIT: "Por tiempo",
  horizon_expired: "Por tiempo",
  protective_stop_reached: "Stop de protección",
  protective_stop_missing: "Sin stop verificado",
  stop: "Stop de protección",
  target_reached: "Objetivo alcanzado",
  target: "Objetivo alcanzado",
  FLATTEN: "Cierre de emergencia",
  flatten: "Cierre de emergencia",
  thesis_invalidated: "La idea dejó de valer",
  time: "Por tiempo",
  manual: "Manual",
};
export const exitReason = (code: string | null | undefined) => (code ? EXIT_REASONS[code] ?? humanize(code) : "—");

/**
 * Plain-Spanish glossary for the few technical words the app cannot avoid.
 * Shown by <InfoHint term="…" /> next to the word.
 */
export const GLOSSARY = {
  drawdown: "Caída desde el punto más alto: cuánto dinero se perdió desde el mejor momento antes de recuperarse.",
  spread: "Diferencia entre el precio de compra y el de venta. Cuanto más grande, más cuesta entrar y salir.",
  slippage: "Deslizamiento: diferencia entre el precio que se esperaba y el que realmente se consiguió.",
  stop: "Precio al que la operación se cierra sola para limitar la pérdida.",
  target: "Precio al que la operación se cierra sola para tomar la ganancia.",
  r: "R es la pérdida máxima prevista de una operación. Un objetivo de 2 R busca ganar el doble de lo que arriesga.",
  oos: "Fuera de muestra: datos que no se usaron para elegir el ajuste. Es la prueba honesta de si algo funciona.",
  regime: "Tipo de mercado: tendencia alcista, tendencia bajista o lateral (sin dirección clara).",
  expectancy: "Resultado medio por operación después de comisiones. Positivo significa que, en promedio, gana.",
  fees: "Comisiones y tasas que cobra el broker en cada compra y venta.",
  exposure: "Parte del capital que está invertida en operaciones abiertas.",
  bps: "Puntos básicos: 100 bps equivalen a 1 %.",
} as const;
export type GlossaryTerm = keyof typeof GLOSSARY;

export const experimentTone = (code: string | null | undefined): Tone =>
  code === "COMPLETED" ? "positive" : code === "FAILED" ? "negative" : code === "RUNNING" ? "info" : "neutral";

const ORDER_STATUS: Record<string, string> = {
  OPEN: "Abierta",
  PARTIALLY_FILLED: "Parcialmente ejecutada",
  FILLED: "Ejecutada",
  CANCELED: "Cancelada",
  REJECTED: "Rechazada",
  UNKNOWN: "Sin confirmar",
};
export const orderStatus = (code: string | null | undefined) => lookup(ORDER_STATUS, code);
export const orderStatusTone = (code: string | null | undefined): Tone =>
  code === "FILLED" ? "positive" : code === "UNKNOWN" || code === "REJECTED" ? "negative" : code === "PARTIALLY_FILLED" || code === "OPEN" ? "warning" : "neutral";


const CHANGE_PROPOSAL_STATUS: Record<string, string> = {
  PROPOSED: "Propuesta",
  TESTING: "En prueba",
  READY_FOR_REVIEW: "Por revisar",
  APPROVED: "Aprobada",
  REJECTED: "Rechazada",
  DEPLOYED: "En uso",
  ROLLED_BACK: "Deshecha",
};
export const changeProposalStatus = (code: string | null | undefined) => lookup(CHANGE_PROPOSAL_STATUS, code);
export const changeProposalTone = (code: string | null | undefined): Tone =>
  code === "APPROVED" || code === "DEPLOYED"
    ? "positive"
    : code === "REJECTED" || code === "ROLLED_BACK"
      ? "negative"
      : code === "READY_FOR_REVIEW"
        ? "warning"
        : code === "TESTING"
          ? "info"
          : "neutral";


export const knowledgeStatusTone = (code: string | null | undefined): Tone =>
  code === "ACTIVE" ? "positive" : code === "NEEDS_REVALIDATION" ? "warning" : "neutral";

const CANDIDATE_STATUS: Record<string, string> = { PENDING: "Pendientes", VALIDATING: "En validación", PROMOTED: "Promovidos", REJECTED: "Rechazados" };
const CONFLICT_TYPES: Record<string, string> = { opposing_claims: "Afirmaciones opuestas" };
export const conflictType = (code: string) => lookup(CONFLICT_TYPES, code);
export const candidateStatus = (code: string) => lookup(CANDIDATE_STATUS, code);

/** Failure detail codes from the subscription login flow. */
export function loginFailure(code: string): string {
  switch (code) {
    case "login_timeout": return "Pasaron 5 minutos sin completar el acceso. Inténtalo de nuevo.";
    case "cancelled": return "Cancelaste el inicio de sesión.";
    case "cli_unavailable": return "Falta instalar la CLI oficial del proveedor.";
    case "cli_launch_failed": return "No se pudo abrir la CLI oficial del proveedor.";
    case "login_command_failed": return "El proveedor terminó el acceso con un error.";
    default: return "El acceso terminó pero la suscripción no quedó conectada.";
  }
}

/** Why the engine paused a subscription (stable codes from the provider circuit breaker). */
export function circuitReason(code: string | null | undefined): string {
  switch (code) {
    case "provider_auth_required": return "la sesión venció o no está iniciada";
    case "provider_quota_exhausted": return "se agotó el cupo de la suscripción";
    case "provider_rate_limited": return "límite de solicitudes temporal";
    case "provider_overloaded": return "el proveedor está saturado";
    case "subscription_cli_missing": return "falta instalar la CLI oficial";
    case "provider_timeout": return "el proveedor no respondió a tiempo";
    default: return "error del proveedor";
  }
}

const LEGACY_WHY_NOW: Record<string, string> = {
  "A fresh price displacement cleared the previous local range.": "Un desplazamiento reciente del precio superó el rango local anterior.",
  "A low-volatility pullback created a bounded reversion setup.": "Un retroceso con baja volatilidad dejó una reversión acotada.",
  "Positive trend and momentum alignment on fresh public market data.": "Tendencia y momentum alineados al alza con datos públicos frescos.",
};
/** Strategy rationale; rows written before 2026-09-26 are in English. */
export const whyNow = (text: string) => LEGACY_WHY_NOW[text] ?? text;

/** Stable engine error codes from POST /engine/start. */
export function engineErrorMessage(code: string): string {
  if (code === "engine_not_running") return "Inicia el motor para que pueda cerrar las posiciones.";
  if (code === "broker_not_connected") return "Conecta tu cuenta Alpaca Paper en Ajustes antes de iniciar el motor.";
  if (code === "engine_already_running") return "Ya hay un motor de simulación en ejecución (por ejemplo, iniciado desde la terminal).";
  if (code === "live_not_allowed") return "La app solo inicia el motor en modo simulación.";
  if (code === "invalid_interval") return "El intervalo mínimo es de 5 segundos.";
  if (code === "spawn_failed") return "No se pudo lanzar el proceso del motor.";
  return code;
}

/** Backend operator errors for memory decisions and proposal transitions. */
export function labErrorMessage(message: string): string {
  if (message.startsWith("only ACTIVE knowledge can be confirmed")) return "Solo se puede confirmar conocimiento activo.";
  if (message.endsWith("cannot be confirmed")) return "Esta categoría de memoria no se puede confirmar; solo hipótesis, patrones y lecciones.";
  if (message.startsWith("needs >=")) return `Evidencia insuficiente para confirmar: se requieren al menos 5 usos evaluados y fiabilidad ≥ 60%. (${message})`;
  if (message.startsWith("more failures than successes")) return "Tiene más fallos que aciertos; no se puede confirmar.";
  if (message.startsWith("knowledge is already retired")) return "Este conocimiento ya está retirado.";
  if (message.startsWith("a reason of 3-200 characters")) return "El motivo debe tener entre 3 y 200 caracteres.";
  if (message.startsWith("invalid change proposal transition")) return "Esa transición no está permitida desde el estado actual de la propuesta.";
  if (message === "change proposal not found") return "La propuesta de cambio ya no existe.";
  if (message === "memory not found") return "La memoria ya no existe.";
  if (message.startsWith("walk-forward replay requires")) return "El walk-forward requiere al menos 10 observaciones de precio.";
  return message;
}

const MEMORY_DETAILS: Record<string, string> = {
  "in-process: single process, not shared": "En proceso: un solo proceso, no compartida",
  "SQLite: semantic search off": "SQLite: búsqueda semántica desactivada",
  "SQLite: not applicable": "SQLite: no aplica",
  "local model present; hashes checked on load": "Modelo local presente; integridad verificada al cargar",
};
export const memoryDetail = (text: string) => MEMORY_DETAILS[text] ?? text;
