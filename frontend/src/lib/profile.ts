import { type FeatureCall } from './aiJson';
import { PROFILE_SUFFICIENT_LENGTH } from './constants';

// Profile logic ported from script.js. The DOM/state orchestration (studentProfile global,
// render*, saveProfile, the drawer) is rebuilt in the Profile screen; these are the pure,
// model-backed cores the plan calls out to salvage. Model access is injected.

// The synthesis output budget and its retry-at-the-ceiling now live server-side, in
// app/services/prompts.py, along with the prompt they belong to (S1-1). A client that could
// name its own token budget could also name an 8k one on every call.
// Past this many days without an update, the Dashboard nudges a refresh.
export const PROFILE_STALE_DAYS = 14;
// A dangling final paragraph shorter than this is "sloppy, not truncated".
export const PROFILE_MIN_HIGHLIGHT_WORDS = 4;

// The merge prompt always wants a NEW INFORMATION block; a repair genuinely has none.
export const REPAIR_ONLY_INPUT =
  '(nothing new — this pass is only to repair any incomplete text already in the profile above, leaving everything else exactly as it is)';

// Merge new information into the single running first-person profile. Throws if the model's
// output is still truncated after the ceiling retry, so the caller can keep the last complete
// profile rather than saving a fragment over it.
export async function synthesizeProfile(
  callFeature: FeatureCall,
  existing: string,
  newText: string,
  isTranscript = false,
): Promise<string> {
  const res = await callFeature('profile_synthesis', { existing, newText, isTranscript });
  // Still throws on a truncated answer, and that is the point of keeping `truncated` on the
  // wire: the server already retried at its ceiling, so a truncated result here means even
  // the ceiling was not enough — and saving a fragment over a complete profile is worse
  // than saving nothing.
  if (res.truncated) throw new Error('Profile synthesis was truncated by the model.');
  return res.text.trim();
}

// Re-run synthesis over the profile alone (no new info) so the repair clause can act on a
// dangling fragment. Cannot add anything the student didn't say.
export async function repairProfileText(
  callFeature: FeatureCall,
  existing: string,
): Promise<string> {
  return synthesizeProfile(callFeature, existing, REPAIR_ONLY_INPUT);
}

// Pull the student's own words out of a "Bot: ... / Student: ..." transcript.
export function transcriptStudentLines(transcript: string | null | undefined): string {
  return (transcript || '')
    .split('\n')
    .filter((l) => /^Student:/.test(l.trim()))
    .map((l) => l.replace(/^\s*Student:\s*/, '').trim())
    .filter(Boolean)
    .join(' ');
}

// Does the profile text carry damage from a write that was cut off short? Synthesis emits
// general paragraphs first, then "Passion Project: ", then "Research Project: " ones, so a
// budget-truncated response always loses its TAIL (the projects). (Takes the text explicitly;
// the source read a global.)
export function profileHasTruncatedTail(synthesized: string | null | undefined): boolean {
  const text = (synthesized || '').trim();
  if (!text) return false;
  const paragraphs = text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  const last = paragraphs[paragraphs.length - 1] || '';
  // A complete profile ends on terminal punctuation (optionally followed by a closing
  // quote/bracket). Anything else is a sentence that never finished.
  if (/[.!?]["'’”)\]]?$/.test(last)) return false;
  // A one-line profile with no punctuation at all is sloppy, not truncated.
  return last.split(/\s+/).length >= PROFILE_MIN_HIGHLIGHT_WORDS;
}

export function countProfileWords(text: string | null | undefined): number {
  if (!text) return 0;
  return text.trim().split(/\s+/).filter((w) => w.length > 0).length;
}

export function profileIsSufficient(text: string | null | undefined): boolean {
  return countProfileWords(text) >= PROFILE_SUFFICIENT_LENGTH;
}

// assessProfileReadiness and its ProfileReadiness type were DELETED by S1-1, not ported.
// It had no caller anywhere in the app, and porting a dead prompt server-side would have
// kept a model call reachable that nothing needed.
