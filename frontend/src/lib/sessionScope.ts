// Module state that belongs to ONE signed-in session, and how it gets cleared.
//
// Phase 5, frontend_report finding 18: "Module singletons (lastChecked, _lastCatalogStamp,
// sessionSearch, newlyAdded) never reset on logout → next account on the same device sees the
// previous account's 'Last checked' / cached results until reload."
//
// The singletons themselves are right, and each one's own file says why: they describe THIS
// session's work, they must survive a screen unmounting (expo-router renders a <Slot/>, so the
// previous screen is gone by the time the next one mounts), and persisting them would be worse
// — a stamp from three days ago greeting a student on a fresh load is staler than saying
// nothing. What was missing is the other end of that lifetime. "This session" ended at logout
// and nothing said so, so on a shared device — a school laptop, a sibling's phone — the next
// account saw the previous one's cached search results and their "Last checked" line.
//
// A REGISTRY rather than a list of imports in forgetSession(), for one specific reason: the
// clearing has to happen in api/httpClient, and httpClient cannot import a screen. Registration
// also puts the reset next to the state it resets, so a new singleton is one line away from
// being covered instead of needing somebody to remember a file two directories up.

type Reset = () => void;

const resets = new Set<Reset>();

/**
 * Register a reset to run when the session ends. Returns an unregister function.
 *
 * Call this at module scope, beside the state it clears. A reset must be safe to run when
 * there is nothing to clear and safe to run twice — it fires on every sign-out, including one
 * that follows another.
 */
export function onSessionReset(reset: Reset): () => void {
  resets.add(reset);
  return () => resets.delete(reset);
}

/**
 * Clear every registered singleton. Called by httpClient.forgetSession(), which is the ONE
 * place a session ends — sign-out, a revoked refresh token, and a bumped token_version all
 * arrive there.
 *
 * A throwing reset must not stop the others: this runs while the app is tearing a session
 * down, and a half-cleared device is exactly the state the finding is about.
 */
export function resetSessionScopedState(): void {
  for (const reset of resets) {
    try {
      reset();
    } catch {
      /* a broken reset must not leave the rest of the device holding the last account's data */
    }
  }
}

/** How many resets are registered. Tests only — a guard against a module quietly opting out. */
export function registeredResetCount(): number {
  return resets.size;
}
