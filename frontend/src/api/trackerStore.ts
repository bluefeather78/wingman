import type { Bucket } from '@/lib/constants';
import { httpClient } from './httpClient';

// Server-persisted tracker items (via the gated data key/value store). One flat list keyed
// by opportunity id; the Tracker screen groups it by bucket. This is the RN equivalent of
// the old script.js tracker state, now server-backed rather than window.storage.
const TRACKER_KEY = 'rn-tracker-items';

export interface TrackerItem {
  oppId: string;
  bucket: Bucket;
  name: string;
  org?: string | null;
  url?: string | null;
  summary?: string | null;
  reason?: string;
}

export async function loadTrackerItems(): Promise<TrackerItem[]> {
  const items = await httpClient.loadData<TrackerItem[]>(TRACKER_KEY);
  return Array.isArray(items) ? items : [];
}

// Add (idempotent by oppId) and persist. Returns the updated list.
export async function addTrackerItem(item: TrackerItem): Promise<TrackerItem[]> {
  const items = await loadTrackerItems();
  if (items.some((i) => i.oppId === item.oppId)) return items;
  const next = [...items, item];
  await httpClient.saveData(TRACKER_KEY, next);
  return next;
}

export async function removeTrackerItem(oppId: string): Promise<TrackerItem[]> {
  const items = await loadTrackerItems();
  const next = items.filter((i) => i.oppId !== oppId);
  await httpClient.saveData(TRACKER_KEY, next);
  return next;
}
