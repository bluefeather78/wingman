// A cross-screen signal for "the profile is being rewritten right now".
//
// Profile synthesis runs on My Vibe and takes seconds (it rewrites the whole profile, at a
// 4-8k output budget). Navigating to Fresh Finds mid-write raced it: the finder read
// whatever was stored at that instant, which was still the PREVIOUS profile, matched its
// session cache against that old text, and showed the old list — then the write landed and
// the list changed underneath the student. Same profile, two different answers, seconds
// apart, with nothing on screen explaining the swap.
//
// A module singleton is the right mechanism for the same reason sessionSearch and
// newlyAdded are: the two screens are separate routes and the authed shell renders a
// <Slot/>, so My Vibe is UNMOUNTED by the time the finder mounts. Component state cannot
// span that gap; the module does.
//
// Deliberately NOT persisted. An interrupted write (reload, crash) must not leave a flag
// behind that makes the next session wait for something that will never finish.

let pending = 0;
let waiters: (() => void)[] = [];

// How long a reader will wait before giving up and using whatever is stored. Synthesis is
// normally a few seconds; this is a backstop, not an expected path. Waiting forever on a
// write that died would be a spinner that never resolves, which is worse than briefly
// showing results derived from the previous profile.
export const PROFILE_WRITE_WAIT_MS = 30000;

function flush(): void {
  const queued = waiters;
  waiters = [];
  queued.forEach((w) => w());
}

/** Mark the start of a profile rewrite. MUST be paired with endProfileWrite in a finally. */
export function beginProfileWrite(): void {
  pending += 1;
}

export function endProfileWrite(): void {
  pending = Math.max(0, pending - 1);
  if (pending === 0) flush();
}

export function profileWriteInFlight(): boolean {
  return pending > 0;
}

/**
 * Resolves once no profile rewrite is in flight (immediately when there is none).
 * Never rejects: a failed synthesis still calls endProfileWrite, and the timeout covers
 * the case where it somehow does not.
 */
export function awaitProfileWrites(timeoutMs: number = PROFILE_WRITE_WAIT_MS): Promise<void> {
  if (pending === 0) return Promise.resolve();
  return new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      // Drop this waiter so a later flush doesn't call an already-resolved resolver.
      waiters = waiters.filter((w) => w !== onDone);
      console.warn('Timed out waiting for profile synthesis; using the stored profile.');
      resolve();
    }, timeoutMs);
    const onDone = () => {
      clearTimeout(timer);
      resolve();
    };
    waiters.push(onDone);
  });
}
