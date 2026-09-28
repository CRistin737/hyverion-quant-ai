import { FileText, Network, NotebookPen, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useSaveOwnerNotes, useVault, useVaultNote } from "@/api/queries";
import type { VaultNoteSummary } from "@/api/types";
import { knowledgeStatus, memoryType } from "@/i18n/labels";
import { Button, IconButton } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Section } from "@/ui/data";
import { Badge, EmptyState, ErrorState, SkeletonRows } from "@/ui/feedback";
import { KnowledgeGraph } from "@/ui/graph";
import { Markdown } from "@/ui/markdown";
import { Segmented } from "@/ui/nav";
import { useToast } from "@/ui/toast";

import { errorText } from "../shared";

const FOLDER_LABELS: Record<string, string> = {
  "00-System": "Sistema",
  "01-Agents": "Agentes",
  "02-Strategies": "Estrategias",
  "03-Markets": "Mercados",
  "04-Regimes": "Regímenes",
  "05-Trade-Reviews": "Revisiones de operaciones",
  "06-Daily-Lessons": "Lecciones",
  "07-Patterns": "Patrones",
  "08-Failures": "Fallas",
  "09-Agent-Learnings": "Aprendizajes de agentes",
  "10-Hypotheses": "Hipótesis",
  "11-Rules": "Reglas",
  "12-Research": "Investigación",
  "13-Retired-Knowledge": "Conocimiento retirado",
};

const folderLabel = (folder: string) => FOLDER_LABELS[folder] ?? folder.replace(/^\d+-/, "").replace(/-/g, " ");

