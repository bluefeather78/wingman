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

// Subject vocabulary used by inferSubjects / ranking (ported from VALID_SUBJECTS).
export const VALID_SUBJECTS = [
  'Mixed', 'STEM', 'Medicine', 'Humanities', 'Art', 'Business', 'Engineering',
  'Computer Science', 'Mathematics', 'Biology', 'Physics', 'Astronomy', 'Chemistry',
  'Leadership', 'Law', 'Logic', 'Education',
] as const;
