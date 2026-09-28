import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import { cn } from "./cn";

/**
 * Visual building blocks for the Inicio dashboard. Geometry and animation use
 * floats; displayed amounts always come from exact Decimal-string formatters.
 * Every animation is skipped under prefers-reduced-motion.
 */

function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/** Count-up for a headline number. Shows `final` (exact text) once settled. */
export function AnimatedNumber({ value, format, final, className }: { value: number; format: (n: number) => string; final: string; className?: string }) {
  const [shown, setShown] = useState<string>(final);
  const previous = useRef<number | null>(null);
  useEffect(() => {
    const from = previous.current ?? value * 0.94;
    previous.current = value;
    if (reducedMotion() || !Number.isFinite(value) || from === value) {
      setShown(final);
      return;
    }
    const start = performance.now();
    const duration = 900;
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - (1 - t) ** 4;
      setShown(t < 1 ? format(from + (value - from) * eased) : final);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, final, format]);
  return <span className={cn("num", className)}>{shown}</span>;
}

export interface SeriesPoint {
  label: string;
  value: number;
}

/** Equity area chart: animated line draw, soft monochrome fill, hover crosshair. */
export function EquityArea({ points, height = 220, format, className }: { points: SeriesPoint[]; height?: number; format: (n: number) => string; className?: string }) {
  const id = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);
  const width = 1000;
  const pad = { top: 16, bottom: 22 };
  const geometry = useMemo(() => {
    if (points.length < 2) return null;
    const values = points.map((p) => p.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || Math.max(1, Math.abs(max) * 0.01);
    const x = (i: number) => (i / (points.length - 1)) * width;
    const y = (v: number) => pad.top + (1 - (v - min + span * 0.08) / (span * 1.16)) * (height - pad.top - pad.bottom);
    const coords = points.map((p, i) => [x(i), y(p.value)] as const);
    const line = coords.map(([cx, cy], i) => `${i ? "L" : "M"}${cx.toFixed(1)},${cy.toFixed(1)}`).join(" ");
    const area = `${line} L${width},${height - pad.bottom} L0,${height - pad.bottom} Z`;
    return { coords, line, area, min, max };
  }, [points, height, pad.top, pad.bottom]);

  if (!geometry) return null;
  const active = hover ?? points.length - 1;
  const [ax, ay] = geometry.coords[active]!;
  const trendUp = points[points.length - 1]!.value >= points[0]!.value;

  return (
    <div className={cn("relative", className)}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="block w-full overflow-visible"
        style={{ height }}
        role="img"
        aria-label="Curva de patrimonio"
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const ratio = (event.clientX - rect.left) / rect.width;
          setHover(Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1)))));
        }}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={`fill-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--text-primary)" stopOpacity="0.16" />
            <stop offset="1" stopColor="var(--text-primary)" stopOpacity="0" />
          </linearGradient>
          <pattern id={`grid-${id}`} width="50" height="36" patternUnits="userSpaceOnUse">
            <path d="M50 0H0V36" fill="none" stroke="var(--border-subtle)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          </pattern>
        </defs>
        <rect width={width} height={height - pad.bottom} fill={`url(#grid-${id})`} opacity="0.7" />
        <path d={geometry.area} fill={`url(#fill-${id})`} className="viz-fade" />
        <path d={geometry.line} fill="none" stroke="var(--text-primary)" strokeWidth="1.75" vectorEffect="non-scaling-stroke" className="viz-reveal" />
        <line x1={ax} x2={ax} y1={pad.top} y2={height - pad.bottom} stroke="var(--border-strong)" strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
      </svg>
      <span
        aria-hidden
        className="viz-pulse pointer-events-none absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fg shadow-[0_0_0_4px_var(--background)]"
        style={{ left: `${(ax / width) * 100}%`, top: ay }}
      />
      <div
        className="pointer-events-none absolute top-0 flex -translate-x-1/2 flex-col items-center rounded-sm bg-surface-floating px-2 py-1 shadow-float"
        style={{ left: `clamp(56px, ${(ax / width) * 100}%, calc(100% - 56px))` }}
      >
        <span className="num text-[12px] font-medium text-fg">{format(points[active]!.value)}</span>
        <span className="text-[10.5px] text-fg-muted">{points[active]!.label}</span>
      </div>
      <div className="flex justify-between pt-1 text-[10.5px] text-fg-muted">
        <span>{points[0]!.label}</span>
        <span className={trendUp ? "text-positive" : "text-negative"}>{trendUp ? "Tendencia al alza" : "Tendencia a la baja"}</span>
        <span>{points[points.length - 1]!.label}</span>
      </div>
    </div>
  );
}

