// Shared constants ported from script.js.

// The bar the app gates a "meaningful profile" on. CLAUDE.md warns this MUST stay
// identical to the server's copy (used by the metrics console's meaningful_profile
// funnel stage) — change it in both places or in neither.
export const PROFILE_SUFFICIENT_LENGTH = 20;

// The six tracker buckets, in display order.
export const ALL_BUCKETS = [
  'summerPrograms',
  'internships',
  'researchCompetitions',
  'pureCompetitions',
  'conferences',
  'journals',
] as const;

export type Bucket = (typeof ALL_BUCKETS)[number];

// VALID_SUBJECTS (the fixed 17-subject vocabulary) was RETIRED in Phase 6 — semantic recall
// via per-theme embeddings replaced it. See OPPORTUNITY_MATCHING_PLAN.md Phase 5/6.
