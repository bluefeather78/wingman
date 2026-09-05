import type { Opportunity } from '@/api/types';

// The finder's pure data: how a catalog row maps onto a Quest Log bucket, what the quiz asks,
// and how a filter facet reads a row.
//
// Phase 5, frontend_report §4 ("Files over 800 lines — suggested splits"), which names
// `src/lib/finderSearch.ts` for exactly this. None of it touches React, the network, or a
// model — it is the part of a 2,000-line screen that can be tested by calling it, and it was
// buried in the middle of one.
//
// It is also the part most likely to be WRONG in a way nobody notices: kindForOpp's map
// decides which bucket a tracked opportunity is filed under, and its own comment records that
// a missing entry silently filed volunteer roles as summer camps. A table like that belongs
// somewhere a test can enumerate it.

// kindForOpp is NOT here. Moving it out of finder.tsx surfaced that the identical function
// already existed in src/lib/tracker.ts — a verbatim copy, whose own comment says it was
// "ported from finder.tsx so both Fresh Finds and the Quest Log's catalog search resolve a
// bucket the same way". Two copies of a table that decides which bucket a tracked
// opportunity is filed under is precisely the drift this file is meant to remove, so the
// screen now imports the one in tracker.ts (frontend_report finding 14, "duplicate helpers").

// Quiz: root → sub-branch → kind (from script.js QUIZ_BRANCHES + the live quiz screen).
export const QUIZ_ROOT = [
  { label: 'I already have a research paper or project', desc: 'In progress or already completed', branch: 'project' },
  { label: "I'm looking for something to do when school is out", desc: 'Camps, programs, or work experience', branch: 'timeoff' },
  { label: 'I enjoy competing directly with my peers', desc: 'Tests, exams, head-to-head challenges', kind: 'pure-competition' },
] as const;
export const QUIZ_SUB: Record<string, { label: string; desc: string; kind: string }[]> = {
  project: [
    { label: 'Enter it in a competition', desc: 'Science fairs, app challenges, project contests', kind: 'research-competition' },
    { label: 'Present it at a conference', desc: 'Submit a paper to a workshop or conference', kind: 'conference' },
    { label: 'Get it published', desc: 'Submit to an academic or student journal', kind: 'journal' },
  ],
  timeoff: [
    { label: 'Hands-on work experience', desc: 'Work with a lab, company, or organization', kind: 'internship' },
    { label: 'A summer program', desc: 'Camps, pre-college programs, academies', kind: 'summer' },
    { label: 'Volunteering or service', desc: 'Give time to a cause or community organization', kind: 'volunteer' },
  ],
};

// How many of the recall pool's best rows get a "why it fits" reason. Matches rankCandidates'
// own 10-12 cap; these are the rows above the fold that the student actually reads.
export const REASON_TOP_N = 12;

export const FILTER_FIELDS = [
  { key: 'type', label: 'Type' },
  { key: 'price', label: 'Cost' },
  { key: 'season', label: 'Season' },
  { key: 'location', label: 'Format' },
] as const;
export type FilterKey = (typeof FILTER_FIELDS)[number]['key'];

// Plenty of catalog rows carry no cost, season or format. The facet list was built from
// non-empty values only, so those rows could not satisfy ANY checked option and silently
// disappeared the moment a student touched a filter. They now get an explicit option they
// can see and choose, rather than being quietly excluded.
export const BLANK_FACET = '__unspecified__';
export const BLANK_FACET_LABEL = 'Not specified';
export function facetValue(opp: Opportunity, key: FilterKey): string {
  const v = opp[key];
  return typeof v === 'string' && v.trim() ? v : BLANK_FACET;
}
