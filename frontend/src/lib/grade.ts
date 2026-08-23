import type { Opportunity } from '@/api/types';

// Grade-level parsing + eligibility, ported from script.js.
// Maps a grade mention (dropdown label or free text) to a single US grade number
// (6-12), the same scale as the DB's grade_min/grade_max columns.

const GRADE_WORD_TO_NUM: Record<string, number> = {
  freshman: 9,
  sophomore: 10,
  junior: 11,
  senior: 12,
};

export function parseGradeFromText(text: string | null | undefined): number | null {
  if (!text) return null;
  const lower = text.toLowerCase();
  // "9th grade", "grade 9", "9th-grade", etc.
  let m =
    lower.match(/\b(6|7|8|9|10|11|12)(?:st|nd|rd|th)?\s*[- ]?\s*grade\b/) ||
    lower.match(/\bgrade\s*[- ]?\s*(6|7|8|9|10|11|12)\b/);
  if (m) return parseInt(m[1], 10);
  // "freshman"/"sophomore"/"junior"/"senior", optionally "rising".
  m = lower.match(/\b(?:rising\s+)?(freshman|sophomore|junior|senior)\b/);
  if (m) return GRADE_WORD_TO_NUM[m[1]];
  if (/\bmiddle school\b/.test(lower)) return 8;
  return null;
}

// Explicit dropdown values share the same phrasing as free text.
export function parseGradeLevel(label: string | null | undefined): number | null {
  return parseGradeFromText(label);
}

// True if the opportunity's grade_min/grade_max range (if set) includes studentGrade.
// Rows with no bounds are eligible for everyone; unknown student grade filters nothing.
export function isGradeEligible(opp: Opportunity, studentGrade: number | null): boolean {
  if (studentGrade == null) return true;
  if (opp.grade_min == null && opp.grade_max == null) return true;
  if (opp.grade_min != null && studentGrade < opp.grade_min) return false;
  if (opp.grade_max != null && studentGrade > opp.grade_max) return false;
  return true;
}
