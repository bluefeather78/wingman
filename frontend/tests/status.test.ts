import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  isValidDateISO,
  storedDateISO,
  daysUntil,
  addYearsISO,
  cycleYearShift,
  computeProgressStatus,
  getDisplayMilestones,
  earliestUpcoming,
} from '@/lib/status';
import type { TrackerItem } from '@/api/trackerStore';

// frontend_report finding 11. Only TRUTHINESS was checked on a stored date, so "TBD",
// "2026-13-45", "Fall 2026" and "2026/11/01" — all of which a model can produce and a student
// can paste — made `new Date(...)` an Invalid Date and daysUntil return NaN.
//
// Every comparison against NaN is FALSE, which is why the failure was silent and specific:
// computeProgressStatus fell past both its guards to 'in_progress', so the card read
// "Happening Now"; getDisplayMilestones set isPast = (NaN < 0) = false, so a past date
// rendered as upcoming; and the calendar rendered the literal string NaN.

// A fixed "today" so the day arithmetic is not a function of when the suite runs.
const TODAY = new Date('2026-09-05T12:00:00');

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(TODAY);
});
afterEach(() => {
  vi.useRealTimers();
});

function item(dates: Array<Partial<{ dateISO: string; date_iso: string; label: string; type: string; googleEventId: string | null }>>,
              extra: Partial<TrackerItem> = {}): TrackerItem {
  return { id: 'x', name: 'X', importantDates: dates, ...extra } as unknown as TrackerItem;
}

describe('isValidDateISO', () => {
  it.each(['2026-09-05', '2026-01-01', '2026-12-31', '2024-02-29'])('accepts %s', (v) => {
    expect(isValidDateISO(v)).toBe(true);
  });

  it.each([
    'TBD', '', 'Fall 2026', '2026/11/01', '2026-11', 'x-y-z',
    '2026-13-45',        // month 13
    '2026-02-31',        // February has no 31st — the regex alone would pass this
    '2025-02-29',        // 2025 is not a leap year
    '2026-11-01T10:00:00Z',
  ])('rejects %s', (v) => {
    expect(isValidDateISO(v)).toBe(false);
  });

  it.each([null, undefined, 0, 20260905, {}, []])('rejects the non-string %s', (v) => {
    expect(isValidDateISO(v)).toBe(false);
  });
});

describe('storedDateISO — both spellings, and only valid values', () => {
  it('reads the client spelling and the deadline endpoint spelling', () => {
    expect(storedDateISO({ dateISO: '2026-09-05' })).toBe('2026-09-05');
    expect(storedDateISO({ date_iso: '2026-09-05' })).toBe('2026-09-05');
  });

  it('answers null for a malformed value in either spelling', () => {
    expect(storedDateISO({ dateISO: 'TBD' })).toBeNull();
    expect(storedDateISO({ date_iso: '2026-13-45' })).toBeNull();
    expect(storedDateISO({})).toBeNull();
    expect(storedDateISO(null)).toBeNull();
  });
});

describe('daysUntil', () => {
  it('counts whole days from today', () => {
    expect(daysUntil('2026-09-05')).toBe(0);
    expect(daysUntil('2026-09-12')).toBe(7);
    expect(daysUntil('2026-09-01')).toBe(-4);
  });

  it('answers null, not NaN, for a date it cannot parse', () => {
    // NaN is the whole bug: it compares false against everything, so every caller silently
    // took the wrong branch. null forces the caller to decide.
    expect(daysUntil('TBD')).toBeNull();
    expect(daysUntil('2026-13-45')).toBeNull();
    expect(Number.isNaN(daysUntil('TBD') as number)).toBe(false);
  });
});

