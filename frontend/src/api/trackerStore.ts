import type { Bucket } from '@/lib/constants';
import { ALL_BUCKETS } from '@/lib/constants';
import { httpClient } from './httpClient';

// The tracker is shared with the original web app: it persists under the SAME data key
// (`hs-tracker-data`) in the SAME shape — a JSON *string* of a 6-bucket object, each bucket
// an array of items. Reading/writing the same key means a student's existing tracked items
// show up here and stay in sync across both frontends during cutover.
const TRACKER_KEY = 'hs-tracker-data';

export interface ImportantDate {
  label: string;
  dateISO: string;
  type: string; // opens | deadline | event_start | event_end | other
  // Written back by the Google Calendar sync so the next run PATCHes the same event
  // instead of creating a duplicate. Same field, same meaning as the retired SPA's.
  googleEventId?: string | null;
}

export interface ActionItem {
  id: string;
  text: string;
  url: string | null;
  state: string;
}

export interface TrackerItem {
  id: string;
  name: string;
  url?: string | null;
  type?: string | null;
  bucket: Bucket;
  progressStatus?: string;
  status?: 'running' | 'not_running' | 'unknown' | string;
  reviewStatus?: string | null;
  reviewSummary?: string | null;
  meta?: string;
  fit?: string;
  note?: string;
  noteType?: string;
  importantDates?: ImportantDate[];
  deadlineLabel?: string;
  wasEstimated?: boolean;
  applyUrl?: string | null;
  applyLabel?: string | null;
  actionItems?: ActionItem[];
}

export type TrackerData = Record<Bucket, TrackerItem[]>;

function emptyData(): TrackerData {
  return {
    summerPrograms: [], internships: [], researchCompetitions: [],
    pureCompetitions: [], conferences: [], journals: [],
  };
}

export async function loadTrackerData(): Promise<TrackerData> {
  return (await loadTrackerDataChecked()).data;
}

// Same load, but says whether the stored value was UNREADABLE rather than merely absent.
// loadTrackerData() collapses those two into an empty tracker, which is right for rendering
// (an empty Quest Log either way) and wrong for the calendar sweep: sweeping on a corrupt
// payload would delete a student's synced deadlines because we could not parse our own data.
// A server/network failure needs no flag — httpClient.loadData throws and never reaches here.
export async function loadTrackerDataChecked(): Promise<{ data: TrackerData; unreadable: boolean }> {
  const raw = await httpClient.loadData<string | Record<string, unknown>>(TRACKER_KEY);
  if (!raw) return { data: emptyData(), unreadable: false };
  let parsed: Record<string, unknown>;
  try {
    parsed = typeof raw === 'string' ? JSON.parse(raw) : (raw as Record<string, unknown>);
  } catch {
    return { data: emptyData(), unreadable: true };
  }
  const data = emptyData();
  // Legacy 4-bucket migration mirror (old app did the same).
  if (Array.isArray((parsed as { competitions?: unknown }).competitions) && !Array.isArray(parsed.researchCompetitions)) {
    parsed.researchCompetitions = (parsed as { competitions: unknown[] }).competitions;
  }
  ALL_BUCKETS.forEach((b) => {
    const arr = parsed[b];
    if (Array.isArray(arr)) {
      data[b] = (arr as TrackerItem[]).map((it) => ({
        ...it,
        bucket: b,
        importantDates: Array.isArray(it.importantDates) ? it.importantDates : [],
        actionItems: Array.isArray(it.actionItems) ? it.actionItems : [],
      }));
    }
  });
  return { data, unreadable: false };
}

export async function saveTrackerData(data: TrackerData): Promise<void> {
  await httpClient.saveData(TRACKER_KEY, JSON.stringify(data));
}

// Saved-for-later flags — same key/shape as the old app (`hs-tracker-saved`, a JSON string
// of { [itemId]: boolean }). Saved items are NOT "actively tracked" anywhere counts appear.
const SAVED_KEY = 'hs-tracker-saved';
export type SavedState = Record<string, boolean>;

