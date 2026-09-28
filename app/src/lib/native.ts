import { invoke, isTauri } from "@tauri-apps/api/core";

/**
 * Save a CSV export. In the app it goes to Downloads through the shell (the
 * webview cannot write files); in a browser it falls back to a normal download.
 * Returns where it was saved when known.
 */
export async function saveDownload(filename: string, content: string): Promise<string | null> {
  if (isTauri()) return invoke<string>("save_download", { filename, content });
  const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
  return null;
}

/** Print dialog of the current page ("Guardar como PDF" lives there). */
export async function printPage(): Promise<void> {
  if (isTauri()) {
    await invoke("print_page");
    return;
  }
  window.print();
}

/** Open an https link in the user's browser (the app window never navigates away). */
export async function openExternal(url: string): Promise<void> {
  if (!url.startsWith("https://")) return;
  if (isTauri()) {
    await invoke("open_external", { url });
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
