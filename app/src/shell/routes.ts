import { Activity, Home, Newspaper, Settings2, ShieldCheck, Sparkles, type LucideIcon } from "lucide-react";

export type DomainId = "inicio" | "trading" | "noticias" | "inteligencia" | "riesgo" | "ajustes";

export interface TabDef {
  value: string;
  label: string;
  /** One plain sentence: what the tab answers. Shown under the page title. */
  purpose: string;
}

export interface DomainDef {
  id: DomainId;
  label: string;
  icon: LucideIcon;
  path: string;
  shortcut: string;
  tabs?: TabDef[];
}

/**
 * Six areas, twelve tabs. Each thing is configured where it is shown:
 * sources in Noticias, models in Inteligencia, limits in Riesgo, the rest and
 * the system health check in Ajustes.
 */
export const DOMAIN_LIST: DomainDef[] = [
  { id: "inicio", label: "Inicio", icon: Home, path: "/inicio", shortcut: "1" },
  {
    id: "trading",
    label: "Trading",
    icon: Activity,
    path: "/trading",
    shortcut: "2",
    tabs: [
      { value: "mercado", label: "Mercado", purpose: "Cómo se mueve QQQ, el único instrumento que se opera." },
      { value: "operaciones", label: "Operaciones", purpose: "Cada operación de principio a fin, con sus órdenes, y las entradas que el control de riesgo frenó." },
      { value: "informe", label: "Informe", purpose: "Resultado de un día, un mes o un año, como un informe de negocio. Se descarga en PDF o Excel." },
    ],
  },
  {
    id: "noticias",
    label: "Noticias",
    icon: Newspaper,
    path: "/noticias",
    shortcut: "3",
    tabs: [
      { value: "calendario", label: "Calendario", purpose: "Qué va a pasar: reuniones de la Fed, datos de inflación y empleo, discursos y resultados de empresas." },
      { value: "noticias", label: "Noticias", purpose: "Todo lo que le llega al sistema ahora mismo y de qué fuente sale cada dato." },
      { value: "fuentes", label: "Fuentes", purpose: "De dónde saca el sistema la información. Si una falla, aquí se arregla." },
    ],
  },
  {
    id: "inteligencia",
    label: "Inteligencia",
    icon: Sparkles,
    path: "/inteligencia",
    shortcut: "4",
    tabs: [
      { value: "agentes", label: "Agentes", purpose: "Quién analiza el mercado, con qué modelo, qué está haciendo ahora y con qué instrucciones." },
      { value: "modelos", label: "Modelos", purpose: "Tus suscripciones de IA, cuánto queda de cada límite y qué modelo usa cada tarea." },
      { value: "aprendizaje", label: "Aprendizaje", purpose: "Cómo mejora la IA: lo que propone, lo que se aplicó solo tras probarse y lo que espera tu visto bueno." },
      { value: "memoria", label: "Memoria", purpose: "Lo que el sistema ha aprendido de las operaciones pasadas y usa para decidir." },
    ],
  },
  {
    id: "riesgo",
    label: "Riesgo",
    icon: ShieldCheck,
    path: "/riesgo",
    shortcut: "5",
    tabs: [
      { value: "limites", label: "Límites", purpose: "Cuánto puede arriesgar el sistema, como un trader profesional: un porcentaje de la cuenta." },
      { value: "decisiones", label: "Decisiones", purpose: "Por qué se aprobó o se frenó cada operación y con qué tamaño." },
    ],
  },
  { id: "ajustes", label: "Ajustes", icon: Settings2, path: "/ajustes", shortcut: "6" },
];

export const DOMAINS = Object.fromEntries(DOMAIN_LIST.map((d) => [d.id, d])) as Record<DomainId, DomainDef>;

export function domainFromPath(path: string): DomainId {
  const first = path.split("/")[1] ?? "";
  return (DOMAIN_LIST.find((d) => d.id === first)?.id ?? "inicio") as DomainId;
}

/** Tab values of one domain, in order (the first is the default). */
export function tabValues(domain: DomainId): string[] {
  return (DOMAINS[domain].tabs ?? []).map((tab) => tab.value);
}

export function tabPurpose(domain: DomainId, tab: string): string | undefined {
  return DOMAINS[domain].tabs?.find((item) => item.value === tab)?.purpose;
}

/** Old addresses (links in alerts, docs, muscle memory) and where they live now. */
const LEGACY: Array<[RegExp, string]> = [
  [/^\/trading\/posiciones\/?$/, "/trading/operaciones"],
  [/^\/trading\/ordenes\/?$/, "/trading/operaciones"],
  [/^\/laboratorio\/(memoria|boveda)\/?$/, "/inteligencia/memoria"],
  [/^\/laboratorio(\/.*)?$/, "/inteligencia/aprendizaje"],
  [/^\/aprendizaje\/memoria\/?$/, "/inteligencia/memoria"],
  [/^\/aprendizaje\/informes\/?$/, "/trading/informe"],
  [/^\/aprendizaje(\/.*)?$/, "/inteligencia/aprendizaje"],
  [/^\/inteligencia\/mercado\/?$/, "/noticias/calendario"],
  [/^\/inteligencia\/fuentes\/?$/, "/noticias/fuentes"],
  [/^\/ajustes\/cuenta-ia\/?$/, "/inteligencia/modelos"],
  [/^\/ajustes\/fuentes\/?$/, "/noticias/fuentes"],
  [/^\/ajustes\/riesgo\/?$/, "/riesgo/limites"],
  [/^\/ajustes\/(?!salud\/?$).+$/, "/ajustes"],
  [/^\/riesgo\/(auditoria|salud)\/?$/, "/ajustes/salud"],
  [/^\/riesgo\/decision\/?$/, "/riesgo/decisiones"],
];

export function legacyRedirect(path: string): string | null {
  for (const [pattern, target] of LEGACY) if (pattern.test(path)) return target;
  return null;
}
