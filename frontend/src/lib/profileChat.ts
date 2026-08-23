import { callGeminiJSON } from './aiJson';

// Profile-chat flow, ported from script.js. CLAUDE.md's long note is the spec:
//   - OPENERS are cached (a pool of 10, a rotating window of 3 per open) and safe to
//     pre-generate — they depend only on the profile text.
//   - FOLLOW-UPS are ONE live call per bot turn and must NOT be pooled: a follow-up's job
//     is to react to what the student just said, which a pre-generated question can't.
//   - The transcript sent with a follow-up includes the BOT lines, not just the answers
//     ("Yes." is meaningless alone; the bot lines also stop the model re-asking).
//   - Style rules: one short sentence, never two questions joined by "and"/"or"; at most
//     2-3 profile details per question.
// The DOM/state orchestration (drawer, speak, render, session reset) is rebuilt in the
// Profile screen; model access is injected so this stays pure and auth-independent.

// A Claude text call: same {system, userContent, useWebSearch} shape as callGemini.
export type ClaudeCall = (
  system: string,
  userContent: string,
  useWebSearch?: boolean,
) => Promise<string>;

export interface ChatMessage {
  role: 'bot' | 'user';
  text: string;
}

export const STARTER_POOL_SIZE = 10;
export const STARTERS_PER_OPEN = 3;
// < 50 words is too thin for AI personalization — use predetermined icebreakers instead.
const INSUFFICIENT_WORD_COUNT = 50;

export const PREDETERMINED_STARTER_QUESTIONS = [
  'If your extracurriculars had a theme song, what would it be — and why does that fit you?',
  "What's something you're weirdly good at that has nothing to do with school?",
  'If you had one free Saturday with zero obligations, what would you actually do with it?',
  "What's a skill you're trying to get better at that's purely for fun?",
  'Tell me about the last time you got totally absorbed in something — what was it?',
  'Do you have any quirky obsessions or guilty pleasures we should know about?',
  'What was the last time you felt genuinely proud of yourself — what did you do?',
  'If you could be part of any group or team (real or imaginary), what would it be?',
  "What's something about your personality that surprises people when they get to know you?",
  'Do you create or make anything (art, music, code, video, crafts, cooking)? What appeals to you?',
  'What role do you usually play in group projects or friend groups — leader, organizer, listener, joker?',
  'Have you ever had a job or volunteer gig? What did you learn about yourself?',
  'What kind of stuff makes you lose track of time in the best way?',
  'If you could teach someone else one skill you have, what would it be?',
  "What's a topic or hobby you know way more about than most people your age?",
  'Tell me about someone who inspires you and why they do.',
  "What's something you've done that took guts or got you out of your comfort zone?",
  'Do you play sports or do any athletic stuff? Or is movement/fitness not really your thing?',
  "What's the most fun you've had in the last few months?",
  'Are you more of a solo person or do you prefer hanging with others?',
  'What would your friends say is your superpower?',
  'Have you ever been really into a cause, movement, or community (online or IRL)?',
];

// Generic fallbacks, used only if the AI call fails/times out AND a profile exists.
export const FALLBACK_STARTER_QUESTIONS = [
  "What's something you're weirdly good at that has nothing to do with school?",
  'If you had one free Saturday with zero obligations, what would you actually do with it?',
  'What was the last time you felt genuinely proud of yourself — what did you do?',
];

let predeterminedStarterRotationIndex = 0;
let starterWindowIndex = 0;

// Next `count` questions from the predetermined pool, rotating so repeated calls don't all
// land on the same handful.
export function getNextPredeterminedQuestions(count: number): string[] {
  const pool = PREDETERMINED_STARTER_QUESTIONS;
  const startIdx = (predeterminedStarterRotationIndex * count) % pool.length;
  predeterminedStarterRotationIndex =
    (predeterminedStarterRotationIndex + 1) % Math.max(1, Math.ceil(pool.length / count));
  const result: string[] = [];
  for (let i = 0; i < count; i++) result.push(pool[(startIdx + i) % pool.length]);
  return result;
}

export function getNextPredeterminedStarterQuestions(): string[] {
  return getNextPredeterminedQuestions(STARTERS_PER_OPEN);
}

