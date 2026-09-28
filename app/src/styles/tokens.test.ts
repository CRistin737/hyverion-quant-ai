import { readFileSync } from "node:fs";

// Vitest runs from app/; CSS imports are stubbed in tests, so read the file directly.
const css = readFileSync("src/styles/tokens.css", "utf8");

/** WCAG AA guard: every text token must reach 4.5:1 on every surface, in both themes. */

function block(selector: string): Record<string, string> {
  const start = css.indexOf(selector);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6});/gi)].map((m) => [m[1]!, m[2]!]));
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const SURFACES = ["background", "surface-1", "surface-2", "surface-3"];

describe.each([
  ["dark", block('[data-theme="dark"]'), ["text-primary", "text-secondary", "text-muted", "accent-text", "positive", "negative", "warning", "info"]],
  ["light", block('[data-theme="light"]'), ["text-primary", "text-secondary", "text-muted", "accent-text", "positive", "negative", "warning", "info"]],
])("%s theme", (_name, tokens, texts) => {
  it.each(texts)("%s meets AA on every surface", (text) => {
    for (const surface of SURFACES) {
      expect(contrast(tokens[text]!, tokens[surface]!)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("primary buttons are readable", () => {
    expect(contrast(tokens["on-accent"]!, tokens["accent"]!)).toBeGreaterThanOrEqual(4.5);
  });
});
