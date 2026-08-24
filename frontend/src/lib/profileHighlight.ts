// Sentence-level diff of a profile before/after a merge — ported verbatim from script.js
// (flagNewProfileText and friends). Returns the set of sentence keys that are genuinely
// NEW, so the UI can highlight them for PROFILE_HIGHLIGHT_MS and scroll to the first one.

export const PROFILE_HIGHLIGHT_MS = 5000;
const PROFILE_REWORD_RATIO = 0.6;
const PROFILE_CONTAINMENT_RATIO = 0.8;
const PROFILE_MIN_NOVEL_WORD_RATIO = 0.25;
const PROFILE_MIN_HIGHLIGHT_WORDS = 4;
const PROJECT_PREFIX_RE = /^(passion|research) projects?:\s*/i;

export function splitProfileSentences(text: string): string[] {
  return (text || '').split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
}

export function profileSentenceKey(text: string): string {
  return (text || '').replace(PROJECT_PREFIX_RE, '').replace(/\s+/g, ' ').trim();
}

function profileSentenceKeys(text: string): string[] {
  return (text || '')
    .split(/\n\s*\n/)
    .flatMap((par) => splitProfileSentences(par.replace(PROJECT_PREFIX_RE, '')))
    .map(profileSentenceKey)
    .filter(Boolean);
}

function sentenceWords(text: string): Set<string> {
  return new Set(text.toLowerCase().match(/[a-z0-9']+/g) || []);
}

function sharedWordCount(A: Set<string>, B: Set<string>): number {
  let shared = 0;
  A.forEach((w) => {
    if (B.has(w)) shared++;
  });
  return shared;
}

function sentenceSimilarity(a: string, b: string): number {
  const A = sentenceWords(a), B = sentenceWords(b);
  if (!A.size || !B.size) return 0;
  const shared = sharedWordCount(A, B);
  return shared / (A.size + B.size - shared);
}

function sentenceContainment(a: string, b: string): number {
  const A = sentenceWords(a), B = sentenceWords(b);
  if (!A.size || !B.size) return 0;
  return sharedWordCount(A, B) / Math.min(A.size, B.size);
}

// The sentences of `after` that aren't restatements/re-splits of anything in `before`.
export function diffNewProfileSentences(before: string, after: string): Set<string> {
  const old = profileSentenceKeys(before);
  const oldExact = new Set(old);
  const oldWords = sentenceWords(before);
  const added = profileSentenceKeys(after).filter((s) => {
    if (oldExact.has(s)) return false;
    const words = sentenceWords(s);
    if (words.size < PROFILE_MIN_HIGHLIGHT_WORDS) return false;
    if (old.some((o) => sentenceSimilarity(s, o) >= PROFILE_REWORD_RATIO || sentenceContainment(s, o) >= PROFILE_CONTAINMENT_RATIO)) {
      return false;
    }
    const novel = words.size - sharedWordCount(words, oldWords);
    return novel / words.size >= PROFILE_MIN_NOVEL_WORD_RATIO;
  });
  return new Set(added);
}
