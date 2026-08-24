import type { TrackerData, TrackerItem } from '@/api/trackerStore';
import { ALL_BUCKETS, type Bucket } from './constants';

// Ported verbatim from script.js — the single source of truth for opportunity event-timing
// status, display milestones, and the calendar's color assignment. Both frontends must
// classify identically or the same student sees different numbers on each.

export type OppStatus = 'not_started' | 'in_progress' | 'completed';

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

function itemDates(item: TrackerItem): string[] {
  return (item.importantDates ?? [])
    .map((d) => (d as { dateISO?: string; date_iso?: string }).dateISO || (d as { date_iso?: string }).date_iso)
    .filter(Boolean) as string[];
}

// script.js computeProgressStatus — including the recurring-program rule: a "running"
// program whose (estimated) dates are all past is a FUTURE event (next cycle coming).
export function computeProgressStatus(item: TrackerItem): OppStatus {
  if (item.status === 'not_running') return 'completed';
  const dates = itemDates(item);
  if (!dates.length) return 'not_started';
  dates.sort();
  const firstStep = dates[0];
  const lastStep = dates[dates.length - 1];
  if (daysUntil(firstStep) > 0) return 'not_started';
  if (daysUntil(lastStep) < 0) {
    if (item.status === 'running' && item.wasEstimated) return 'not_started';
    return 'completed';
  }
  return 'in_progress';
}

export interface Milestone {
  date: string;
  label: string;
  type: string;
  isPast: boolean;
}

// script.js getDisplayMilestones — dedupe (date,label), sort, flag past.
export function getDisplayMilestones(item: TrackerItem): Milestone[] {
  const seen = new Set<string>();
  const milestones: Milestone[] = [];
  (item.importantDates ?? []).forEach((d) => {
    const dateISO = (d as { dateISO?: string; date_iso?: string }).dateISO || (d as { date_iso?: string }).date_iso;
    if (!dateISO) return;
    const key = dateISO + '|' + (d.label || '');
    if (seen.has(key)) return;
    seen.add(key);
    milestones.push({ date: dateISO, label: d.label || 'Date', type: d.type || 'deadline', isPast: false });
  });
  milestones.sort((a, b) => a.date.localeCompare(b.date));
  milestones.forEach((m) => {
    m.isPast = daysUntil(m.date) < 0;
  });
  return milestones;
}

// script.js earliestUpcoming — nearest future date, else the latest past one.
export function earliestUpcoming(item: TrackerItem): { date: string; label: string; kind: string } | null {
  const candidates = (item.importantDates ?? [])
    .filter((d) => (d as { dateISO?: string; date_iso?: string }).dateISO || (d as { date_iso?: string }).date_iso)
    .map((d) => ({
      date: ((d as { dateISO?: string; date_iso?: string }).dateISO || (d as { date_iso?: string }).date_iso) as string,
      label: d.label,
      kind: d.type || 'deadline',
    }));
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

// Task (action-item) counts across a set of upcoming entries.
export function allTodoUnitCounts(upcoming: UpcomingEntry[]) {
  const counts: Record<OppStatus, number> = { not_started: 0, in_progress: 0, completed: 0 };
  let total = 0;
  upcoming.forEach(({ item }) => {
    (item.actionItems ?? []).forEach((ai) => {
      const st = (ai.state as OppStatus) in counts ? (ai.state as OppStatus) : 'not_started';
      counts[st]++;
      total++;
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
