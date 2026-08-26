import { isSetAsideTask, type TrackerData, type TrackerItem } from '@/api/trackerStore';
import { ALL_BUCKETS, type Bucket } from './constants';

// Ported verbatim from script.js — the single source of truth for opportunity event-timing
// status, display milestones, and the calendar's color assignment. Both frontends must
// classify identically or the same student sees different numbers on each.

export type OppStatus = 'not_started' | 'in_progress' | 'completed';
// Mirrors TaskStatus in src/ui/components.tsx — a task has the three opportunity states
// plus 'not_needed'. Mirrored rather than imported for the same reason OppStatus is: this
// module is pure logic and must not depend on the UI layer.
export type TaskStatus = OppStatus | 'not_needed';

export const BUCKET_LABELS: Record<Bucket, string> = {
  summerPrograms: 'Summer Program',
  internships: 'Internship',
  researchCompetitions: 'Research or Project Competition',
  pureCompetitions: 'Academic Competition',
  conferences: 'Conference',
  journals: 'Research Journal',
};

export function daysUntil(dateISO: string): number {
  const d = new Date(dateISO + 'T00:00:00');
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - now.getTime()) / 86400000);
}

function rawDates(item: TrackerItem): string[] {
  return (item.importantDates ?? [])
    .map((d) => (d as { dateISO?: string; date_iso?: string }).dateISO || (d as { date_iso?: string }).date_iso)
    .filter(Boolean) as string[];
}

// Feb 29 does not exist in a non-leap year — clamp to the 28th rather than letting the
// date roll into March, which would silently move a deadline.
export function addYearsISO(iso: string, years: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const ny = y + years;
  const lastDayOfMonth = new Date(Date.UTC(ny, m, 0)).getUTCDate();
  const nd = Math.min(d, lastDayOfMonth);
  return `${ny}-${String(m).padStart(2, '0')}-${String(nd).padStart(2, '0')}`;
}

// ---------- Next-cycle projection ----------
// A cycle that has entirely passed does NOT mean the opportunity is over: these programs
// recur annually, and the whole pipeline (check_deadlines, the intake/extract prompts)
// already treats "roll last cycle forward by a year" as the expected answer rather than a
// last resort. So when every known date is past, the client projects the same dates onto
// their next annual occurrence and reports THOSE everywhere — status pill, card dates,
// calendar, Home's next moves, and the sort order — instead of showing a dead cycle.
//
// The one exception is `not_running`: that is the deadline checker saying the program is
// discontinued, and predicting a next cycle for something that has genuinely ended would
// be inventing one. Those keep their real dates and still read as a Past Event.
//
// The shift is a single whole-year offset applied to EVERY date (the smallest that brings
// the last one back into the future), never a per-date roll — that preserves the ordering
// and the intervals between opens/deadline/event, which a per-date roll can distort when a
// cycle straddles a year boundary.
export function cycleYearShift(item: TrackerItem): number {
  // Neither a discontinued nor a rolling program gets a projected next cycle: not_running
  // has genuinely ended, and rolling has no cycle at all (it carries no dates to roll). A
  // rolling row has an empty date list so this returns 0 regardless, but naming it keeps the
  // intent explicit alongside not_running.
  if (item.status === 'not_running' || item.status === 'rolling') return 0;
  const dates = rawDates(item);
  if (!dates.length) return 0;
  const last = [...dates].sort()[dates.length - 1];
  if (daysUntil(last) >= 0) return 0;
  let n = Math.max(1, new Date().getFullYear() - Number(last.slice(0, 4)));
  while (daysUntil(addYearsISO(last, n)) < 0) n += 1;
  return n;
}

// True when this item's dates are a prediction rather than something we read off the page.
export function hasProjectedDates(item: TrackerItem): boolean {
  return cycleYearShift(item) > 0;
}

// Every reader of importantDates goes through this, so nothing can disagree about which
// cycle an item is in.
function itemDates(item: TrackerItem): string[] {
  const shift = cycleYearShift(item);
  const dates = rawDates(item);
  return shift ? dates.map((d) => addYearsISO(d, shift)) : dates;
}

