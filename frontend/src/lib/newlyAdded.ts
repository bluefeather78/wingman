// The batch of opportunities just added to the Quest Log from Fresh Finds, so the Quest Log
// can float them to the top of the list and badge them NEW.
//
// Deliberately a module singleton and NOT persisted: this is the retired SPA's
// `newlyAddedTrackerIds` behaviour — "the batch you just added, this visit". It resets on
// reload, which is the point. Persisting it would mean writing an addedAt stamp into
// hs-tracker-data, and a card would keep claiming to be new across sessions.
//
// Lifetime matches the original exactly: script.js's showPage() cleared the set the moment
// the user navigated AWAY from the Tracker ("shown only for the batch of opportunities added
// in the current session"), so the marker survives one visit to the Quest Log, not the whole
// session. clearNewlyAdded() is called from the Quest Log's blur.
let newlyAdded = new Set<string>();

// Replaces the previous batch — only the most recent add carries the NEW treatment, exactly
// as the old app reset the set at the top of buildTracker().
export function markNewlyAdded(ids: Iterable<string>): void {
  newlyAdded = new Set(ids);
}

export function getNewlyAdded(): Set<string> {
  return newlyAdded;
}

export function clearNewlyAdded(): void {
  newlyAdded = new Set();
}
