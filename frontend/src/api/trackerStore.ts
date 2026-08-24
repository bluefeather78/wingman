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
  const raw = await httpClient.loadData<string | Record<string, unknown>>(TRACKER_KEY);
  if (!raw) return emptyData();
  let parsed: Record<string, unknown>;
  try {
    parsed = typeof raw === 'string' ? JSON.parse(raw) : (raw as Record<string, unknown>);
  } catch {
    return emptyData();
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
  return data;
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
