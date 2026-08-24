import type { Bucket } from '@/lib/constants';
import { ALL_BUCKETS } from '@/lib/constants';
import { httpClient } from './httpClient';
import { isVerifiedDeadlineSource } from '@/lib/tracker';

// The tracker is shared with the original web app: it persists under the SAME data key
// (`hs-tracker-data`) in the SAME shape — a JSON *string* of a 6-bucket object, each bucket
// an array of items. Reading/writing the same key means a student's existing tracked items
// show up here and stay in sync across both frontends during cutover.
const TRACKER_KEY = 'hs-tracker-data';

export interface ImportantDate {
  label: string;
  dateISO: string;
  type: string; // opens | deadline | event_start | event_end | other
  // Whether THIS date was estimated (prior cycle / interval / vague pattern) rather than
  // read off a current-cycle page. Undefined on rows predating 2026-08-24 — unknown, not
  // confirmed. See status.ts getDisplayMilestones for how it reaches the card.
  estimated?: boolean;
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
  return parseTrackerData(await httpClient.loadData<string | Record<string, unknown>>(TRACKER_KEY));
}

// The stored-value -> TrackerData step on its own, so the cached-value path (peekTrackerData)
// and the fetched one cannot drift in how they migrate legacy buckets or coerce arrays.
function parseTrackerData(raw: string | Record<string, unknown> | null): { data: TrackerData; unreadable: boolean } {
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
    return parseSaved(await httpClient.loadData<string | SavedState>(SAVED_KEY));
  } catch {
    return {};
  }
}

function parseSaved(raw: string | SavedState | null | undefined): SavedState {
  if (!raw) return {};
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    return parsed && typeof parsed === 'object' ? (parsed as SavedState) : {};
  } catch {
    return {};
  }
}

// --- Cached reads ----------------------------------------------------------
// The last values the client already fetched, parsed the same way, with no network. Both
// return undefined when nothing has been loaded yet, which is what lets a screen tell
// "no data" apart from "not fetched" — the distinction Home Base's spinner turns on.
export function peekTrackerData(): TrackerData | undefined {
  const raw = httpClient.peekData<string | Record<string, unknown>>(TRACKER_KEY);
  return raw === undefined ? undefined : parseTrackerData(raw).data;
}

export function peekTrackerSaved(): SavedState | undefined {
  const raw = httpClient.peekData<string | SavedState>(SAVED_KEY);
  return raw === undefined ? undefined : parseSaved(raw);
}

export async function saveTrackerSaved(state: SavedState): Promise<void> {
  await httpClient.saveData(SAVED_KEY, JSON.stringify(state));
}

export function flattenItems(data: TrackerData): TrackerItem[] {
  return ALL_BUCKETS.flatMap((b) => data[b]);
}

