import { callGeminiJSON, type ClaudeDetailedCall, type GeminiCall } from './aiJson';
import { PROFILE_SUFFICIENT_LENGTH } from './constants';
import { ACTIVE_KINDS, KIND_CONFIG } from './kinds';

// Profile logic ported from script.js. The DOM/state orchestration (studentProfile global,
// render*, saveProfile, the drawer) is rebuilt in the Profile screen; these are the pure,
// model-backed cores the plan calls out to salvage. Model access is injected.

// Output budget for synthesis, not a content limit: the profile is rewritten whole on every
// merge, so the answer grows with the profile and a fixed cap eventually cuts it mid-sentence.
// Unused budget is free (billed on tokens produced), so ask generously and retry once at the
// ceiling. There is deliberately no word limit in the prompt, storage, or display.
export const PROFILE_SYNTH_MAX_TOKENS = 4000;
export const PROFILE_SYNTH_MAX_TOKENS_RETRY = 8000;
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
  callClaudeDetailed: ClaudeDetailedCall,
  existing: string,
  newText: string,
  isTranscript = false,
): Promise<string> {
  const system = `You maintain a single, coherent running profile of a high school student's academic and extracurricular interests, built up over multiple sessions. You'll be given the student's CURRENT profile (may be empty) and NEW information they just added. Merge the new information in: add genuinely new details, and update or remove anything the new information supersedes or contradicts. Do not drop specific, still-relevant details from the current profile just because they weren't repeated in the new information. Write it as concise statements in FIRST PERSON, as if the student is describing themself (e.g. "I'm interested in...", "I've been working on...", "My goal is..." — not third person, not addressed to the student, not a bulleted list, no markdown). Structure the output as short paragraphs separated by a blank line (double newline). General paragraphs (no prefix) should cover academic interests, extracurriculars, and goals — 1-3 such paragraphs is typical. If the student has described any larger, longer-term "marquee" projects they're personally driving (as opposed to one-off activities or classes), describe EACH one in its OWN separate paragraph prefixed with the literal text "Passion Project: " — one such paragraph per distinct project, never combining multiple projects into one paragraph. Separately, if the student has described any independent research projects (research, papers, studies they're conducting), describe EACH one in its OWN separate paragraph prefixed with the literal text "Research Project: ", same rule — one per project. A project that fits both categories should be listed under whichever one fits best, not both. Only include these prefixed paragraphs for projects actually described — don't fabricate any. If the CURRENT PROFILE ends mid-sentence, or contains a paragraph that is obviously an incomplete fragment, that is damage from an earlier write that was cut off short — repair it rather than preserving it verbatim: finish the thought only if the rest of the profile makes what was meant unambiguous, and otherwise drop the incomplete fragment. Never invent details to fill such a gap. Respond with ONLY the updated profile text — no preamble, no quotes around it.${isTranscript ? ` The NEW INFORMATION is a raw transcript of a chat between this app's bot and the student, not prose written for you. Use only what the Student lines actually say; the Bot lines are prompts, not facts about the student, and small talk should be ignored. Never quote the transcript verbatim — restate what was learned in the student's first-person voice.` : ''}`;
  const userContent = `CURRENT PROFILE:\n${existing || '(empty — nothing recorded yet)'}\n\nNEW INFORMATION TO ADD${isTranscript ? ' (raw chat transcript)' : ''}:\n${newText}\n\nRespond with the updated, merged profile text only.`;
  let res = await callClaudeDetailed(system, userContent, false, PROFILE_SYNTH_MAX_TOKENS);
  if (res.truncated) {
    res = await callClaudeDetailed(system, userContent, false, PROFILE_SYNTH_MAX_TOKENS_RETRY);
  }
  if (res.truncated) throw new Error('Profile synthesis was truncated by the model.');
  return res.text.trim();
}

// Re-run synthesis over the profile alone (no new info) so the repair clause can act on a
// dangling fragment. Cannot add anything the student didn't say.
export async function repairProfileText(
  callClaudeDetailed: ClaudeDetailedCall,
  existing: string,
): Promise<string> {
  return synthesizeProfile(callClaudeDetailed, existing, REPAIR_ONLY_INPUT);
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

export interface ProfileReadiness {
  ready: boolean;
  kinds?: string[];
  questions?: (string | { text?: string })[];
}

// Decide whether a profile has enough detail to recommend, and which kinds are relevant.
export async function assessProfileReadiness(
  callGemini: GeminiCall,
  profileText: string,
): Promise<ProfileReadiness> {
  const kindList = ACTIVE_KINDS.map(
    (k) => `"${k}" (${KIND_CONFIG[k].name}: ${KIND_CONFIG[k].desc})`,
  ).join(', ');
  const system = `You help decide whether a student's profile has enough detail to confidently recommend extracurricular opportunities, and which types are relevant. Valid opportunity type keys: ${kindList}. Read the profile below. If it gives clear enough signal about what the student wants to do and why, respond with ONLY raw JSON, no markdown, no preamble: {"ready":true,"kinds":["one or more of the valid type keys, the ones genuinely relevant"]}. If it's too vague, sparse, or ambiguous to match well, respond with ONLY raw JSON matching: {"ready":false,"questions":["a short, specific clarifying question", "..."]}. Ask at most 3 questions, and only ones that would actually change which opportunities fit — don't ask generic questions the profile already answers.`;
  return callGeminiJSON<ProfileReadiness>(callGemini, system, profileText, false);
}
