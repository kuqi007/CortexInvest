const DAYS_DEFAULT = 7;
const DAYS_MIN = 1;
const DAYS_MAX = 366;

/** Safe window for ?days= — avoids NaN/negative/huge cutoffs from bad query strings. */
export function parseDaysAhead(raw: string | null): number {
  const n = parseInt(raw ?? String(DAYS_DEFAULT), 10);
  if (!Number.isFinite(n)) {
    return DAYS_DEFAULT;
  }
  return Math.min(DAYS_MAX, Math.max(DAYS_MIN, n));
}
