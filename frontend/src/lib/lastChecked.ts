import { onSessionReset } from './sessionScope';

// The Quest Log's "Last checked: …" line, held across navigation.
//
// It used to be plain component state, so it reset to "Last checked: never" the moment the
// student switched tabs — the check had really happened, and the app then said it never had.
// That reads as the refresh having failed, which is the one thing it must not imply.
//
// A module singleton rather than persisted storage, deliberately, and for the same reason
// newlyAdded.ts is one: this describes THIS session's work. Writing it into the server-side
// profile would make a stamp from three days ago greet the student on a fresh load, which is
// staler than saying nothing. A reload legitimately starts over at "never".
const DEFAULT_LABEL = 'Last checked: never';

let label = DEFAULT_LABEL;

export function getLastCheckedLabel(): string {
  return label;
}

export function setLastCheckedLabel(next: string): void {
  label = next || DEFAULT_LABEL;
}

// A session-long memory is the point, so nothing resets this DURING a session. It does have to
// end with the session though: on a shared device the next account was greeted with the
// previous one's "Last checked" line (Phase 5, finding 18).
export function resetLastCheckedLabel(): void {
  label = DEFAULT_LABEL;
}

onSessionReset(resetLastCheckedLabel);
