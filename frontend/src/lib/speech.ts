import { Platform } from 'react-native';

// The Web Speech API shims for the My Vibe chat drawer — dictation in, spoken questions out.
//
// Phase 5, frontend_report §4 ("Files over 800 lines — suggested splits"), which names
// `src/lib/speech.ts` for exactly this. It was ~70 lines of browser feature-detection and
// `globalThis as Record<string, unknown>` casts inlined in a screen that is otherwise about
// profiles, and it is the only part of that screen with no React in it at all.
//
// WEB ONLY, and each capability is detected INDEPENDENTLY (matching the retired SPA's
// initProfileChatVoiceUI): a browser can have speech synthesis without recognition and the
// reverse, so one combined "voice is available" flag would hide a working half. On native the
// two constants are simply false — expo has no equivalent wired up, and a control that does
// nothing is worse than one that is not there.
//
// The casts are unavoidable rather than lazy: neither API is in React Native's DOM lib, and
// `webkitSpeechRecognition` is not in anyone's. They are confined to this file so no screen
// has to repeat them.

export type SpeechRecognitionLike = {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((e: { results: { 0: { transcript: string } }[] }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: unknown) => void) | null;
  start: () => void;
  stop: () => void;
};

type SynthLike = { cancel: () => void; speak: (u: unknown) => void };

function global<T>(name: string): T | null {
  return ((globalThis as Record<string, unknown>)[name] as T | undefined) ?? null;
}

/** The browser's SpeechRecognition constructor, or null where there is none. */
export const SpeechRecognitionCtor: (new () => SpeechRecognitionLike) | null =
  Platform.OS === 'web'
    ? global<new () => SpeechRecognitionLike>('SpeechRecognition')
      ?? global<new () => SpeechRecognitionLike>('webkitSpeechRecognition')
    : null;

/** Whether questions can be read aloud. Independent of dictation — see the note above. */
export const ttsAvailable =
  Platform.OS === 'web' && typeof globalThis !== 'undefined' && 'speechSynthesis' in globalThis;

/**
 * Speak `text`, cancelling anything already in flight.
 *
 * Cancelling first is deliberate: the chat can produce a new question while the previous one
 * is still being read, and browsers QUEUE utterances rather than replacing them — without the
 * cancel a student who sends three messages quickly hears all three answers back to back,
 * long after the screen has moved on.
 */
export function speakText(text: string): void {
  if (!ttsAvailable || !text) return;
  const synth = global<SynthLike>('speechSynthesis');
  synth?.cancel();
  const Utter = global<new (t: string) => unknown>('SpeechSynthesisUtterance');
  if (Utter) synth?.speak(new Utter(text));
}

/** Stop anything currently being spoken. Safe where there is no synthesis at all. */
export function cancelSpeech(): void {
  if (!ttsAvailable) return;
  global<SynthLike>('speechSynthesis')?.cancel();
}

/**
 * A configured recognizer, or null where dictation is unavailable.
 *
 * `interimResults` is on so the draft box fills as the student talks rather than jumping to a
 * finished sentence; `onend` is where the caller sends, because the API fires it on both a
 * deliberate stop and a natural pause.
 */
export function createRecognizer(handlers: {
  onTranscript: (text: string) => void;
  onEnd: () => void;
  onError: () => void;
}): SpeechRecognitionLike | null {
  if (!SpeechRecognitionCtor) return null;
  const rec = new SpeechRecognitionCtor();
  rec.lang = 'en-US';
  rec.interimResults = true;
  rec.maxAlternatives = 1;
  rec.onresult = (e) => {
    // The results list is array-LIKE, not an array — no .map, no spread. Every interim result
    // is concatenated because the browser reports the utterance in growing fragments rather
    // than resending the whole thing.
    let transcript = '';
    const results = e.results as unknown as { length: number };
    for (let i = 0; i < results.length; i += 1) transcript += e.results[i][0].transcript;
    handlers.onTranscript(transcript);
  };
  rec.onend = handlers.onEnd;
  rec.onerror = handlers.onError;
  return rec;
}