// Empty or very thin profile — takes the text explicitly (the source tolerated the profile
// being edited mid-flight, so a cache slot judges the specific version it's computing against).
export function isProfileInsufficientForAI(text: string | null | undefined): boolean {
  const synthesized = text || '';
  const wordCount = synthesized.trim().split(/\s+/).filter((w) => w.length > 0).length;
  return wordCount < INSUFFICIENT_WORD_COUNT;
}

// Races a promise against a timeout so a hung call can't leave the chat stuck loading.
export function withTimeout<T>(promise: Promise<T>, ms: number, message?: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(message || 'Timed out')), ms),
    ),
  ]);
}

// Next `STARTERS_PER_OPEN` questions from the pool, wrapping around (so opening the drawer
// twice in a row doesn't show the identical three).
export function drawStarterWindow(pool: string[] | null | undefined): string[] {
  if (!pool || !pool.length) return FALLBACK_STARTER_QUESTIONS.slice();
  const start = (starterWindowIndex * STARTERS_PER_OPEN) % pool.length;
  starterWindowIndex =
    (starterWindowIndex + 1) % Math.max(1, Math.ceil(pool.length / STARTERS_PER_OPEN));
  const out: string[] = [];
  for (let i = 0; i < Math.min(STARTERS_PER_OPEN, pool.length); i++) {
    out.push(pool[(start + i) % pool.length]);
  }
  return out;
}

// Build the cached bank of 10 openers for the current profile (the `starterPool` slot).
export async function starterQuestionPoolFromAI(
  callClaude: ClaudeCall,
  text: string,
): Promise<string[]> {
  if (isProfileInsufficientForAI(text)) return getNextPredeterminedQuestions(STARTER_POOL_SIZE);
  const system = `You are a friendly, upbeat chatbot helping a high schooler build a detailed personal profile for finding extracurricular opportunities (research programs, internships, competitions, summer programs). You'll be given their CURRENT PROFILE SUMMARY. Come up with exactly TEN distinct, short, fun, wacky-but-meaningful icebreaker questions, each capable of opening a chat session on its own, probing for details the profile is missing or only has shallowly — think music, sports/athletics, hobbies, what they do purely for fun, family or community involvement, leadership moments, part-time jobs, quirks of personality, or deeper specifics on things already mentioned. Every question must be ONE short, plain sentence — never a run-on, never two questions joined with "and"/"or"/a semicolon. When a question draws on the profile, pull in at most 2-3 specific details from it at a time — don't try to connect four or more dots into one elaborate question. Keep the tone playful and casual, like a clever friend riffing with them, not a form — but every question must serve a real purpose in understanding this student for extracurricular/college-application matching. These ten are shown a few at a time across several visits, so keep them varied and non-overlapping with each other. Respond with ONLY a JSON array of exactly 10 short question strings, e.g. ["...", ...] — no markdown, no preamble, no numbering.`;
  const userContent = `CURRENT PROFILE SUMMARY:\n${text || '(empty)'}\n\nRespond with a JSON array of exactly ${STARTER_POOL_SIZE} questions only.`;
  const parsed = await withTimeout(
    callGeminiJSON<unknown>(callClaude, system, userContent, false),
    20000,
    'Timed out waiting for starter questions',
  );
  if (!Array.isArray(parsed) || !parsed.length) throw new Error('Unexpected starter question format');
  return parsed.slice(0, STARTER_POOL_SIZE).map(String);
}

