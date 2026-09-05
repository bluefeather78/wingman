import { describe, it, expect } from 'vitest';
import { applyDeadlineToTrackerItem, type TrackerItem, type ImportantDate } from '@/api/trackerStore';
import type { TrackerInfo } from '@/lib/tracker';

// frontend_report finding 9: "Add path sorts importantDates; refresh/sync path does not, yet
// carries googleEventId by INDEX → after a refresh whose order differs, calendar event ids
// attach to the wrong date and `changed` flips true on order alone."
//
// applyDeadlineToTrackerItem is shared by the PAID refresh (the button) and the FREE catalog
// sync, deliberately, so the two apply identical merge rules. Both were wrong in the same way.

function item(dates: Partial<ImportantDate>[] = []): TrackerItem {
  return { id: 'ec1', name: 'X', importantDates: dates } as unknown as TrackerItem;
}

// 'cached' and 'fresh, real search' are the two VERIFIED_DEADLINE_SOURCES (src/lib/tracker.ts);
// anything else is an unverified echo whose empty date list must not clear good data.
function info(dates: Array<Partial<{ date_iso: string; label: string; type: string }>>,
              source = 'fresh, real search'): Partial<TrackerInfo> {
  return { source, important_dates: dates } as unknown as Partial<TrackerInfo>;
}

describe('the refresh path sorts, exactly like the add path', () => {
  it('stores dates in ascending order whatever order they arrive in', () => {
    const it_ = item();
    applyDeadlineToTrackerItem(it_, info([
      { date_iso: '2026-12-01', label: 'Program starts' },
      { date_iso: '2026-10-15', label: 'Deadline' },
      { date_iso: '2026-11-01', label: 'Decisions' },
    ]));
    expect(it_.importantDates?.map((d) => d.dateISO))
      .toEqual(['2026-10-15', '2026-11-01', '2026-12-01']);
  });

  it('does not report a change when only the ARRIVAL order differs', () => {
    // This is the `changed` half of the finding: an unsorted merge compared a re-ordered list
    // against the stored one with JSON.stringify and called it a change, so every refresh
    // marked the item updated and re-rendered the card for nothing.
    const it_ = item();
    applyDeadlineToTrackerItem(it_, info([
      { date_iso: '2026-10-15', label: 'Deadline' },
      { date_iso: '2026-12-01', label: 'Program starts' },
    ]));
    const changed = applyDeadlineToTrackerItem(it_, info([
      { date_iso: '2026-12-01', label: 'Program starts' },
      { date_iso: '2026-10-15', label: 'Deadline' },
    ]));
    expect(changed).toBe(false);
  });

  it('still reports a change when a date actually moves', () => {
    const it_ = item();
    applyDeadlineToTrackerItem(it_, info([{ date_iso: '2026-10-15', label: 'Deadline' }]));
    expect(applyDeadlineToTrackerItem(it_, info([{ date_iso: '2026-11-01', label: 'Deadline' }])))
      .toBe(true);
    expect(it_.importantDates?.[0].dateISO).toBe('2026-11-01');
  });
});

