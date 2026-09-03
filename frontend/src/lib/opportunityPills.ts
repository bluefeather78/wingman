// Single source of truth for the meta pills shown on an opportunity card, so Fresh Finds and
// the Quest Log cannot drift. The set is deliberately narrow: cost, format, season, and — only
// when the program is in-person or hybrid — its location. The opportunity `type` (Internship,
// Summer Program, …) is intentionally NOT a pill: the card already carries a kind badge, so a
// type pill just repeated it.
//
// Field naming mirrors the catalog: the row's `location` column is the FORMAT (In-Person /
// Remote / In-Person and Remote), while the actual place lives in `state`. A remote-only
// program has no meaningful location to show, so its state pill is suppressed; in-person and
// hybrid ("In-Person and Remote") keep it.

export interface MetaPillFields {
  /** Catalog `price` — Paid / Free. */
  price?: unknown;
  /** Catalog `location` column, i.e. the FORMAT: In-Person / Remote / In-Person and Remote. */
  format?: unknown;
  /** Catalog `state` — the actual location. Shown only for in-person/hybrid programs. */
  state?: unknown;
  /** Catalog `season` — Summer / Year-Long / … */
  season?: unknown;
}

function str(v: unknown): string {
  return typeof v === 'string' ? v.trim() : '';
}

// True when the format includes an in-person component (covers "In-Person" and the hybrid
// "In-Person and Remote"). Remote-only and unknown formats return false, so the location
// pill is dropped unless we can confirm the student would actually go somewhere.
export function formatIsInPersonOrHybrid(format: unknown): boolean {
  return /in-?person/i.test(str(format));
}

export function buildMetaPills(fields: MetaPillFields): string[] {
  const format = str(fields.format);
  const state = str(fields.state);
  const showState = formatIsInPersonOrHybrid(format) && !!state && state !== 'All States';
  return [str(fields.price), format, showState ? state : '', str(fields.season)].filter(
    (x) => x.length > 0,
  );
}