// script.js computeProgressStatus — including the recurring-program rule: a program whose
// cycle has passed is a FUTURE event (next cycle coming), not a past one. Only a
// discontinued (`not_running`) program is ever completed.
export function computeProgressStatus(item: TrackerItem): OppStatus {
  if (item.status === 'not_running') return 'completed';
  // A rolling / always-open program is open RIGHT NOW with no cycle — it maps to in_progress
  // ("Happening Now" / apply anytime) rather than not_started, which is what a dateless row
  // would otherwise read as. This sits beside the not_running case deliberately: rolling has
  // no dates, so without this line it would fall through to the `!dates.length` → not_started
  // branch below and read as "not started yet", the exact backwards reading G3 fixes.
  if (item.status === 'rolling') return 'in_progress';
  const dates = itemDates(item);
  if (!dates.length) return 'not_started';
  dates.sort();
  const firstStep = dates[0];
  const lastStep = dates[dates.length - 1];
  if (daysUntil(firstStep) > 0) return 'not_started';
  if (daysUntil(lastStep) < 0) return 'completed';
  return 'in_progress';
}

export interface Milestone {
  date: string;
  label: string;
  type: string;
  isPast: boolean;
  // The date shown is last cycle's, rolled forward — see cycleYearShift.
  projected: boolean;
  // This specific date is a prediction, not something read off the program's page. True when
  // the deadline check flagged the entry itself, OR when the client projected the whole cycle
  // forward (a projected date is an estimate by construction, whatever the stored flag says).
  //
  // Per-DATE on purpose. The row-level `wasEstimated` only says "something on this card is a
  // guess", which on a mixed row either implies a confirmed deadline is a guess or lets a
  // guessed opening pass as fact — and the opening is exactly the date that decides whether
  // the card reads "Happening Now".
  estimated: boolean;
  // P6c: this date was found on a page the deadline check actually fetched, and sourceUrl is
  // that page — the per-date evidence link. undefined = unknown (written before the check
  // existed), which renders as nothing: absence of proof is never shown as proof. A
  // client-projected date (shift > 0) is forced false — the page carried LAST cycle's date,
  // not the one shown.
  verified?: boolean;
  sourceUrl?: string | null;
}

// script.js getDisplayMilestones — dedupe (date,label), sort, flag past.
export function getDisplayMilestones(item: TrackerItem): Milestone[] {
  const shift = cycleYearShift(item);
  const seen = new Set<string>();
  const milestones: Milestone[] = [];
  (item.importantDates ?? []).forEach((d) => {
    const stored = (d as { dateISO?: string; date_iso?: string }).dateISO || (d as { date_iso?: string }).date_iso;
    if (!stored) return;
    const dateISO = shift ? addYearsISO(stored, shift) : stored;
    const key = dateISO + '|' + (d.label || '');
    if (seen.has(key)) return;
    seen.add(key);
    milestones.push({
      date: dateISO,
      label: d.label || 'Date',
      type: d.type || 'deadline',
      isPast: false,
      projected: shift > 0,
      estimated: shift > 0 || (d as { estimated?: boolean }).estimated === true,
      // A projected date is by construction not the date any page carried, so the verified
      // marker (and its evidence link) must not survive the year-shift.
      verified: shift > 0 ? false : (d as { verified?: boolean }).verified,
      sourceUrl: shift > 0 ? null : ((d as { sourceUrl?: string | null }).sourceUrl ?? null),
    });
  });
  milestones.sort((a, b) => a.date.localeCompare(b.date));
  milestones.forEach((m) => {
    m.isPast = daysUntil(m.date) < 0;
  });
  return milestones;
}