// The 3 openers offered when the drawer opens (regenerate = student asked for a fresh set).
export async function profileChatStarterQuestionsFromAI(
  callClaude: ClaudeCall,
  profileText: string,
  chatRounds: number,
  regenerate: boolean,
): Promise<string[]> {
  if (isProfileInsufficientForAI(profileText)) return getNextPredeterminedStarterQuestions();
  const breadthDirective = regenerate
    ? ` The student explicitly asked to regenerate these — swap in a fresh set. Prioritize BREADTH over depth: favor surfacing entirely new areas of their life the profile hasn't touched at all (academics, social life, jobs, family, random obsessions, sports, art, gaming, etc.) over drilling further into what's already well-covered. Where a question does build on something they've already mentioned, use it only as a springboard to go one layer deeper on that specific thing — but most of the three should open up completely uncovered territory rather than deepen existing ones.`
    : '';
  const system = `You are a friendly, upbeat chatbot helping a high schooler build a detailed personal profile for finding extracurricular opportunities (research programs, internships, competitions, summer programs). You'll be given their CURRENT PROFILE SUMMARY (may be empty). Come up with exactly THREE distinct, short, fun, wacky-but-meaningful icebreaker questions to kick off a chat session that probes for details the profile is missing or only has shallowly — think music, sports/athletics, hobbies, what they do purely for fun, leadership, part-time jobs, quirks of personality, or deeper specifics on things already mentioned.${breadthDirective} Every question must be ONE short, plain sentence — never a run-on, never two questions joined with "and"/"or"/a semicolon. When a question draws on the profile, pull in at most 2-3 specific details from it at a time — don't try to connect four or more dots into one elaborate question. Keep each one playful and casual, like a clever friend riffing with them, not a form — but each must serve a real purpose in understanding this student for extracurricular/college-application matching. This is chat round ${chatRounds + 1} of them returning to this page — the higher that number, the more specific and creative the questions should get. Respond with ONLY a JSON array of exactly 3 short question strings, e.g. ["...", "...", "..."] — no markdown, no preamble, no numbering.`;
  const userContent = `CURRENT PROFILE SUMMARY:\n${profileText || '(empty)'}\n\nRespond with a JSON array of exactly 3 starter questions only.`;
  const parsed = await withTimeout(
    callGeminiJSON<unknown>(callClaude, system, userContent, false),
    20000,
    'Timed out waiting for starter questions',
  );
  if (!Array.isArray(parsed) || !parsed.length) throw new Error('Unexpected starter question format');
  return parsed.slice(0, STARTERS_PER_OPEN).map(String);
}

// The bot's next question — ONE live call, deliberately NOT pooled. Transcript sent whole.
export async function profileChatNextQuestion(
  callClaude: ClaudeCall,
  profileText: string,
  history: ChatMessage[],
  chatRounds: number,
): Promise<string> {
  const system = `You are a friendly, upbeat chatbot helping a high schooler build a detailed personal profile for finding extracurricular opportunities (research programs, internships, competitions, summer programs). You'll be given their CURRENT PROFILE SUMMARY (may be empty) and the CONVERSATION SO FAR in this session. Ask exactly ONE short, fun, wacky-but-meaningful question. If their last answer introduced something specific — a project, a role, a place, a result — follow up on THAT rather than changing the subject: ask what exactly they did, what their part in it was, what surprised them, or what they'd change. Only open a new topic when the last answer was thin or the thread is genuinely exhausted, and then favour ground the profile hasn't covered (music, sports/athletics, hobbies, family or community involvement, leadership moments, part-time jobs, quirks of personality). Your question must be ONE short, plain sentence — never a run-on, never two questions joined with "and"/"or"/a semicolon. Draw on at most 2-3 specific details at a time — don't try to connect four or more dots into one elaborate question. This is chat round ${chatRounds + 1} of them returning to this page — the more rounds, the more specific and creative your questions should get; don't repeat ground already covered earlier in this conversation. Keep your tone playful and casual, like a clever friend riffing with them, not a form — but every question must serve a real purpose in understanding this student for extracurricular/college-application matching. No lists, no markdown, no preamble, and no "Great!" acknowledgment beyond at most a few words of playful reaction folded into the same sentence.`;
  const transcript =
    history.map((m) => `${m.role === 'bot' ? 'You' : 'Student'}: ${m.text}`).join('\n') ||
    '(nothing yet)';
  const userContent = `CURRENT PROFILE SUMMARY:\n${profileText || '(empty)'}\n\nCONVERSATION SO FAR:\n${transcript}\n\nRespond with your next single question only — no preamble, no quotes around it.`;
  const raw = await withTimeout(
    callClaude(system, userContent, false),
    20000,
    'Timed out waiting for the next question',
  );
  return raw.trim();
}

// The transcript in the shape synthesizeProfile's transcript mode expects (Bot: / Student:).
export function profileChatTranscript(history: ChatMessage[]): string {
  return history.map((m) => `${m.role === 'bot' ? 'Bot' : 'Student'}: ${m.text}`).join('\n');
}
