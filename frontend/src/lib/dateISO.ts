// Are the dates stored on a tracked opportunity actually dates? (Phase 5, finding 11.)
//
// ITS OWN MODULE, and that is not tidiness. These live where BOTH readers can import them
// without importing each other: src/lib/status.ts needs them to classify an item, and
// src/api/trackerStore.ts needs them to refuse a bad date on the way IN — and status.ts
// already imports trackerStore for isSetAsideTask, so putting them in status.ts made
// trackerStore -> status -> trackerStore a require cycle. Metro allows cycles and warns that
// they "can result in uninitialized values"; both uses here happen inside functions so the
// cycle was benign today, but a benign cycle is one edit away from a module-init `undefined`
// that fails at runtime and nowhere else. A leaf module with no imports of its own cannot
// participate in one at all.
//
// Everything below reads a date out of stored tracker data, and until now the only check was
// TRUTHINESS. A stored `date_iso` of "TBD", "2026-13-45", "Fall 2026" or "2026/11/01" — all of
// which a model can produce and a student can paste — made `new Date(...)` an Invalid Date, so
// daysUntil returned NaN. Every comparison against NaN is false, so:
//
//   computeProgressStatus  daysUntil(first) > 0 → false, daysUntil(last) < 0 → false
//                          → falls through to 'in_progress', and the card reads HAPPENING NOW
//                          for a programme whose date is a typo.
//   getDisplayMilestones   isPast = NaN < 0 = false → a past date renders as upcoming.
//   the calendar            renders the literal string NaN.
//
// One validator, applied at the ONE place each reader extracts a date, so a malformed entry is
// simply not a date rather than being a date that lies. That is the safe direction: an item
// with no usable dates reads as "not started" and shows no milestone, which is honest, where
// "Happening Now" is not.

// A calendar date, not a timestamp: `YYYY-MM-DD`, and a real day in a real month. The regex
// alone is not enough — "2026-02-31" passes it — so the parsed date is checked to round-trip,
// which is what rejects a day that does not exist in that month.
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function isValidDateISO(value: unknown): value is string {
  if (typeof value !== 'string' || !ISO_DATE_RE.test(value)) return false;
  const [y, m, d] = value.split('-').map(Number);
  if (m < 1 || m > 12 || d < 1) return false;
  // Date.UTC(y, m, 0) is the last day of month `m` (months are 0-based, so `m` is the month
  // after this one and day 0 steps back one).
  return d <= new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/**
 * The stored date on an importantDates entry, in either spelling, or null if it is not a
 * usable calendar date. Both spellings exist in stored data: the client writes `dateISO`, the
 * deadline endpoint speaks `date_iso`, and rows written by either are still on disk.
 */
export function storedDateISO(entry: unknown): string | null {
  const d = entry as { dateISO?: unknown; date_iso?: unknown } | null;
  const raw = d?.dateISO ?? d?.date_iso;
  return isValidDateISO(raw) ? raw : null;
}

/**
 * Whole days from today until `dateISO`, or null if that is not a usable date.
 *
 * The null return is the point: callers used to get NaN and compare it, which is always false
 * and therefore always the wrong branch, silently. Now a caller has to decide what an unknown
 * date means, and every one of them below decides "treat the item as having no such date".
 */
