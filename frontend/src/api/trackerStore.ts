import type { Bucket } from '@/lib/constants';
import { httpClient } from './httpClient';

// Server-persisted tracker items (via the gated data key/value store). One flat list keyed
// by opportunity id; the Tracker screen groups it by bucket and reads saved dates into a
// calendar. This is the RN equivalent of the old script.js tracker state, server-backed.
const TRACKER_KEY = 'rn-tracker-items';

export interface TrackedDate {
  label: string;
  dateISO: string;
  type: string;
}

export interface TrackerItem {
  oppId: string;
  bucket: Bucket;
  name: string;
  org?: string | null;
  url?: string | null;
  summary?: string | null;
  reason?: string;
  // Filled in when the student runs a deadline check; drives the calendar view.
  status?: string;
  dates?: TrackedDate[];
  checkedAt?: string;
}

export async function loadTrackerItems(): Promise<TrackerItem[]> {
  const items = await httpClient.loadData<TrackerItem[]>(TRACKER_KEY);
  return Array.isArray(items) ? items : [];
}

async function save(items: TrackerItem[]): Promise<void> {
  await httpClient.saveData(TRACKER_KEY, items);
}

// Add (idempotent by oppId) and persist. Returns the updated list.
export async function addTrackerItem(item: TrackerItem): Promise<TrackerItem[]> {
  const items = await loadTrackerItems();
  if (items.some((i) => i.oppId === item.oppId)) return items;
  const next = [...items, item];
  await save(next);
  return next;
}

export async function removeTrackerItem(oppId: string): Promise<TrackerItem[]> {
  const items = await loadTrackerItems();
  const next = items.filter((i) => i.oppId !== oppId);
  await save(next);
  return next;
}

// Merge a patch (e.g. a deadline-check result) into one item and persist.
export async function updateTrackerItem(oppId: string, patch: Partial<TrackerItem>): Promise<TrackerItem[]> {
  const items = await loadTrackerItems();
  const next = items.map((i) => (i.oppId === oppId ? { ...i, ...patch } : i));
  await save(next);
  return next;
}