// script.js earliestUpcoming — nearest future date, else the latest past one.
export function earliestUpcoming(item: TrackerItem): { date: string; label: string; kind: string } | null {
  const shift = cycleYearShift(item);
  const candidates = (item.importantDates ?? [])
    .filter((d) => (d as { dateISO?: string; date_iso?: string }).dateISO || (d as { date_iso?: string }).date_iso)
    .map((d) => {
      const stored = ((d as { dateISO?: string; date_iso?: string }).dateISO || (d as { date_iso?: string }).date_iso) as string;
      return {
        date: shift ? addYearsISO(stored, shift) : stored,
        label: d.label,
        kind: d.type || 'deadline',
      };
    });
  if (!candidates.length) return null;
  const future = candidates.filter((c) => daysUntil(c.date) >= 0);
  future.sort((a, b) => a.date.localeCompare(b.date));
  if (future.length) return future[0];
  candidates.sort((a, b) => a.date.localeCompare(b.date));
  return candidates[candidates.length - 1];
}

export type SavedState = Record<string, boolean>;

// script.js computeStats — saved-for-later is NOT "actively tracked".
export function computeStats(data: TrackerData, saved: SavedState) {
  const stats = { total: 0, not_started: 0, in_progress: 0, completed: 0 };
  ALL_BUCKETS.forEach((bucket) => {
    data[bucket].forEach((item) => {
      if (saved[item.id]) return;
      stats.total++;
      stats[computeProgressStatus(item)]++;
    });
  });
  return stats;
}

export interface UpcomingEntry {
  item: TrackerItem;
  bucket: Bucket;
  nextDate: string;
  nextLabel: string;
  nextKind: string;
}

// script.js getUpcomingDeadlineItems — due this month or next; excludes not_running & saved.
export function getUpcomingDeadlineItems(data: TrackerData, saved: SavedState): UpcomingEntry[] {
  const now = new Date();
  const thisMonthKey = now.getFullYear() * 12 + now.getMonth();
  const nextMonthKey = thisMonthKey + 1;
  const results: UpcomingEntry[] = [];
  ALL_BUCKETS.forEach((bucket) => {
    data[bucket].forEach((item) => {
      if (item.status === 'not_running') return;
      if (saved[item.id]) return;
      const next = earliestUpcoming(item);
      if (!next) return;
      const d = new Date(next.date + 'T00:00:00');
      const key = d.getFullYear() * 12 + d.getMonth();
      if (key === thisMonthKey || key === nextMonthKey) {
        results.push({ item, bucket, nextDate: next.date, nextLabel: next.label, nextKind: next.kind });
      }
    });
  });
  const order: Record<OppStatus, number> = { in_progress: 0, not_started: 1, completed: 2 };
  results.sort((a, b) => {
    const s = order[computeProgressStatus(a.item)] - order[computeProgressStatus(b.item)];
    if (s !== 0) return s;
    return a.nextDate.localeCompare(b.nextDate);
  });
  return results;
}

// script.js getBeyondDeadlineItems — due after next month.
export function getBeyondDeadlineItems(data: TrackerData, saved: SavedState): UpcomingEntry[] {
  const now = new Date();
  const nextMonthKey = now.getFullYear() * 12 + now.getMonth() + 1;
  const results: UpcomingEntry[] = [];
  ALL_BUCKETS.forEach((bucket) => {
    data[bucket].forEach((item) => {
      if (item.status === 'not_running') return;
      if (saved[item.id]) return;
      const next = earliestUpcoming(item);
      if (!next) return;
      const d = new Date(next.date + 'T00:00:00');
      const key = d.getFullYear() * 12 + d.getMonth();
      if (key > nextMonthKey) {
        results.push({ item, bucket, nextDate: next.date, nextLabel: next.label, nextKind: next.kind });
      }
    });
  });
  const order: Record<OppStatus, number> = { in_progress: 0, not_started: 1, completed: 2 };
  results.sort((a, b) => {
    const s = order[computeProgressStatus(a.item)] - order[computeProgressStatus(b.item)];
    if (s !== 0) return s;
    return a.nextDate.localeCompare(b.nextDate);
  });
  return results;
}

