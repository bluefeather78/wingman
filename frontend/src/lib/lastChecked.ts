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

// Only for tests — the app never resets this, since a session-long memory is the point.
export function resetLastCheckedLabel(): void {
  label = DEFAULT_LABEL;
}
