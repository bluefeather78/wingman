import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { callFeatureJSON, MAX_PAID_ATTEMPTS, RETRY_BACKOFF_MS } from '@/lib/aiJson';
import { ENRICH_MAX_ROUNDS, enrichProfileTags } from '@/lib/profileTags';

// MARQUEE M9 (Phase 5, frontend_report finding 7 and 5). These count BILLED CALLS, which is
// why they are worth having at all: every one of these numbers is money, and the failure they
// guard against is silent — a well-meaning "retry once more for reliability" at a call site
// multiplies against the retry that already exists here and nothing on screen changes.
//
// The finding: "parse-retry inside callGeminiJSON × outer retry = up to 4 paid calls per
// search and per tracker add; enrichment up to 4 rounds; no global budget."

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** Runs `fn` with timers advanced automatically, so the retry backoff does not stall a test. */
function run<T>(fn: () => Promise<T>): Promise<T> {
  // The handlers are attached SYNCHRONOUSLY, before any timer is advanced. Awaiting the
  // promise only after advancing timers leaves a rejection unhandled for a tick, which Vitest
  // reports as an unhandled rejection and warns can produce false positives.
  const settled = fn().then(
    (value) => ({ ok: true as const, value }),
    (error: unknown) => ({ ok: false as const, error }),
  );
  return (async () => {
    await vi.advanceTimersByTimeAsync(RETRY_BACKOFF_MS * (MAX_PAID_ATTEMPTS + 2));
    const result = await settled;
    if (result.ok) return result.value;
    throw result.error;
  })();
}

describe('callFeatureJSON is the only retry', () => {
  it('costs ONE call when the first answer parses', async () => {
    const call = vi.fn(async () => ({ text: '{"ok":true}', truncated: false }));
    expect(await run(() => callFeatureJSON(call, 'ranking', {}))).toEqual({ ok: true });
    expect(call).toHaveBeenCalledTimes(1);
  });

  it('retries a malformed answer exactly once', async () => {
    const call = vi.fn()
      .mockResolvedValueOnce({ text: 'not json at all', truncated: false })
      .mockResolvedValueOnce({ text: '{"ok":true}', truncated: false });
    expect(await run(() => callFeatureJSON(call, 'ranking', {}))).toEqual({ ok: true });
    expect(call).toHaveBeenCalledTimes(2);
  });

  it('retries a NETWORK failure too — which is why an outer retry was pure multiplication', async () => {
    // The call sites that wrapped this each believed they were covering "a transient
    // network/API error" that this function did not handle. It always did: the catch wraps
    // the call as well as the parse.
    const call = vi.fn()
      .mockRejectedValueOnce(new Error('Failed to fetch'))
      .mockResolvedValueOnce({ text: '{"ok":true}', truncated: false });
    expect(await run(() => callFeatureJSON(call, 'ranking', {}))).toEqual({ ok: true });
    expect(call).toHaveBeenCalledTimes(2);
  });

  it('never bills more than MAX_PAID_ATTEMPTS, whatever keeps failing', async () => {
    const call = vi.fn(async () => ({ text: 'still not json', truncated: false }));
    await expect(run(() => callFeatureJSON(call, 'ranking', {}))).rejects.toThrow();
    expect(call).toHaveBeenCalledTimes(MAX_PAID_ATTEMPTS);
    expect(MAX_PAID_ATTEMPTS).toBe(2);
  });

  it('waits before re-asking', async () => {
    // Neither a formatting glitch nor a transient network error clears in the same
    // millisecond, which is why the backoff moved in here from the call sites that had it.
    const call = vi.fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({ text: '{"ok":true}', truncated: false });
    const p = callFeatureJSON(call, 'ranking', {});
    await vi.advanceTimersByTimeAsync(RETRY_BACKOFF_MS - 10);
    expect(call).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(20);
    await p;
    expect(call).toHaveBeenCalledTimes(2);
  });
});

describe('the call sites do not add a second retry', () => {
  it('trackerAdd extracts meta/fit at most twice per opportunity', async () => {
    // Reading the source rather than driving the whole add path: the regression is somebody
    // re-adding a try/catch retry, and that is exactly what this sees.
    const src = await import('node:fs').then((fs) =>
      fs.readFileSync(new URL('../src/api/trackerAdd.ts', import.meta.url), 'utf8'));
    const extractCalls = src.match(/extractTrackerInfo\(/g) ?? [];
    expect(extractCalls.length).toBe(1);
  });

  it('the finder never calls the ranker from inside a catch', async () => {
    // Two call sites is correct — the suggest path and the form path are different searches.
    // What must not come back is a RETRY: a second rankCandidates in the catch of the first,
    // which is what made one press of "Find my matches" bill four reasoning calls.
    const src = await import('node:fs').then((fs) =>
      fs.readFileSync(new URL('../app/(app)/finder.tsx', import.meta.url), 'utf8'));
    const sites = [...src.matchAll(/await rankCandidates\(/g)];
    expect(sites.length).toBe(2);
    for (const site of sites) {
      const before = src.slice(Math.max(0, (site.index ?? 0) - 400), site.index);
      expect(before).not.toMatch(/catch\s*\(/);
    }
  });
});

describe('tag enrichment is bounded', () => {
  it('stops at ENRICH_MAX_ROUNDS, which is 2', async () => {
    expect(ENRICH_MAX_ROUNDS).toBe(2);
    let round = 0;
    // Every round returns ONE more tag, so the loop keeps making partial progress and never
    // hits its early break — the only case that reaches the ceiling.
    const call = vi.fn(async () => {
      round += 1;
      return { text: JSON.stringify([{ tag: `t${round}`, intent: 'x', nextSteps: [] }]),
               truncated: false };
    });
    await run(() => enrichProfileTags(call, ['t1', 't2', 't3', 't4', 't5']));
    expect(call.mock.calls.length).toBeLessThanOrEqual(ENRICH_MAX_ROUNDS * MAX_PAID_ATTEMPTS);
  });

  it('stops immediately when a round adds nothing, rather than spending the rest', async () => {
    const call = vi.fn(async () => ({ text: '[]', truncated: false }));
    await run(() => enrichProfileTags(call, ['t1', 't2']));
    expect(call).toHaveBeenCalledTimes(1);
  });

  it('keeps every tag even when enrichment never answers', async () => {
    // The cost of the lower round cap: a slightly weaker facet, never a missing one.
    const call = vi.fn(async () => ({ text: '[]', truncated: false }));
    const out = await run(() => enrichProfileTags(call, ['robotics', 'debate']));
    expect(out.map((t) => t.tag)).toEqual(['robotics', 'debate']);
  });

  it('does not call at all when the seed already covers every tag', async () => {
    const call = vi.fn(async () => ({ text: '[]', truncated: false }));
    const out = await enrichProfileTags(call, ['robotics'], {
      robotics: { tag: 'robotics', intent: 'build', nextSteps: ['join a team'] },
    });
    expect(call).not.toHaveBeenCalled();
    expect(out[0].intent).toBe('build');
  });
});
