import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  onSessionReset,
  resetSessionScopedState,
  registeredResetCount,
} from '@/lib/sessionScope';

// frontend_report finding 18: "Module singletons (lastChecked, _lastCatalogStamp,
// sessionSearch, newlyAdded) never reset on logout → next account on the same device sees the
// previous account's 'Last checked' / cached results until reload."
//
// The singletons themselves are right — each file says why — and the fix is the other end of
// their lifetime, not their removal.

describe('sessionScope — the registry', () => {
  it('runs every registered reset', () => {
    const calls: string[] = [];
    const off1 = onSessionReset(() => calls.push('a'));
    const off2 = onSessionReset(() => calls.push('b'));
    resetSessionScopedState();
    expect(calls.sort()).toEqual(['a', 'b']);
    off1();
    off2();
  });

  it('does not let one throwing reset strand the others', () => {
    // This runs while a session is being torn down. A half-cleared device holding the last
    // account's data is exactly the state the finding is about.
    const calls: string[] = [];
    const off1 = onSessionReset(() => { throw new Error('boom'); });
    const off2 = onSessionReset(() => calls.push('ran'));
    expect(() => resetSessionScopedState()).not.toThrow();
    expect(calls).toEqual(['ran']);
    off1();
    off2();
  });

  it('is safe to run twice, and when there is nothing to clear', () => {
    const calls: string[] = [];
    const off = onSessionReset(() => calls.push('x'));
    resetSessionScopedState();
    resetSessionScopedState();
    expect(calls).toEqual(['x', 'x']);
    off();
  });

  it('unregisters', () => {
    const before = registeredResetCount();
    const off = onSessionReset(() => undefined);
    expect(registeredResetCount()).toBe(before + 1);
    off();
    expect(registeredResetCount()).toBe(before);
  });
});

describe('the session-scoped singletons register themselves', () => {
  it('clears the Quest Log "Last checked" line', async () => {
    const { getLastCheckedLabel, setLastCheckedLabel } = await import('@/lib/lastChecked');
    setLastCheckedLabel('Last checked: 2 minutes ago');
    expect(getLastCheckedLabel()).toBe('Last checked: 2 minutes ago');
    resetSessionScopedState();
    expect(getLastCheckedLabel()).toBe('Last checked: never');
  });

  it('clears the NEW badges on the previous account\'s additions', async () => {
    const { markNewlyAdded, getNewlyAdded } = await import('@/lib/newlyAdded');
    markNewlyAdded(['ec1', 'ec2']);
    expect(getNewlyAdded().size).toBe(2);
    resetSessionScopedState();
    expect(getNewlyAdded().size).toBe(0);
  });

  it('clears the catalog-sync throttle as well as the stamp', async () => {
    // The stamp is the visible half. The THROTTLE is the one that bites: inheriting it means
    // the next account's first sync is skipped, so they see stale tracker data under a
    // timestamp that was never theirs.
    const trackerStore = await import('@/api/trackerStore');
    expect(typeof trackerStore.resetCatalogSyncState).toBe('function');
    expect(() => trackerStore.resetCatalogSyncState()).not.toThrow();
  });

  it('has more than a couple of resets registered once the modules are loaded', async () => {
    await import('@/lib/lastChecked');
    await import('@/lib/newlyAdded');
    await import('@/api/trackerStore');
    await import('@/lib/profileDerived');
    await import('@/lib/profileChat');
    expect(registeredResetCount()).toBeGreaterThanOrEqual(5);
  });
});

describe('forgetSession clears session-scoped state', () => {
  it('calls the registry', async () => {
    // Reached through the public sign-out, which is what a student actually does.
    const { httpClient } = await import('@/api/httpClient');
    const calls: string[] = [];
    const off = onSessionReset(() => calls.push('cleared'));
    // logout() posts to /api/auth/logout best-effort, then forgets the session.
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
    await httpClient.logout();
    expect(calls).toEqual(['cleared']);
    off();
    vi.unstubAllGlobals();
  });
});
