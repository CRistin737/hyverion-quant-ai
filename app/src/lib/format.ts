/**
 * Central number/date formatters (DESIGN_SPEC §56).
 *
 * Money, prices and quantities arrive as Decimal strings. Rounding and grouping
 * are done on the digit string itself, so no binary-float error can ever change
 * a displayed amount. Grouping uses a comma and a decimal point (10,482.17),
 * like the US dollar amounts the broker reports. The minus sign is U+2212.
 */

import type { Dec } from "@/api/types";

const MINUS = "−";
const DASH = "—";

interface Parsed {
  negative: boolean;
  int: string;
  frac: string;
}

/** Parse a decimal string (incl. exponent forms like "0E-12") without floats. */
export function parseDec(value: Dec | number | null | undefined): Parsed | null {
  if (value === null || value === undefined) return null;
  let raw = typeof value === "number" ? String(value) : value.trim();
  if (raw === "" || raw === "NaN" || raw === "Infinity" || raw === "-Infinity") return null;
  let negative = false;
  if (raw.startsWith("-") || raw.startsWith(MINUS)) {
    negative = true;
    raw = raw.slice(1);
  } else if (raw.startsWith("+")) {
    raw = raw.slice(1);
  }
  const match = /^(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(raw);
  if (!match) return null;
  let int = match[1] ?? "";
  let frac = match[2] ?? "";
  if (int === "" && frac === "") return null;
  const exp = match[3] ? parseInt(match[3], 10) : 0;
  if (exp > 0) {
    frac = frac.padEnd(exp, "0");
    int = int + frac.slice(0, exp);
    frac = frac.slice(exp);
  } else if (exp < 0) {
    const shift = -exp;
    int = int.padStart(shift + 1, "0");
    frac = int.slice(int.length - shift) + frac;
    int = int.slice(0, int.length - shift);
  }
  int = int.replace(/^0+(?=\d)/, "") || "0";
  const isZero = /^0*$/.test(int) && /^0*$/.test(frac);
  return { negative: negative && !isZero, int, frac };
}

/** -1, 0 or 1 without floating point. Null input is treated as zero. */
export function sign(value: Dec | number | null | undefined): -1 | 0 | 1 {
  const parsed = parseDec(value);
  if (!parsed) return 0;
  if (/^0*$/.test(parsed.int) && /^0*$/.test(parsed.frac)) return 0;
  return parsed.negative ? -1 : 1;
}

/** Round half away from zero to `dp` decimals, returned as an unsigned digit pair. */
function roundDigits(parsed: Parsed, dp: number): { int: string; frac: string } {
  const frac = parsed.frac.padEnd(dp + 1, "0");
  const kept = parsed.int + frac.slice(0, dp);
  const roundUp = (frac.charCodeAt(dp) - 48) >= 5;
  let digits = kept;
  if (roundUp) {
    const arr = kept.split("");
    let i = arr.length - 1;
    while (i >= 0) {
      if (arr[i] === "9") {
        arr[i] = "0";
        i -= 1;
      } else {
        arr[i] = String.fromCharCode((arr[i] ?? "0").charCodeAt(0) + 1);
        break;
      }
    }
    digits = (i < 0 ? "1" : "") + arr.join("");
  }
  const intLen = digits.length - dp;
  return {
    int: digits.slice(0, intLen).replace(/^0+(?=\d)/, "") || "0",
    frac: digits.slice(intLen),
  };
}

function group(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

interface NumberOptions {
  dp?: number;
  signed?: boolean;
  trim?: boolean;
  prefix?: string;
  suffix?: string;
}

/** Generic decimal formatter used by money/qty/pct/price. */
export function decimal(value: Dec | number | null | undefined, options: NumberOptions = {}): string {
  const parsed = parseDec(value);
  if (!parsed) return DASH;
  const { dp = 2, signed = false, trim = false, prefix = "", suffix = "" } = options;
  const rounded = roundDigits(parsed, dp);
  let frac = rounded.frac;
  if (trim) frac = frac.replace(/0+$/, "");
  const isZero = /^0*$/.test(rounded.int) && /^0*$/.test(rounded.frac);
  const negative = parsed.negative && !isZero;
  const body = group(rounded.int) + (frac ? `.${frac}` : "");
  const signText = negative ? MINUS : signed && !isZero ? "+" : "";
  return `${signText}${prefix}${body}${suffix}`;
}

/** $10,482.17 · +$82.17 · −$12.08 */
export function money(value: Dec | number | null | undefined, options: { signed?: boolean; dp?: number } = {}): string {
  return decimal(value, { dp: options.dp ?? 2, signed: options.signed, prefix: "$" });
}

/** Quantity with up to `maxDp` decimals, trailing zeros removed. */
export function qty(value: Dec | number | null | undefined, maxDp = 6): string {
  return decimal(value, { dp: maxDp, trim: true });
}

/** Price with precision adapted to magnitude. */
export function price(value: Dec | number | null | undefined): string {
  const parsed = parseDec(value);
  if (!parsed) return DASH;
  const magnitude = parsed.int.replace(/^0+/, "").length;
  const dp = magnitude >= 4 ? 2 : magnitude >= 1 ? 4 : 6;
  return decimal(value, { dp, trim: magnitude < 4 });
}

/** Percent value already expressed in percent units ("1.8" → "1.8%"). */
export function pct(value: Dec | number | null | undefined, options: { signed?: boolean; dp?: number } = {}): string {
  return decimal(value, { dp: options.dp ?? 2, signed: options.signed, suffix: "%", trim: false });
}

/**
 * Display-only ratio a/b as a percent number. Uses Number because the result is
 * never persisted or used for decisions — it only sizes a label or a bar.
 */
export function displayRatio(a: Dec | null | undefined, b: Dec | null | undefined): number | null {
  const x = Number(a);
  const y = Number(b);
  if (!Number.isFinite(x) || !Number.isFinite(y) || y === 0) return null;
  return (x / y) * 100;
}

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** Backend timestamps may be naive (SQLite); they are UTC by contract. */
export function parseUTC(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** 25 sep 14:02:11 UTC */
export function dateUTC(iso: string | null | undefined, options: { seconds?: boolean; zone?: boolean } = {}): string {
  const date = parseUTC(iso);
  if (!date) return DASH;
  const { seconds = true, zone = true } = options;
  const time = `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}${seconds ? `:${pad(date.getUTCSeconds())}` : ""}`;
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${time}${zone ? " UTC" : ""}`;
}

export function timeUTC(iso: string | null | undefined): string {
  const date = parseUTC(iso);
  if (!date) return DASH;
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
}

/** "hace 3 min" — relative time for captions (absolute time goes in a tooltip). */
/** Time in New York (the US market's clock), e.g. "lun 09:30" or "16:00". */
export function timeNewYork(iso: string | null | undefined, options: { weekday?: boolean } = {}): string {
  const date = parseUTC(iso);
  if (!date) return "—";
  return new Intl.DateTimeFormat("es", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...(options.weekday ? { weekday: "short" } : {}),
  }).format(date);
}

export function relative(iso: string | null | undefined, now: Date = new Date()): string {
  const date = parseUTC(iso);
  if (!date) return DASH;
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000);
  if (seconds < 0) return "ahora";
  if (seconds < 45) return "hace unos segundos";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.round(hours / 24);
  if (days < 30) return `hace ${days} ${days === 1 ? "día" : "días"}`;
  return dateUTC(iso, { seconds: false });
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} d`;
}

export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return DASH;
  return id.length > length ? id.slice(0, length) : id;
}

const SCALE = 18;

function toScaled(value: Dec | null | undefined): bigint {
  const parsed = parseDec(value);
  if (!parsed) return 0n;
  const frac = parsed.frac.padEnd(SCALE, "0").slice(0, SCALE);
  const magnitude = BigInt(parsed.int + frac);
  return parsed.negative ? -magnitude : magnitude;
}

function fromScaled(value: bigint): Dec {
  const negative = value < 0n;
  const digits = (negative ? -value : value).toString().padStart(SCALE + 1, "0");
  const int = digits.slice(0, digits.length - SCALE);
  const frac = digits.slice(digits.length - SCALE).replace(/0+$/, "");
  return `${negative ? "-" : ""}${int}${frac ? `.${frac}` : ""}`;
}

/** Exact decimal addition on strings (18 fractional digits), no binary floats. */
export function addDec(...values: Array<Dec | null | undefined>): Dec {
  return fromScaled(values.reduce<bigint>((sum, value) => sum + toScaled(value), 0n));
}

/** Exact decimal subtraction a − b. */
export function subDec(a: Dec | null | undefined, b: Dec | null | undefined): Dec {
  return fromScaled(toScaled(a) - toScaled(b));
}