/** Radial gauge with animated sweep; tone escalates with usage. */
export function RadialGauge({ ratio, label, value, detail, size = 104 }: { ratio: number | null; label: string; value: ReactNode; detail: ReactNode; size?: number }) {
  const clamped = ratio === null ? 0 : Math.max(0, Math.min(1, ratio));
  const r = 42;
  const circumference = 2 * Math.PI * r;
  const arc = circumference * 0.75;
  const tone = clamped >= 0.8 ? "var(--negative)" : clamped >= 0.5 ? "var(--warning)" : "var(--text-primary)";
  return (
    <figure className="flex flex-col items-center gap-2 text-center" aria-label={label}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg viewBox="0 0 100 100" className="size-full -rotate-[225deg]" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(clamped * 100)} aria-label={label}>
          <circle cx="50" cy="50" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="7" strokeLinecap="round" strokeDasharray={`${arc} ${circumference}`} />
          {clamped > 0.005 ? <circle
            cx="50"
            cy="50"
            r={r}
            fill="none"
            stroke={tone}
            strokeWidth="7"
            strokeLinecap="round"
            strokeDasharray={`${arc * clamped} ${circumference}`}
            className="viz-sweep"
            style={{ ["--arc" as string]: `${arc * clamped}` }}
          /> : null}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="num text-[18px] font-semibold leading-6 text-fg">{value}</span>
        </div>
      </div>
      <figcaption className="flex flex-col">
        <span className="text-label font-medium text-fg-2">{label}</span>
        <span className="text-caption text-fg-muted">{detail}</span>
      </figcaption>
    </figure>
  );
}

export type StageState = "done" | "active" | "blocked" | "idle";

/** The decision pipeline as a live flow: DATA → AGENTS → PROPOSAL → CRITIC → RISK → EXECUTION. */
export function PipelineFlow({ stages }: { stages: Array<{ key: string; label: string; state: StageState; detail: string }> }) {
  return (
    <ol className="grid grid-cols-6 gap-0 max-lg:grid-cols-3 max-lg:gap-y-4" aria-label="Flujo de decisión">
      {stages.map((stage, index) => {
        const last = index === stages.length - 1;
        const tone =
          stage.state === "done" ? "border-fg bg-fg text-bg" : stage.state === "active" ? "border-fg text-fg" : stage.state === "blocked" ? "border-negative text-negative" : "border-line-strong text-fg-muted";
        const flowing = stage.state === "done" && stages[index + 1]?.state !== "idle";
        return (
          <li key={stage.key} className="relative flex flex-col items-center gap-2 text-center">
            {!last ? (
              <span aria-hidden className="absolute left-1/2 top-[13px] h-px w-full overflow-hidden bg-line-strong max-lg:hidden">
                {flowing ? <span className="viz-flow absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-[var(--text-primary)] to-transparent" /> : null}
              </span>
            ) : null}
            <span className={cn("relative z-[1] flex size-[27px] items-center justify-center rounded-full border text-[11px] font-semibold", tone, stage.state === "active" && "viz-ring")}>
              {stage.state === "blocked" ? "×" : index + 1}
            </span>
            <span className="text-[12px] font-medium text-fg">{stage.label}</span>
            <span className="max-w-[16ch] text-[11px] leading-4 text-fg-muted">{stage.detail}</span>
          </li>
        );
      })}
    </ol>
  );
}