describe('computeProgressStatus — the "Happening Now" bug', () => {
  it('does NOT read a malformed date as happening now', () => {
    expect(computeProgressStatus(item([{ date_iso: 'TBD', label: 'Deadline' }])))
      .toBe('not_started');
    expect(computeProgressStatus(item([{ date_iso: '2026-13-45', label: 'Deadline' }])))
      .toBe('not_started');
  });

  it('still classifies real dates exactly as before', () => {
    expect(computeProgressStatus(item([{ date_iso: '2026-10-01' }]))).toBe('not_started');
    expect(computeProgressStatus(item([{ date_iso: '2026-09-05' }]))).toBe('in_progress');
    expect(computeProgressStatus(item([{ date_iso: '2026-09-01' },
                                       { date_iso: '2026-09-10' }]))).toBe('in_progress');
  });

  it('ignores a malformed date sitting beside a real one', () => {
    expect(computeProgressStatus(item([{ date_iso: 'TBD' }, { date_iso: '2026-10-01' }])))
      .toBe('not_started');
  });

  it('leaves rolling and dateless items alone', () => {
    expect(computeProgressStatus(item([], { status: 'rolling' } as Partial<TrackerItem>)))
      .toBe('in_progress');
    expect(computeProgressStatus(item([]))).toBe('not_started');
  });
});

describe('getDisplayMilestones — a malformed date is dropped, not rendered', () => {
  it('drops it rather than showing it as upcoming', () => {
    const ms = getDisplayMilestones(item([
      { date_iso: 'TBD', label: 'Deadline' },
      { date_iso: '2026-10-01', label: 'Program starts' },
    ]));
    expect(ms.map((m) => m.date)).toEqual(['2026-10-01']);
  });

  it('marks a genuinely past date as past', () => {
    // NaN < 0 was false, so before the fix a past date rendered as upcoming.
    const ms = getDisplayMilestones(item([{ date_iso: '2026-09-01', label: 'Deadline' }],
                                         { status: 'not_running' } as Partial<TrackerItem>));
    expect(ms[0].isPast).toBe(true);
  });
});

describe('earliestUpcoming', () => {
  it('skips malformed entries', () => {
    const next = earliestUpcoming(item([
      { date_iso: 'TBD', label: 'Bad' },
      { date_iso: '2026-10-01', label: 'Deadline' },
    ]));
    expect(next?.date).toBe('2026-10-01');
  });

  it('answers null when nothing is usable', () => {
    expect(earliestUpcoming(item([{ date_iso: 'TBD' }]))).toBeNull();
  });

  it('falls back to the latest past date when nothing is upcoming', () => {
    const next = earliestUpcoming(item([{ date_iso: '2026-08-01', label: 'A' },
                                        { date_iso: '2026-09-01', label: 'B' }],
                                       { status: 'not_running' } as Partial<TrackerItem>));
    expect(next?.date).toBe('2026-09-01');
  });
});

describe('cycleYearShift — the projection, and the loop that must terminate', () => {
  it('does not project for a usable future date', () => {
    expect(cycleYearShift(item([{ date_iso: '2026-10-01' }]))).toBe(0);
  });

  it('rolls a fully-past cycle forward to its next occurrence', () => {
    expect(cycleYearShift(item([{ date_iso: '2026-01-15' }]))).toBe(1);
    expect(cycleYearShift(item([{ date_iso: '2024-01-15' }]))).toBe(3);
  });

  it('never projects for a discontinued or rolling programme', () => {
    expect(cycleYearShift(item([{ date_iso: '2020-01-15' }],
                               { status: 'not_running' } as Partial<TrackerItem>))).toBe(0);
    expect(cycleYearShift(item([{ date_iso: '2020-01-15' }],
                               { status: 'rolling' } as Partial<TrackerItem>))).toBe(0);
  });

  it('terminates on an item whose only date is malformed', () => {
    // The loop's exit condition depends on a date parse, and this runs on every render of
    // every card — a hang here is a frozen UI thread, not a wrong number.
    expect(cycleYearShift(item([{ date_iso: 'TBD' }]))).toBe(0);
  });
});

describe('addYearsISO — unchanged', () => {
  it('clamps Feb 29 into a non-leap year rather than rolling into March', () => {
    expect(addYearsISO('2024-02-29', 1)).toBe('2025-02-28');
  });

  it('is a plain year shift otherwise', () => {
    expect(addYearsISO('2026-01-15', 2)).toBe('2028-01-15');
  });
});
