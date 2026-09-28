type ClassValue = string | false | null | undefined | 0;

/** Join class names, dropping falsy values. */
export function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(" ");
}