function OwnerNotes({ path, initial, editable }: { path: string; initial: string; editable: boolean }) {
  const [draft, setDraft] = useState(initial);
  const save = useSaveOwnerNotes();
  const toast = useToast();
  useEffect(() => setDraft(initial), [initial, path]);
  const dirty = draft.trim() !== initial.trim();

  if (!editable) {
    return <p className="text-body-2 text-fg-muted">Las notas de índice se regeneran desde la base de datos y no admiten anotaciones.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      <textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        maxLength={20_000}
        rows={5}
        aria-label="Tus notas"
        placeholder="Anota lo que observaste. Hyverion conserva esta sección cada vez que regenera la nota."
        className="w-full resize-y rounded-sm bg-surface-2 px-3 py-2 text-body text-fg shadow-[inset_0_0_0_1px_var(--border-default)] placeholder:text-fg-muted focus-visible:outline-2"
      />
      <div className="flex items-center gap-2">
        <span className="flex-1 text-caption text-fg-muted">
          {save.error ? <span className="text-danger">{errorText(save.error)}</span> : "Solo para ti: los agentes de IA no leen estas notas."}
        </span>
        {dirty ? (
          <Button size="sm" variant="ghost" onClick={() => setDraft(initial)}>
            Descartar
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="primary"
          disabled={!dirty}
          loading={save.isPending}
          onClick={() => save.mutate({ path, notes: draft }, { onSuccess: () => toast({ tone: "success", title: "Notas guardadas en la bóveda" }) })}
        >
          Guardar notas
        </Button>
      </div>
    </div>
  );
}

function NoteView({ path, notes, onOpen }: { path: string; notes: VaultNoteSummary[]; onOpen: (path: string) => void }) {
  const note = useVaultNote(path);
  const byName = useMemo(() => new Map(notes.map((n) => [n.name, n.path])), [notes]);
  const titles = useMemo(() => new Map(notes.map((n) => [n.path, n.title])), [notes]);

  if (note.isLoading) return <SkeletonRows rows={6} />;
  if (note.error || !note.data) {
    return <ErrorState title="No se pudo abrir la nota" impact="Puede haberse movido al regenerar la bóveda." detail={note.error ? errorText(note.error) : undefined} onRetry={() => void note.refetch()} />;
  }
  const data = note.data;
  const fm = data.frontmatter;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_260px] gap-8 max-xl:grid-cols-1">
      <article className="flex min-w-0 flex-col gap-6">
        <div className="flex flex-wrap items-center gap-2">
          {typeof fm.type === "string" ? <Badge>{memoryType(fm.type.toUpperCase())}</Badge> : null}
          {typeof fm.status === "string" ? <Badge tone={fm.status === "active" ? "positive" : fm.status === "retired" ? "neutral" : "warning"}>{knowledgeStatus(fm.status.toUpperCase())}</Badge> : null}
          {typeof fm.reliability === "number" ? <span className="text-caption text-fg-muted">Fiabilidad {(fm.reliability * 100).toFixed(0)}%</span> : null}
          {typeof fm.version === "number" ? <span className="text-caption text-fg-muted">· versión {fm.version}</span> : null}
          <span className="ml-auto font-mono text-[11px] text-fg-muted">{data.path}</span>
        </div>
        <Markdown source={data.body} resolves={(target) => byName.has(target)} onWikilink={(target) => { const next = byName.get(target); if (next) onOpen(next); }} />
        <Section title="Tus notas">
          <OwnerNotes path={data.path} initial={data.owner_notes} editable={data.editable} />
        </Section>
      </article>
      <aside className="flex flex-col gap-6">
        <Section title="Enlaces">
          {data.links.length ? (
            <ul className="flex flex-col">
              {data.links.map((link) => (
                <li key={link.name}>
                  <button type="button" disabled={!link.path} onClick={() => link.path && onOpen(link.path)} className="flex h-8 w-full items-center gap-2 rounded-sm px-1 text-left text-body-2 text-fg-2 hover:bg-surface-hover hover:text-fg disabled:text-fg-muted">
                    <FileText size={14} strokeWidth={1.5} aria-hidden className="shrink-0 text-fg-muted" />
                    <span className="truncate">{(link.path && titles.get(link.path)) || link.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-body-2 text-fg-muted">Sin enlaces salientes.</p>
          )}
        </Section>
        <Section title="Mencionada en">
          {data.backlinks.length ? (
            <ul className="flex flex-col">
              {data.backlinks.map((link) => (
                <li key={link.path}>
                  <button type="button" onClick={() => onOpen(link.path)} className="flex h-8 w-full items-center gap-2 rounded-sm px-1 text-left text-body-2 text-fg-2 hover:bg-surface-hover hover:text-fg">
                    <FileText size={14} strokeWidth={1.5} aria-hidden className="shrink-0 text-fg-muted" />
                    <span className="truncate">{link.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-body-2 text-fg-muted">Ninguna otra nota enlaza aquí.</p>
          )}
        </Section>
      </aside>
    </div>
  );
}

/**
 * In-app Knowledge Vault: the Markdown export of strategic memory, readable and
 * navigable without Obsidian. The database stays authoritative; only the
 * owner's notes section is editable.
 */
export function Boveda() {
  const vault = useVault();
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<"nota" | "grafo">("nota");
  const [query, setQuery] = useState("");

  const notes = useMemo(() => vault.data?.notes ?? [], [vault.data]);
  const current = selected ?? notes.find((n) => n.kind === "knowledge")?.path ?? notes[0]?.path ?? null;
  const filtered = useMemo(() => {
    const text = query.trim().toLowerCase();
    return text ? notes.filter((n) => n.title.toLowerCase().includes(text) || n.name.toLowerCase().includes(text)) : notes;
  }, [notes, query]);
  const groups = useMemo(() => {
    const map = new Map<string, VaultNoteSummary[]>();
    for (const note of filtered) map.set(note.folder, [...(map.get(note.folder) ?? []), note]);
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  if (vault.isLoading) return <SkeletonRows rows={8} />;
  if (vault.error) return <ErrorState title="No se pudo leer la bóveda" detail={errorText(vault.error)} onRetry={() => void vault.refetch()} />;
  if (!notes.length) {
    return (
      <EmptyState
        icon={NotebookPen}
        title="La bóveda aún está vacía"
        description="El ciclo de memoria exporta aquí cada lección, patrón y falla validados, enlazados por mercado y régimen. Ejecuta un ciclo de memoria desde la vista Conocimiento."
      />
    );
  }

  const open = (path: string) => {
    setSelected(path);
    setView("nota");
  };

  return (
    <div className="flex flex-col gap-6">
    <p className="text-body-2 text-fg-muted">
      Cada lección o patrón como una nota enlazada por mercado y régimen. Puedes añadir tus propias notas al final de cada una; los agentes no las leen.
    </p>
    <div className="grid grid-cols-[250px_minmax(0,1fr)] gap-8 max-lg:grid-cols-1">
      <nav aria-label="Notas de la bóveda" className="flex flex-col gap-3">
        <div className="relative">
          <Search size={14} strokeWidth={1.5} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Buscar notas"
            aria-label="Buscar notas"
            className="h-8 w-full rounded-sm bg-surface-2 pl-8 pr-8 text-body text-fg shadow-[inset_0_0_0_1px_var(--border-default)] placeholder:text-fg-muted"
          />
          {query ? <IconButton icon={X} label="Borrar búsqueda" size="sm" className="absolute right-0.5 top-1/2 size-7 -translate-y-1/2" onClick={() => setQuery("")} /> : null}
        </div>
        <div className="flex flex-col gap-4">
          {groups.map(([folder, items]) => (
            <div key={folder} className="flex flex-col">
              <p className="px-2 pb-1 text-caption font-medium text-fg-muted">{folderLabel(folder)}</p>
              {items.map((note) => (
                <button
                  key={note.path}
                  type="button"
                  aria-current={note.path === current ? "true" : undefined}
                  onClick={() => open(note.path)}
                  className={cn(
                    "flex min-h-8 items-center gap-2 rounded-sm px-2 py-1 text-left text-body-2 transition-colors duration-[var(--duration-fast)]",
                    note.path === current ? "bg-surface-3 text-fg" : "text-fg-2 hover:bg-surface-hover hover:text-fg",
                  )}
                >
                  <span className="flex-1 truncate">{note.title}</span>
                  {note.has_owner_notes ? <NotebookPen size={12} strokeWidth={1.5} aria-label="Con tus notas" className="shrink-0 text-accent-text" /> : null}
                </button>
              ))}
            </div>
          ))}
          {!groups.length ? <p className="px-2 text-body-2 text-fg-muted">Sin coincidencias.</p> : null}
        </div>
      </nav>
      <div className="flex min-w-0 flex-col gap-5">
        <div className="flex items-center justify-between gap-4">
          <p className="text-body-2 text-fg-muted">
            {notes.length} notas · {vault.data?.edges.length ?? 0} enlaces. La base de datos es la fuente de verdad; esta vista no requiere Obsidian.
          </p>
          <Segmented
            label="Vista"
            value={view}
            onChange={setView}
            items={[
              { value: "nota", label: "Nota" },
              { value: "grafo", label: "Grafo" },
            ]}
          />
        </div>
        {view === "grafo" ? (
          <div className="rounded-md bg-surface-1 p-3">
            <div className="flex items-center gap-2 pb-1 text-caption text-fg-muted">
              <Network size={14} strokeWidth={1.5} aria-hidden /> Selecciona un nodo para abrir la nota.
            </div>
            <KnowledgeGraph
              nodes={notes.map((n) => ({ id: n.path, label: n.title, kind: n.kind }))}
              edges={vault.data?.edges ?? []}
              selected={current}
              onSelect={open}
              height={480}
            />
          </div>
        ) : current ? (
          <NoteView path={current} notes={notes} onOpen={open} />
        ) : null}
      </div>
    </div>
    </div>
  );
}
