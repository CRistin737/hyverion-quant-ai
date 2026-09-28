import { useMemo, useState } from "react";

import { cn } from "./cn";

export interface GraphNode {
  id: string;
  label: string;
  kind: "knowledge" | "index" | "map" | string;
}

export interface GraphEdge {
  source: string;
  target: string;
}

interface Positioned extends GraphNode {
  x: number;
  y: number;
}

/** Deterministic force layout (no randomness, fixed iterations) for small vault graphs. */
function layout(nodes: GraphNode[], edges: GraphEdge[], width: number, height: number): Positioned[] {
  const count = Math.max(nodes.length, 1);
  const points = nodes.map((node, i) => {
    const angle = (i / count) * Math.PI * 2;
    const radius = Math.min(width, height) * (node.kind === "map" ? 0.05 : node.kind === "index" ? 0.22 : 0.38);
    return { ...node, x: width / 2 + Math.cos(angle) * radius, y: height / 2 + Math.sin(angle) * radius, vx: 0, vy: 0 };
  });
  const index = new Map(points.map((p, i) => [p.id, i]));
  const ideal = Math.sqrt((width * height) / count) * 0.55;
  for (let step = 0; step < 220; step += 1) {
    for (let a = 0; a < points.length; a += 1) {
      for (let b = a + 1; b < points.length; b += 1) {
        const p = points[a]!;
        const q = points[b]!;
        const dx = p.x - q.x || 0.01;
        const dy = p.y - q.y || 0.01;
        const dist2 = dx * dx + dy * dy;
        const force = (ideal * ideal) / Math.max(dist2, 1) * 0.6;
        p.vx += dx * force * 0.01;
        p.vy += dy * force * 0.01;
        q.vx -= dx * force * 0.01;
        q.vy -= dy * force * 0.01;
      }
    }
    for (const edge of edges) {
      const p = points[index.get(edge.source) ?? -1];
      const q = points[index.get(edge.target) ?? -1];
      if (!p || !q) continue;
      const dx = q.x - p.x;
      const dy = q.y - p.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const pull = (dist - ideal) / dist * 0.04;
      p.vx += dx * pull;
      p.vy += dy * pull;
      q.vx -= dx * pull;
      q.vy -= dy * pull;
    }
    const cooling = 1 - step / 240;
    for (const p of points) {
      p.vx += (width / 2 - p.x) * 0.002;
      p.vy += (height / 2 - p.y) * 0.002;
      p.x = Math.min(width - 16, Math.max(16, p.x + p.vx * cooling));
      p.y = Math.min(height - 16, Math.max(16, p.y + p.vy * cooling));
      p.vx *= 0.6;
      p.vy *= 0.6;
    }
  }
  return points.map(({ vx: _vx, vy: _vy, ...rest }) => rest);
}

/** Knowledge graph: notes as nodes, wikilinks as edges. Click a node to open it. */
export function KnowledgeGraph({
  nodes,
  edges,
  selected,
  onSelect,
  height = 420,
  className,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected?: string | null;
  onSelect: (id: string) => void;
  height?: number;
  className?: string;
}) {
  const width = 800;
  const [hover, setHover] = useState<string | null>(null);
  const placed = useMemo(() => layout(nodes, edges, width, height), [nodes, edges, height]);
  const byId = useMemo(() => new Map(placed.map((p) => [p.id, p])), [placed]);
  const focus = hover ?? selected ?? null;
  const neighbours = useMemo(() => {
    const set = new Set<string>();
    if (!focus) return set;
    for (const edge of edges) {
      if (edge.source === focus) set.add(edge.target);
      if (edge.target === focus) set.add(edge.source);
    }
    return set;
  }, [edges, focus]);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className={cn("w-full", className)} role="img" aria-label="Grafo de conocimiento">
      {edges.map((edge) => {
        const a = byId.get(edge.source);
        const b = byId.get(edge.target);
        if (!a || !b) return null;
        const active = focus !== null && (edge.source === focus || edge.target === focus);
        return (
          <line
            key={`${edge.source}->${edge.target}`}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke={active ? "var(--accent-text)" : "var(--border-strong)"}
            strokeOpacity={focus && !active ? 0.35 : 0.9}
            strokeWidth={active ? 1.5 : 1}
          />
        );
      })}
      {placed.map((node) => {
        const isSelected = node.id === selected;
        const dimmed = focus !== null && node.id !== focus && !neighbours.has(node.id);
        const r = node.kind === "map" ? 7 : node.kind === "index" ? 6 : 5;
        return (
          <g
            key={node.id}
            role="button"
            tabIndex={0}
            aria-label={node.label}
            onClick={() => onSelect(node.id)}
            onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && onSelect(node.id)}
            onMouseEnter={() => setHover(node.id)}
            onMouseLeave={() => setHover(null)}
            className="cursor-default outline-none"
            opacity={dimmed ? 0.35 : 1}
          >
            <circle
              cx={node.x}
              cy={node.y}
              r={r + (isSelected ? 2 : 0)}
              fill={isSelected ? "var(--accent)" : node.kind === "knowledge" ? "var(--text-secondary)" : "var(--surface-3)"}
              stroke={node.kind === "knowledge" ? "none" : "var(--border-strong)"}
            />
            <text x={node.x + r + 5} y={node.y + 4} fontSize="11" fill={isSelected || hover === node.id ? "var(--text-primary)" : "var(--text-muted)"} fontFamily="Geist Variable, sans-serif">
              {node.label.length > 34 ? `${node.label.slice(0, 33)}…` : node.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