// Every actively-tracked opportunity, with no date window at all — what Home Base's task
// tracker shows. Same exclusions as getUpcomingDeadlineItems (not_running, saved-for-later),
// but an item with no dates on it is still listed, with an empty nextDate.
export function getAllDeadlineItems(data: TrackerData, saved: SavedState): UpcomingEntry[] {
  const results: UpcomingEntry[] = [];
  ALL_BUCKETS.forEach((bucket) => {
    data[bucket].forEach((item) => {
      if (item.status === 'not_running') return;
      if (saved[item.id]) return;
      const next = earliestUpcoming(item);
      results.push({
        item,
        bucket,
        nextDate: next?.date ?? '',
        nextLabel: next?.label ?? 'No date set',
        nextKind: next?.kind ?? 'deadline',
      });
    });
  });
  const order: Record<OppStatus, number> = { in_progress: 0, not_started: 1, completed: 2 };
  results.sort((a, b) => {
    const s = order[computeProgressStatus(a.item)] - order[computeProgressStatus(b.item)];
    if (s !== 0) return s;
    // Undated items sort last within a status band rather than ahead of every real date.
    if (!a.nextDate !== !b.nextDate) return a.nextDate ? -1 : 1;
    return a.nextDate.localeCompare(b.nextDate);
  });
  return results;
}

// Task (action-item) counts across a set of upcoming entries.
export function allTodoUnitCounts(upcoming: UpcomingEntry[]) {
  const counts: Record<TaskStatus, number> = { not_started: 0, in_progress: 0, completed: 0, not_needed: 0 };
  let total = 0;
  upcoming.forEach(({ item }) => {
    (item.actionItems ?? []).forEach((ai) => {
      const st = (ai.state as TaskStatus) in counts ? (ai.state as TaskStatus) : 'not_started';
      counts[st]++;
      // 'not_needed' is counted but kept OUT of the total, so it neither fills a segment of
      // the progress bar nor inflates the denominator the other three are drawn against —
      // and DUE SOON, which reads off these counts, ignores it. A student who says a step
      // does not apply to them and still sees it in "3 not started" has not been listened to.
      if (!isSetAsideTask(ai)) total++;
    });
  });
  return { counts, total };
}

// ---------- Calendar colors (script.js assignCalendarColors / hashColor) ----------
const GOLDEN_ANGLE = 137.508;
export interface CalColor {
  bg: string;
  border: string;
  text: string;
}
export function hashColor(seed: string): CalColor {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = seed.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash * GOLDEN_ANGLE) % 360;
  return { bg: `hsl(${hue}, 78%, 88%)`, border: `hsl(${hue}, 75%, 42%)`, text: `hsl(${hue}, 80%, 24%)` };
}
const CALENDAR_PALETTE_HUES = [210, 20, 150, 280, 45, 340, 170, 265, 5, 195, 320, 95, 240, 60, 300, 130];
const CALENDAR_PALETTE: CalColor[] = CALENDAR_PALETTE_HUES.map((hue) => ({
  bg: `hsl(${hue}, 78%, 88%)`,
  border: `hsl(${hue}, 75%, 42%)`,
  text: `hsl(${hue}, 80%, 24%)`,
}));
export function assignCalendarColors(venueIds: string[]): Map<string, CalColor> {
  const map = new Map<string, CalColor>();
  let next = 0;
  venueIds.forEach((id) => {
    if (map.has(id)) return;
    map.set(id, next < CALENDAR_PALETTE.length ? CALENDAR_PALETTE[next++] : hashColor(id));
  });
  return map;
}

export const MONTH_NAMES = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
const MONTHS_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// script.js shortDate — "APR 19" (uppercase short month + day, no year).
export function shortDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return `${MONTHS_ABBR[m - 1]} ${d}`.toUpperCase();
}

// script.js formatMonthDay — "Nov 13".
export function formatMonthDay(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return `${MONTHS_ABBR[m - 1]} ${d}`;
}
