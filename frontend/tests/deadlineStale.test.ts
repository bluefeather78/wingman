import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { deadlineStampNeedsCheck } from '@/api/trackerStore';

// The client-side half of the login / Quest Log staleness fan-out (MARQUEE M9, approved
// 2026-09-27). This decides WHICH tracked rows get a fresh deadline check; the server's own
// 7-day gate (DEADLINE_STALE_DAYS in app/services/deadlines.py) stays the authority, so a
// disagreement at the margin only ever costs a free cached round trip, never a wrong write.
describe('deadlineStampNeedsCheck', () => {
  const NOW = Date.parse('2026-09-27T12:00:00Z');
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it('treats a missing stamp as needing a check (never checked)', () => {
    expect(deadlineStampNeedsCheck(null)).toBe(true);
    expect(deadlineStampNeedsCheck(undefined)).toBe(true);
    expect(deadlineStampNeedsCheck('')).toBe(true);
  });

  it('treats an unparseable stamp as needing a check (do not trust a value we cannot date)', () => {
    expect(deadlineStampNeedsCheck('not a date')).toBe(true);
    expect(deadlineStampNeedsCheck('2026-13-45')).toBe(true);
  });

  it('keeps a stamp fresh until it crosses the 7-day window', () => {
    // 6 days, 23 hours old — still inside the window.
    expect(deadlineStampNeedsCheck('2026-09-20T13:00:00Z')).toBe(false);
    // Just now.
    expect(deadlineStampNeedsCheck('2026-09-27T12:00:00Z')).toBe(false);
  });

  it('marks a stamp stale at or past 7 days old', () => {
    // Exactly 7 days.
    expect(deadlineStampNeedsCheck('2026-09-20T12:00:00Z')).toBe(true);
    // Well past.
    expect(deadlineStampNeedsCheck('2026-08-01T12:00:00Z')).toBe(true);
  });

  it('accepts a Z-suffixed UTC stamp the same as an explicit offset', () => {
    expect(deadlineStampNeedsCheck('2026-09-26T12:00:00+00:00')).toBe(false);
  });
});
