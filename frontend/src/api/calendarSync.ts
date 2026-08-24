import { ALL_BUCKETS } from '@/lib/constants';
import { addYearsISO, cycleYearShift } from '@/lib/status';
import { httpClient } from './httpClient';
import {
  loadTrackerDataChecked,
  loadTrackerSaved,
  saveTrackerData,
  type TrackerData,
  type TrackerItem,
} from './trackerStore';
import type { CalendarSyncEvent } from './ApiClient';

// Google Calendar sync for the Quest Log — the RN port of the retired SPA's
// syncToGoogleCalendar()/collectTrackedDeadlineEvents(), plus the removal half it never had.
//
// The removal design is a SWEEP, not a tombstone list: every sync sends the full set of
// currently-tracked deadlines and asks the server to delete any event it previously wrote
// whose id isn't in that set. Nothing has to remember the Google event id of an item the
// student has already deleted (removeTrackerItem discards the item and its ids together),
// and it self-heals — a removal made on another device, or while offline, is reconciled by
// the next sync from anywhere.
//
// As of 2026-08-24 the sweep MIRRORS the Quest Log: it deletes every event on the Wingman
// calendar that is not currently tracked, whether or not it carries our `wingmanId` marker.
// It used to spare unmarked events to protect anything the student had added there by hand,
// but the marker only started being written on 2026-08-22, so every older event was
// permanently unremovable - 45 events for 17 tracked dates on the first real account. The
// consequence is real and deliberate: anything added to THAT calendar by hand is removed on
// the next sync. Events only ever land on the app-created "Highschool Wingman" calendar, not
// the student's primary one - the calendar.app.created scope makes anything else impossible.

export interface CollectedEvent extends CalendarSyncEvent {
  itemId: string;
  dateIdx: number;
}

// Only actively-tracked items (saved-for-later is not "actively tracked" anywhere counts
// appear) with a real ISO date. Everything not collected here is what the sweep removes.
export function collectTrackedDeadlineEvents(data: TrackerData, saved: Record<string, boolean>): CollectedEvent[] {
  const events: CollectedEvent[] = [];
  ALL_BUCKETS.forEach((bucket) => {
    data[bucket].forEach((item: TrackerItem) => {
      if (saved[item.id]) return;
      // Discontinued programs are excluded from every count and list in status.ts, and they
      // must be excluded here too. cycleYearShift deliberately does not project a next cycle
      // for `not_running`, so these carry REAL past dates — syncing them puts dead deadlines
      // for a cancelled program on the student's actual calendar. Leaving them out also
      // means the sweep takes any already-synced ones back off, since they stop appearing in
      // the tracked set. The Quest Log still shows the card, flagged "Not running".
      if (item.status === 'not_running') return;
      // Same next-cycle projection the app itself shows (cycleYearShift): syncing last
      // cycle's dead dates would put a passed deadline on the student's real calendar and
      // disagree with every date in the Quest Log. The event id stays `${item.id}::${idx}`,
      // so a shifted date PATCHes the existing event rather than creating a second one.
      const shift = cycleYearShift(item);
      (item.importantDates ?? []).forEach((d, idx) => {
        // Both spellings exist in stored data — the deadline endpoint speaks date_iso.
        const stored = (d as { dateISO?: string; date_iso?: string }).dateISO
          || (d as { date_iso?: string }).date_iso;
        if (!stored) return;
        const dateISO = shift ? addYearsISO(stored, shift) : stored;
        const label = shift
          ? `${d.label || 'Deadline'} (predicted from last cycle — confirm on the program site)`
          : d.label || 'Deadline';
        const org = orgOf(item);
        events.push({
          itemId: item.id,
          dateIdx: idx,
          id: `${item.id}::${idx}`,
          title: org ? `${item.name} (${org})` : item.name,
          description: item.url ? `${label}\nURL: ${item.url}` : label,
          dateISO,
          googleEventId: d.googleEventId ?? null,
        });
      });
    });
  });
  return events;
}

// TrackerItem has no `org` column of its own in the RN port — the finder folds it into the
// leading segment of `meta` ("org · type · price · location"). Items carried over from the
// retired SPA do still have a real `org` key (loadTrackerData spreads unknown keys through),
// so prefer that. The ' · ' test is what stops a meta line that is really a summary — set
// when org/type/price/location were all empty — from being read as an organization name.
function orgOf(item: TrackerItem): string {
  const legacy = (item as { org?: string }).org;
  if (legacy) return legacy;
  const meta = item.meta ?? '';
  return meta.includes(' · ') ? meta.split(' · ')[0].trim() : '';
}

export type SyncOutcome =
  | { kind: 'ok'; synced: number; failed: number; removed: number; deduped: number;
      sweepErrors: string[]; calendarName: string; calendarLink: string }
  | { kind: 'not-connected' }
  | { kind: 'error'; message: string };

export async function syncTrackerToCalendar(): Promise<SyncOutcome> {
  const [loaded, saved] = await Promise.all([loadTrackerDataChecked(), loadTrackerSaved()]);
  // Refuse outright rather than sweeping against a tracker we could not read: an unreadable
  // payload looks identical to an empty one, and sweeping on it would delete every synced
  // deadline. Failing loudly is recoverable; a wiped calendar is not.
  if (loaded.unreadable) {
    return { kind: 'error', message: 'Your tracked opportunities could not be read, so nothing was synced.' };
  }
  const { data } = loaded;
  const events = collectTrackedDeadlineEvents(data, saved);

  // An EMPTY list is meaningful with sweep on: "nothing is tracked any more, clear it".
  // That is precisely the case where an untracked opportunity's deadline is still sitting
  // on the calendar, so it must not short-circuit.
  const res = await httpClient.syncCalendar(
    events.map(({ itemId: _itemId, dateIdx: _dateIdx, ...e }) => e),
    true,
  );
  if (!res.ok) {
    if (res.notConnected) return { kind: 'not-connected' };
    return { kind: 'error', message: res.error };
  }

  // Write each event id back onto the stored date so the next sync PATCHes in place.
  const byKey = new Map(res.results.map((r) => [r.id, r]));
  let synced = 0;
  let failed = 0;
  ALL_BUCKETS.forEach((bucket) => {
    data[bucket].forEach((item) => {
      (item.importantDates ?? []).forEach((d, idx) => {
        const r = byKey.get(`${item.id}::${idx}`);
        if (!r) return;
        if (r.status === 'ok') {
          d.googleEventId = r.googleEventId ?? null;
          synced++;
        } else {
          failed++;
        }
      });
    });
  });
  if (synced) await saveTrackerData(data);

  return {
    kind: 'ok', synced, failed, removed: res.deleted, deduped: res.deduped,
    sweepErrors: res.sweepErrors, calendarName: res.calendarName, calendarLink: res.calendarLink,
  };
}
