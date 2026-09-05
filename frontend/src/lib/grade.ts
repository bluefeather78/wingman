import type { Opportunity } from '@/api/types';

// Grade-level parsing + eligibility, ported from script.js.
// Maps a grade mention (dropdown label or free text) to a single US grade number
// (6-12), the same scale as the DB's grade_min/grade_max columns.
//
// TWO ENTRY POINTS, AND THE DIFFERENCE IS LOAD-BEARING (Phase 5, frontend_report finding 8).
//
//   parseGradeLevel(label)   an EXPLICIT value the student chose or typed into a grade field —
//                            a dropdown option, nothing else. Permissive.
//   parseGradeFromText(text) PROSE that may happen to mention a grade — the synthesized
//                            profile, a chat transcript. Requires grade CONTEXT.
//
// They were one permissive function, and it matched bare `junior`, `senior` and `middle
// school` anywhere in a paragraph. So a profile saying "I play junior varsity soccer", "I
// volunteer with senior citizens" or "I tutor middle school kids" set a HARD grade filter:
// preFilter drops every row whose grade_min/grade_max excludes it, and the same number is
// posted to /api/match, where the server drops verified-ineligible rows too. A student who
// tutors younger kids was being shown middle-school programmes and denied high-school ones,
// with nothing on screen explaining why.
//
// The fix is context, not a blocklist of nouns. "junior varsity", "senior citizens", "senior
// developer", "junior year abroad", "middle school kids" — the list is open-ended, and a
// blocklist fails silently on the first phrase nobody thought of. A frame requirement fails
// the other way: an unfamiliar phrasing means "no grade found", which filters nothing.

const GRADE_WORD_TO_NUM: Record<string, number> = {
  freshman: 9,
  sophomore: 10,
  junior: 11,
  senior: 12,
};

const WORD = '(freshman|freshmen|sophomore|sophomores|junior|juniors|senior|seniors)';

// Numeric forms. Unambiguous in prose as well as in a label — "11th grade" and "grade 11" are
// not adjectives modifying something else — so both entry points accept them.
const NUMERIC = [
  /\b(6|7|8|9|10|11|12)(?:st|nd|rd|th)?\s*[- ]?\s*grade\b/,
  /\bgrade\s*[- ]?\s*(6|7|8|9|10|11|12)\b/,
];

// The frames that make a grade WORD a grade rather than an adjective. Each one is a phrasing a
// student uses about themselves; none of them fits "junior varsity" or "senior citizens".
const WORD_FRAMES = [
  // "rising junior", "incoming senior", "current sophomore", "entering freshman"
  new RegExp(`\\b(?:rising|incoming|current|entering|going into)\\s+${WORD}\\b`, 'i'),
  // "I'm a junior", "I am a senior", "im a sophomore"
  new RegExp(`\\bi\\s*(?:'|’)?\\s*a?m\\s+(?:an?\\s+)?${WORD}\\b`, 'i'),
  // "a junior in high school", "a senior at Lincoln High"
  new RegExp(`\\b(?:an?)\\s+${WORD}\\s+(?:in|at)\\b`, 'i'),
  // "junior year", "senior year"
  new RegExp(`\\b${WORD}\\s+year\\b`, 'i'),
  // "high school junior", "hs senior"
  new RegExp(`\\b(?:high\\s*school|hs)\\s+${WORD}\\b`, 'i'),
  // "grade: junior" — a labelled field pasted into prose
  new RegExp(`\\bgrade\\s*[:-]\\s*${WORD}\\b`, 'i'),
];

// "middle school" is the same problem one level down: "I tutor middle school kids" is about
// somebody else's grade. These frames are about the writer.
const MIDDLE_FRAMES = [
  /\bi\s*(?:'|’)?\s*a?m\s+(?:in\s+)?(?:a\s+)?middle[\s-]*school(?:er)?\b/i,
  /\bi\s*(?:'|’)?\s*a?m\s+a\s+middle[\s-]*schooler\b/i,
  /\b(?:rising|incoming|current|entering|going into)\s+middle[\s-]*school(?:er)?\b/i,
  /\bmiddle[\s-]*school\s+student\b/i,
];

function numericGrade(lower: string): number | null {
  for (const re of NUMERIC) {
    const m = lower.match(re);
    if (m) return parseInt(m[1], 10);
  }
  return null;
}

// "juniors" and "freshmen" both land on the singular key.
function wordToNum(word: string): number | null {
  const key = word.toLowerCase().replace(/s$/, '').replace(/freshmen$/, 'freshman');
  return GRADE_WORD_TO_NUM[key === 'freshme' ? 'freshman' : key] ?? null;
}

/**
 * A grade from an EXPLICIT value: a dropdown option or a grade input field.
 *
 * Permissive on purpose — the whole string is about the grade, so there is no surrounding
 * sentence for a word to be an adjective in. This is what the finder's grade pickers use
 * ('Middle School', '9th grade', … ) and what a student typing "senior" into a grade box means.
 */
export function parseGradeLevel(label: string | null | undefined): number | null {
  if (!label) return null;
  const lower = label.toLowerCase().trim();
  const numeric = numericGrade(lower);
  if (numeric != null) return numeric;
  const m = lower.match(new RegExp(`^(?:rising\\s+)?${WORD}$`, 'i'));
  if (m) return wordToNum(m[1]);
  if (/^middle[\s-]*school(?:er)?$/.test(lower)) return 8;
  return null;
}

/**
 * A grade from PROSE — a synthesized profile, a chat transcript, anything a student wrote.
 *
 * Returns null unless the text actually says what grade the STUDENT is in. That is stricter
 * than the old behaviour by design: this number becomes a hard filter on both the client
 * (preFilter) and the server (/api/match), so a false positive silently removes real matches,
 * while a false negative removes nothing at all.
 */
export function parseGradeFromText(text: string | null | undefined): number | null {
  if (!text) return null;
  const lower = text.toLowerCase();
  // A short value that IS a grade — "11th grade", "Senior" — reaching this function rather
  // than parseGradeLevel. Kept so an existing caller passing a dropdown value still works.
  const asLabel = parseGradeLevel(lower);
  if (asLabel != null) return asLabel;

  const numeric = numericGrade(lower);
  if (numeric != null) return numeric;

  for (const re of WORD_FRAMES) {
    const m = lower.match(re);
    if (m) {
      const num = wordToNum(m[1]);
      if (num != null) return num;
    }
  }
  for (const re of MIDDLE_FRAMES) {
    if (re.test(lower)) return 8;
  }
  return null;
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
