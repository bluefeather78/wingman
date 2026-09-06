import { describe, it, expect } from 'vitest';
import { subscriptionEnding, isPaidTier } from '@/lib/tier';
import type { SessionUser } from '@/api/types';

// subscriptionEnding drives the Home Base "your plan ends in X days" countdown. It must fire
// ONLY for a cancelled account still inside the period it paid for (cancel-at-period-end), and
// stay silent for every other account so no normal user ever sees an ending banner.
function userWith(sub: Partial<NonNullable<SessionUser['subscription']>>): SessionUser {
  return { userid: 'u', subscription: sub };
}

describe('subscriptionEnding', () => {
  it('fires for a cancelled account still in its paid period', () => {
    const end = new Date(Date.now() + 5 * 86400_000).toISOString();
    const r = subscriptionEnding(
      userWith({ status: 'canceled', in_paid_period: true, days_left: 5, subscription_end_at: end }),
    );
    expect(r).toEqual({ days: 5, endAt: end });
  });

  it('is null once the cancelled period has lapsed (now a Free account)', () => {
    expect(
      subscriptionEnding(userWith({ status: 'canceled', in_paid_period: false, days_left: 0 })),
    ).toBeNull();
  });

  it('is null for an active paid subscription', () => {
    expect(
      subscriptionEnding(userWith({ status: 'active', in_paid_period: true, ai_tier: 'paid' })),
    ).toBeNull();
  });

  it('is null for a Free account and for no user', () => {
    expect(subscriptionEnding(userWith({ status: 'free', in_paid_period: false }))).toBeNull();
    expect(subscriptionEnding(null)).toBeNull();
    expect(subscriptionEnding(undefined)).toBeNull();
  });

  it('clamps a missing/negative day count to 0', () => {
    expect(
      subscriptionEnding(userWith({ status: 'canceled', in_paid_period: true }))?.days,
    ).toBe(0);
    expect(
      subscriptionEnding(userWith({ status: 'canceled', in_paid_period: true, days_left: -3 }))?.days,
    ).toBe(0);
  });

  it('a cancelled-but-still-paid account is still Paid tier (banner replaces the Free strip)', () => {
    expect(isPaidTier(userWith({ status: 'canceled', in_paid_period: true }))).toBe(true);
  });
});