export async function loadTrackerSaved(): Promise<SavedState> {
  try {
    const raw = await httpClient.loadData<string | SavedState>(SAVED_KEY);
    if (!raw) return {};
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    return parsed && typeof parsed === 'object' ? (parsed as SavedState) : {};
  } catch {
    return {};
  }
}

export async function saveTrackerSaved(state: SavedState): Promise<void> {
  await httpClient.saveData(SAVED_KEY, JSON.stringify(state));
}

export function flattenItems(data: TrackerData): TrackerItem[] {
  return ALL_BUCKETS.flatMap((b) => data[b]);
}

export interface DeadlineRefreshResult {
  data: TrackerData;
  checked: number;
  updated: number;
  failed: number;
}

// Quest Log's "Check for updates" button — ported from script.js's refreshTracker(), minus
// the extractTrackerInfo() re-classification pass (a separate, heavier AI call this RN port
// never wired up elsewhere). This overlays the same shared/cached on-demand deadline check
// buildTracker() already uses (getDeadlineCheck -> GET /api/opportunities/<id>/deadline),
// which no-ops into a cheap cache hit for anything checked by any user in the last 7 days.
export async function refreshTrackerDeadlines(
  onProgress?: (checked: number, total: number) => void,
): Promise<DeadlineRefreshResult> {
  const data = await loadTrackerData();
  const items = flattenItems(data);
  let updated = 0;
  let failed = 0;
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    onProgress?.(i, items.length);
    try {
      const info = await httpClient.getDeadlineCheck(item.id);
      if (!info) continue;
      let changed = false;
      if (info.status && ['running', 'not_running', 'unknown'].includes(info.status) && info.status !== item.status) {
        item.status = info.status;
        changed = true;
      }
      if (Array.isArray(info.important_dates) && info.important_dates.length) {
        const mapped = info.important_dates
          .filter((d) => d && d.date_iso)
          .map((d) => ({ label: d.label || 'Date', dateISO: d.date_iso, type: d.type || 'deadline' }));
        if (JSON.stringify(mapped) !== JSON.stringify(item.importantDates ?? [])) changed = true;
        item.importantDates = mapped;
      }
      if (typeof info.was_estimated === 'boolean') item.wasEstimated = info.was_estimated;
      if (info.important_date_note) item.note = info.important_date_note;
      if (changed) updated++;
    } catch {
      failed++;
    }
  }
  onProgress?.(items.length, items.length);
  await saveTrackerData(data);
  return { data, checked: items.length, updated, failed };
}

export function countItems(data: TrackerData): number {
  return ALL_BUCKETS.reduce((n, b) => n + data[b].length, 0);
}

function existsAcross(data: TrackerData, id: string, url?: string | null): boolean {
  return ALL_BUCKETS.some((b) => data[b].some((i) => i.id === id || (!!url && i.url === url)));
}

// Add (idempotent by id/url across all buckets) and persist. Returns the updated data.
export async function addTrackerItem(bucket: Bucket, item: TrackerItem): Promise<TrackerData> {
  const data = await loadTrackerData();
  if (existsAcross(data, item.id, item.url)) return data;
  data[bucket] = [...data[bucket], { ...item, bucket }];
  await saveTrackerData(data);
  return data;
}

export async function removeTrackerItem(id: string): Promise<TrackerData> {
  const data = await loadTrackerData();
  ALL_BUCKETS.forEach((b) => {
    data[b] = data[b].filter((i) => i.id !== id);
  });
  await saveTrackerData(data);
  return data;
}

export async function updateTrackerItem(id: string, patch: Partial<TrackerItem>): Promise<TrackerData> {
  const data = await loadTrackerData();
  ALL_BUCKETS.forEach((b) => {
    data[b] = data[b].map((i) => (i.id === id ? { ...i, ...patch } : i));
  });
  await saveTrackerData(data);
  return data;
}