export interface DeadlineRefreshResult {
  data: TrackerData;
  /** Items we got a real answer for. NOT the number of tracked items. */
  checked: number;
  updated: number;
  /** Tracked items with no catalog row behind them — they cannot be auto-checked. */
  skipped: number;
  /** Refused by the subscription gate (402). */
  blocked: number;
  failed: number;
  /** The session expired mid-run, so the sweep stopped early. */
  signedOut: boolean;
  /** Total items considered, i.e. checked + skipped + blocked + failed. */
  total: number;
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
  let checked = 0;
  let updated = 0;
  let skipped = 0;
  let blocked = 0;
  let failed = 0;
  let signedOut = false;
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    onProgress?.(i, items.length);
    // getDeadlineCheckResult, not getDeadlineCheck: the caller reports back to the student,
    // so it has to know whether an empty answer means "nothing changed", "this item has no
    // catalog row", "your trial lapsed" or "the network failed". Collapsing those into null
    // is what let this report "no changes found" for items it never checked at all.
    const res = await httpClient.getDeadlineCheckResult(item.id);
    if (res.outcome !== 'ok' || !res.info) {
      if (res.outcome === 'not-found') {
        skipped++;
      } else if (res.outcome === 'blocked') {
        blocked++;
      } else if (res.outcome === 'auth') {
        // The session is gone, so every remaining item would fail the same way. Stop and
        // say so rather than grinding through the rest and reporting them as failures.
        signedOut = true;
        break;
      } else {
        failed++;
      }
      continue;
    }
    checked++;
    const info = res.info;
    let changed = false;
    if (info.status && ['running', 'not_running', 'unknown'].includes(info.status) && info.status !== item.status) {
      item.status = info.status;
      changed = true;
    }
    // Same source gate as applyDeadlineCheckToInfo: an empty list may only overwrite when
    // the answer was actually verified, so a discontinued program's dates can be cleared
    // while a mock or fallback echo can never wipe good ones.
    if (Array.isArray(info.important_dates)
        && (isVerifiedDeadlineSource(info.source) || info.important_dates.length)) {
      const previous = item.importantDates ?? [];
      const mapped = info.important_dates
        .filter((d) => d && d.date_iso)
        .map((d, idx) => ({
          label: d.label || 'Date',
          dateISO: d.date_iso,
          type: d.type || 'deadline',
          estimated: d.estimated,
          // Carry the Google Calendar event id forward. Dropping it made the next sync
          // POST a NEW event, while the old one — still carrying the same index-based
          // wingmanId — was not an orphan the sweep would remove, so the student's real
          // calendar gained a duplicate entry on every single refresh. Matching by index
          // is what keeps this consistent: the wingmanId IS `${item.id}::${index}`, so
          // slot N's event is by definition the event for slot N's date.
          googleEventId: previous[idx]?.googleEventId ?? null,
        }));
      if (JSON.stringify(mapped) !== JSON.stringify(previous)) changed = true;
      item.importantDates = mapped;
    }
    if (typeof info.was_estimated === 'boolean') item.wasEstimated = info.was_estimated;
    if (info.important_date_note) item.note = info.important_date_note;
    if (changed) updated++;
  }
  onProgress?.(items.length, items.length);
  await saveTrackerData(data);
  return { data, checked, updated, skipped, blocked, failed, signedOut, total: items.length };
}

export function countItems(data: TrackerData): number {
  return ALL_BUCKETS.reduce((n, b) => n + data[b].length, 0);
}

function existsAcross(data: TrackerData, id: string, url?: string | null): boolean {
  return !!findAcross(data, id, url);
}

// The item already holding this id or url, if any — the same test existsAcross makes, but
// it hands back WHICH item, so a caller can name it instead of just refusing.
function findAcross(data: TrackerData, id: string, url?: string | null): TrackerItem | null {
  for (const b of ALL_BUCKETS) {
    const hit = data[b].find((i) => i.id === id || (!!url && i.url === url));
    if (hit) return hit;
  }
  return null;
}

export interface AddTrackerResult {
  data: TrackerData;
  /** False when the item was already tracked (by id OR by url) and nothing was written. */
  added: boolean;
  /** The item that blocked the add, so the caller can say WHAT it collided with. */
  existing: TrackerItem | null;
}

// Add (idempotent by id/url across all buckets) and persist, reporting whether it actually
// wrote anything. The plain addTrackerItem() below cannot say — and Fresh Finds believed it
// always succeeded, so an opportunity sharing a URL with something already tracked was
// silently dropped while still being marked tracked and badged NEW in the Quest Log.
// (Named like loadTrackerDataChecked: same "…Checked variant tells you what the plain one
// swallows" idiom.)
export async function addTrackerItemChecked(
  bucket: Bucket,
  item: TrackerItem,
): Promise<AddTrackerResult> {
  const data = await loadTrackerData();
  const existing = findAcross(data, item.id, item.url);
  if (existing) return { data, added: false, existing };
  data[bucket] = [...data[bucket], { ...item, bucket }];
  await saveTrackerData(data);
  return { data, added: true, existing: null };
}

// Add (idempotent by id/url across all buckets) and persist. Returns the updated data.
export async function addTrackerItem(bucket: Bucket, item: TrackerItem): Promise<TrackerData> {
  return (await addTrackerItemChecked(bucket, item)).data;
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
