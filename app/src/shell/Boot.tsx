import { LoaderCircle, TriangleAlert } from "lucide-react";

/** Shown while the Tauri shell starts the core, or when it cannot. */
export function Boot({ state }: { state: { status: "loading" } | { status: "error"; message: string } | { status: "ready" } }) {
  return (
    <div data-tauri-drag-region className="flex h-full items-center justify-center bg-bg">
      <div className="flex w-[380px] flex-col items-start gap-4">
        <svg viewBox="0 0 64 64" aria-hidden className="size-8 text-fg">
          <g fill="currentColor">
            <rect x="10" y="10" width="10" height="44" rx="1" />
            <rect x="44" y="10" width="10" height="44" rx="1" />
            <rect x="30.75" y="17" width="2.5" height="30" />
            <rect x="26" y="25" width="12" height="14" rx="1" />
          </g>
        </svg>
        {state.status === "error" ? (
          <div role="alert" className="flex flex-col gap-2">
            <p className="flex items-center gap-2 text-section font-semibold text-fg">
              <TriangleAlert size={16} className="text-warning" aria-hidden />
              No se pudo iniciar el núcleo de Hyverion
            </p>
            <p className="text-body-2 text-fg-2">
              La interfaz no puede mostrar datos sin el núcleo local. Ninguna operación se ejecuta mientras tanto.
            </p>
            <pre className="selectable whitespace-pre-wrap rounded-sm bg-surface-2 p-3 font-mono text-caption text-fg-2">{state.message}</pre>
            <p className="text-caption text-fg-muted">Cierra y vuelve a abrir la aplicación. Si persiste, revisa los registros en ~/Library/Application Support/Hyverion Quant AI/logs.</p>
          </div>
        ) : (
          <p className="flex items-center gap-2 text-body-2 text-fg-2">
            <LoaderCircle size={14} className="animate-spin text-fg-muted" aria-hidden />
            Iniciando el núcleo local…
          </p>
        )}
      </div>
    </div>
  );
}