describe('Google Calendar event ids follow the MILESTONE, not the slot', () => {
  it('keeps an event id attached to its own milestone when the order changes', () => {
    const it_ = item([
      { dateISO: '2026-10-15', label: 'Deadline', type: 'deadline', googleEventId: 'evt-deadline' },
      { dateISO: '2026-12-01', label: 'Program starts', type: 'event', googleEventId: 'evt-start' },
    ]);
    // A refresh that returns the same two milestones in the other order.
    applyDeadlineToTrackerItem(it_, info([
      { date_iso: '2026-12-01', label: 'Program starts', type: 'event' },
      { date_iso: '2026-10-15', label: 'Deadline', type: 'deadline' },
    ]));
    const byLabel = Object.fromEntries(
      (it_.importantDates ?? []).map((d) => [d.label, d.googleEventId]));
    expect(byLabel).toEqual({ Deadline: 'evt-deadline', 'Program starts': 'evt-start' });
  });

  it('keeps the event id when the milestone MOVES — the refresh that matters', () => {
    // Keying the carry on the DATE would lose the id here and the next sync would create a
    // second calendar event for the same deadline. The label is the identity; the date is the
    // thing a refresh exists to change.
    const it_ = item([
      { dateISO: '2026-10-15', label: 'Deadline', type: 'deadline', googleEventId: 'evt-1' },
    ]);
    applyDeadlineToTrackerItem(it_, info([
      { date_iso: '2026-11-20', label: 'Deadline', type: 'deadline' },
    ]));
    expect(it_.importantDates?.[0]).toMatchObject({
      dateISO: '2026-11-20', googleEventId: 'evt-1',
    });
  });

  it('does not hand an event id to a milestone that never had one', () => {
    const it_ = item([
      { dateISO: '2026-10-15', label: 'Deadline', type: 'deadline', googleEventId: 'evt-1' },
    ]);
    applyDeadlineToTrackerItem(it_, info([
      { date_iso: '2026-09-01', label: 'Early deadline', type: 'deadline' },
      { date_iso: '2026-10-15', label: 'Deadline', type: 'deadline' },
    ]));
    const byLabel = Object.fromEntries(
      (it_.importantDates ?? []).map((d) => [d.label, d.googleEventId]));
    expect(byLabel).toEqual({ 'Early deadline': null, Deadline: 'evt-1' });
  });

  it('pairs two milestones sharing a label in order rather than both taking the first id', () => {
    const it_ = item([
      { dateISO: '2026-10-01', label: 'Deadline', type: 'deadline', googleEventId: 'evt-a' },
      { dateISO: '2026-11-01', label: 'Deadline', type: 'deadline', googleEventId: 'evt-b' },
    ]);
    applyDeadlineToTrackerItem(it_, info([
      { date_iso: '2026-10-01', label: 'Deadline', type: 'deadline' },
      { date_iso: '2026-11-01', label: 'Deadline', type: 'deadline' },
    ]));
    expect(it_.importantDates?.map((d) => d.googleEventId)).toEqual(['evt-a', 'evt-b']);
  });

  it('drops the id when the milestone is gone, so the sweep can remove its event', () => {
    const it_ = item([
      { dateISO: '2026-10-15', label: 'Deadline', type: 'deadline', googleEventId: 'evt-1' },
    ]);
    applyDeadlineToTrackerItem(it_, info([
      { date_iso: '2026-11-01', label: 'Decisions', type: 'event' },
    ]));
    expect(it_.importantDates?.map((d) => d.googleEventId)).toEqual([null]);
  });
});

describe('malformed dates never reach the stored item', () => {
  it('drops them on the refresh path', () => {
    const it_ = item();
    applyDeadlineToTrackerItem(it_, info([
      { date_iso: 'TBD', label: 'Deadline' },
      { date_iso: '2026-13-45', label: 'Also bad' },
      { date_iso: '2026-10-15', label: 'Deadline' },
    ]));
    expect(it_.importantDates?.map((d) => d.dateISO)).toEqual(['2026-10-15']);
  });
});

describe('what must NOT have changed', () => {
  it('an unverified EMPTY list never clears a good snapshot', () => {
    // The `source` gate: a mock or fallback echo must not be able to wipe real dates.
    const it_ = item([{ dateISO: '2026-10-15', label: 'Deadline', type: 'deadline' }]);
    applyDeadlineToTrackerItem(it_, info([], 'mock'));
    expect(it_.importantDates?.map((d) => d.dateISO)).toEqual(['2026-10-15']);
  });

  it('a VERIFIED empty list does clear one — a rolling programme has no dates', () => {
    const it_ = item([{ dateISO: '2026-10-15', label: 'Deadline', type: 'deadline' }]);
    const changed = applyDeadlineToTrackerItem(it_, info([], 'cached'));
    expect(it_.importantDates).toEqual([]);
    expect(changed).toBe(true);
  });

  it('carries status and the estimate flag through', () => {
    const it_ = item();
    applyDeadlineToTrackerItem(it_, {
      ...info([{ date_iso: '2026-10-15', label: 'Deadline' }]),
      status: 'not_running',
      was_estimated: true,
    } as unknown as Partial<TrackerInfo>);
    expect(it_.status).toBe('not_running');
    expect(it_.wasEstimated).toBe(true);
  });
});
