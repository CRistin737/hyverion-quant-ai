import { Fragment, type ReactNode } from "react";

/**
 * Minimal, safe Markdown renderer for vault notes. It builds React elements
 * (never raw HTML), supports headings, paragraphs, lists, blockquotes, code,
 * **bold**, *italic*, `code` and Obsidian [[wikilinks|alias]].
 */

export interface MarkdownProps {
  source: string;
  onWikilink?: (target: string) => void;
  resolves?: (target: string) => boolean;
}

function inline(text: string, props: MarkdownProps, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|([^\]]*))?\]\])|(`[^`]+`)|(\*\*[^*]+\*\*)|(_[^_]+_|\*[^*]+\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = pattern.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const key = `${keyBase}-${index++}`;
    if (match[1]) {
      const target = (match[2] ?? "").trim();
      const label = (match[3] ?? target).trim();
      const exists = props.resolves ? props.resolves(target) : true;
      out.push(
        exists && props.onWikilink ? (
          <button
            key={key}
            type="button"
            onClick={() => props.onWikilink?.(target)}
            className="rounded-xs text-accent-text underline decoration-[color-mix(in_srgb,var(--accent-text)_40%,transparent)] underline-offset-2 hover:decoration-current"
          >
            {label}
          </button>
        ) : (
          <span key={key} className="text-fg-muted" title="Nota inexistente">
            {label}
          </span>
        ),
      );
    } else if (match[4]) {
      out.push(
        <code key={key} className="rounded-xs bg-surface-2 px-1 font-mono text-[0.92em] text-fg">
          {match[4].slice(1, -1)}
        </code>,
      );
    } else if (match[5]) {
      out.push(
        <strong key={key} className="font-semibold text-fg">
          {match[5].slice(2, -2)}
        </strong>,
      );
    } else if (match[6]) {
      out.push(<em key={key}>{match[6].slice(1, -1)}</em>);
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown(props: MarkdownProps) {
  const lines = props.source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").startsWith("```")) code.push(lines[i++] ?? "");
      i += 1;
      blocks.push(
        <pre key={key++} className="selectable overflow-x-auto rounded-sm bg-surface-2 p-3 font-mono text-caption text-fg-2">
          {code.join("\n")}
        </pre>,
      );
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1]!.length;
      const cls = level === 1 ? "text-[20px] font-semibold leading-7 tracking-[-0.015em]" : level === 2 ? "pt-2 text-section font-semibold" : "text-body font-semibold";
      blocks.push(
        <p key={key++} role="heading" aria-level={Math.min(level + 1, 6)} className={`text-fg ${cls}`}>
          {inline(heading[2] ?? "", props, `h${key}`)}
        </p>,
      );
      i += 1;
      continue;
    }
    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith(">")) quote.push((lines[i++] ?? "").replace(/^>\s?/, ""));
      blocks.push(
        <blockquote key={key++} className="border-l-2 border-line-strong pl-3 text-body text-fg-2">
          {inline(quote.join(" "), props, `q${key}`)}
        </blockquote>,
      );
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i] ?? "")) items.push((lines[i++] ?? "").replace(/^\s*([-*]|\d+\.)\s+/, ""));
      const List = ordered ? "ol" : "ul";
      blocks.push(
        <List key={key++} className={`flex flex-col gap-1 pl-5 text-body text-fg-2 ${ordered ? "list-decimal" : "list-disc"} marker:text-fg-muted`}>
          {items.map((item, n) => (
            <li key={n}>{inline(item, props, `l${key}-${n}`)}</li>
          ))}
        </List>,
      );
      continue;
    }
    const paragraph: string[] = [];
    while (i < lines.length && (lines[i] ?? "").trim() && !/^(#{1,4}\s|>|```|\s*([-*]|\d+\.)\s)/.test(lines[i] ?? "")) paragraph.push(lines[i++] ?? "");
    blocks.push(
      <p key={key++} className="text-body text-fg-2">
        {paragraph.map((part, n) => (
          <Fragment key={n}>
            {n > 0 ? " " : null}
            {inline(part, props, `p${key}-${n}`)}
          </Fragment>
        ))}
      </p>,
    );
  }
  return <div className="selectable flex flex-col gap-3">{blocks}</div>;
}
