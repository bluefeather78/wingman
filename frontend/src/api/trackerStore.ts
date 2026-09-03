import type { Bucket } from '@/lib/constants';
import { ALL_BUCKETS } from '@/lib/constants';
import { httpClient } from './httpClient';
import { isVerifiedDeadlineSource, normalizeVerifiedActionItems, type TrackerInfo } from '@/lib/tracker';

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
  // P6c date verification: this date was found (date-aware match) in the content of a page
  // the deadline check actually fetched — and sourceUrl is that page, the per-date evidence
  // link F2 asked for. An estimated/projected date is verified:false by design. Absent on
  // dates written before 2026-08-26: unknown, never rendered as verified.
  verified?: boolean;
  sourceUrl?: string | null;
  // Written back by the Google Calendar sync so the next run PATCHes the same event
  // instead of creating a duplicate. Same field, same meaning as the retired SPA's.
  googleEventId?: string | null;
}

// Where a task's CONTENT came from — the task equivalent of ImportantDate.estimated, and
// it exists for the same reason: the card cannot render a guess and a fact identically.
//   'page'    the extractor quoted the program's own page for a specific claim, and that
//             quote was checked against the fetched page text before the task was kept.
//   'generic' ordinary application logistics ("draft your essay") that asserts nothing
//             program-specific, so there is nothing to verify and nothing to get wrong.
export type ActionItemBasis = 'page' | 'generic';

export interface ActionItem {
  id: string;
  text: string;
  url: string | null;
  state: string;
  // ABSENT on every item written before 2026-08-24. Read as 'generic', never as 'page':
  // those were produced by a prompt that explicitly told the model to fill gaps with
  // "what's typical for this type of opportunity", i.e. to invent — which is how a
  // fabricated "Algebra 2 prerequisite" reached a real student's card. Unknown provenance
  // is not evidence of provenance. Same rule as ImportantDate.estimated above.
  basis?: ActionItemBasis;
  // The verbatim line from the program page that supports a 'page' task. Kept so the claim
  // stays auditable after the fact and so a re-check can re-verify it without re-asking a
  // model. Null/absent on 'generic' tasks.
  evidence?: string | null;
  // P7 trust gradient: which TIER of source the quote was verified against ('official' =
  // the program's own page/PDF, 'trusted' = an operator-approved guide), plus the evidence
  // link — the fetched page holding the quote, distinct from `url` (the step-action link).
  // Absent on generic tasks and on page-backed tasks written before P6b (those read as
  // official via taskTrustTier below — the urllib pipeline only ever read the program's
  // own page).
  sourceTier?: 'official' | 'trusted' | null;
  sourceUrl?: string | null;
  sourceDomain?: string | null;
  // LEGACY. Tasks a student dismissed before 2026-08-24, when dismissing hid a task
  // outright. That is now the 'not_needed' STATE instead — visible, reversible, and
  // countable — and parseTrackerData migrates this flag into it on load. Nothing writes
  // it any more; it stays declared so the migration has a typed field to read.
  dismissed?: boolean;
  // P10: where this task came from. Absent ⇒ 'catalog' (everything written before user
  // tasks existed). A 'user' task is the student's own: never page-backed by construction
  // (nothing verified it), rendered in its own group, and PRESERVED by mergeActionItems —
  // the catalog list regenerates and would otherwise drop it on every refresh.
  origin?: 'catalog' | 'user';
}

// The ONLY way to ask whether a task is page-backed. A bare `ai.basis === 'page'` scattered
// across call sites is how the legacy-undefined case eventually gets read as verified by
// one of them.
export function isPageBackedTask(ai: Pick<ActionItem, 'basis' | 'evidence'>): boolean {
  return ai.basis === 'page' && !!(ai.evidence && ai.evidence.trim());
}

// The ONLY way to ask which trust tier a task renders at — same rule as isPageBackedTask,
// which it builds on. Three answers, matching the three groups the Quest Log shows:
//   'official' page-backed, quote verified against the program's OWN page (or a legacy
//              pre-P6b page-backed task, whose pipeline only ever read the own page)
//   'trusted'  page-backed, quote verified against an operator-approved guide domain
//   'generic'  everything else — asserts nothing, "Typical steps — confirm on the site"
// A 'pending'/'blocked' tier never reaches here (the serve path and the normalizer both
// withhold it), but if one did, isPageBackedTask's basis test still bounds the damage.
export type TaskTrustTier = 'official' | 'trusted' | 'generic';

export function taskTrustTier(
  ai: Pick<ActionItem, 'basis' | 'evidence' | 'sourceTier'>,
): TaskTrustTier {
  if (!isPageBackedTask(ai)) return 'generic';
  return ai.sourceTier === 'trusted' ? 'trusted' : 'official';
}

// A task the student has stepped out of the way. Excluded from the progress bar and from
// DUE SOON — a student who says a step does not apply to them and still sees it counted in
// "3 not started" has not been listened to — but still RENDERED, which is the difference
// from the dismiss button this replaced: they can see what they set aside and undo it.
export function isSetAsideTask(ai: Pick<ActionItem, 'state'>): boolean {
  return ai.state === 'not_needed';
}

// The text-normalised identity every per-user task decision keys on — merge, tombstones,
// duplicate checks. Text is the key because there is no stable task id: catalog ids are
// positional (`${oppId}-t0`), so a re-generated list that drops one task shifts every id
// after it and would hand slot 2's completion to slot 3's task. The same positional-id trap
// the Google Calendar sync hit with importantDates.
export function taskKey(text: string): string {
  return (text || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

// Refreshing pulls the shared, re-verified checklist off the catalog row — which means the
// incoming list is the source of truth for what the CATALOG tasks are, and the stored one is
// the source of truth for what the student has DONE: their ticked-off state, the tasks they
// removed (`removedKeys`, the per-user tombstones on the tracker item), and the tasks they
// added themselves (`origin: 'user'`). Merge on task text via taskKey.
//
// The two P10 rules, both here so every refresh path (paid button, free sync, add-time
// overlay) enforces them identically:
//  - a tombstoned catalog task is dropped from the incoming list — deleting is a per-user
//    tombstone precisely because the shared list regenerates, so a plain splice would come
//    straight back on the next refresh;
//  - surviving `origin:'user'` tasks are APPENDED — they are not in any catalog list, so the
//    old map-over-incoming silently deleted them on the first refresh.
export function mergeActionItems(
  existing: ActionItem[] | undefined,
  incoming: ActionItem[],
  removedKeys?: Iterable<string>,
): ActionItem[] {
  const tombstoned = new Set(removedKeys ?? []);
  const previous = new Map((existing ?? []).map((ai) => [taskKey(ai.text), ai]));
  const merged = incoming
    .filter((ai) => !tombstoned.has(taskKey(ai.text)))
    .map((ai) => {
      const was = previous.get(taskKey(ai.text));
      // State alone now carries "not needed", so there is nothing else to preserve.
      return was ? { ...ai, state: was.state } : ai;
    });
  const mergedKeys = new Set(merged.map((ai) => taskKey(ai.text)));
  const userTasks = (existing ?? []).filter(
    (ai) => ai.origin === 'user' && !mergedKeys.has(taskKey(ai.text)),
  );
  return [...merged, ...userTasks];
}

export interface TrackerItem {
  id: string;
  name: string;
  // The hosting organization, shown under the name on the Quest Log card. Populated at add
  // time from the catalog row's `org`; absent on items added before this field existed.
  org?: string | null;
  url?: string | null;
  type?: string | null;
  bucket: Bucket;
  progressStatus?: string;
  status?: 'running' | 'not_running' | 'rolling' | 'unknown' | string;
  reviewStatus?: string | null;
  reviewSummary?: string | null;
  meta?: string;
  // Structured catalog facets, captured at add-time so the Quest Log card can render the same
  // meta pills Fresh Finds does (buildMetaPills). Absent on items added before this existed —
  // those fall back to the free-text `meta` line. `format` is the catalog `location` column
  // (In-Person / Remote / In-Person and Remote); `state` is the actual place.
  price?: string | null;
  format?: string | null;
  state?: string | null;
  season?: string | null;
  fit?: string;
  note?: string;
  noteType?: string;
  importantDates?: ImportantDate[];
  deadlineLabel?: string;
  wasEstimated?: boolean;
  applyUrl?: string | null;
  applyLabel?: string | null;
  actionItems?: ActionItem[];
  // P10: taskKey()s of CATALOG tasks this student removed. A tombstone, not a splice,
  // because the shared list regenerates on every refresh and a spliced task would come
  // straight back. User tasks are never tombstoned — deleting one is a real splice, since
  // nothing regenerates it.
  removedTasks?: string[];
}

export type TrackerData = Record<Bucket, TrackerItem[]>;

function migrateDismissed(ai: ActionItem): ActionItem {
  if (!ai?.dismissed) return ai;
  const { dismissed: _dropped, ...rest } = ai;
  return { ...rest, state: 'not_needed' };
}

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
        // Migrate the retired `dismissed` flag into the 'not_needed' state. Idempotent, and
        // done on the read path so it applies to data written before the state existed
        // without needing a one-off pass over every account.
        actionItems: Array.isArray(it.actionItems) ? it.actionItems.map(migrateDismissed) : [],
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
  /** Items we got a real deadline answer for. NOT the number of tracked items. */
  checked: number;
  /** Items where ANYTHING changed (deadlines or tasks). */
  updated: number;
  /** P9 distinct counts: items whose dates/status changed vs items whose checklist changed.
   *  The two checks are decoupled — each honours its own staleness rules — so one number
   *  cannot speak for both. */
  deadlineUpdates: number;
  taskUpdates: number;
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

// ---------- Applying catalog deadline/task data onto a tracked item ----------
// Shared by the PAID refresh (refreshTrackerDeadlines, the button) and the FREE catalog sync
// (syncTrackerFromCatalog) so the two apply IDENTICAL merge rules — the same reason
// deadline_write_decision() is one shared function server-side. Both preserve per-user state
// (ticked-off tasks, Google Calendar event ids carried forward by index); neither wipes good
// data on an unverified empty payload.

// Overlay one catalog deadline payload onto a tracked item IN PLACE. Returns whether anything
// changed. `info` is either a /deadline result (paid path) or one entry of the /api/tracker/sync
// batch (free path) — both carry the same {status, important_dates, was_estimated,
// important_date_note, source} shape, and the `source` gate is what lets a verified empty list
// (e.g. a rolling program with no dates) clear a stale snapshot while a mock/fallback echo
// never can.
export function applyDeadlineToTrackerItem(item: TrackerItem, info: Partial<TrackerInfo>): boolean {
  let changed = false;
  if (info.status && ['running', 'not_running', 'rolling', 'unknown'].includes(info.status)
      && info.status !== item.status) {
    item.status = info.status;
    changed = true;
  }
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
        // P6c per-date provenance, carried into the snapshot so the card can render the
        // verified marker + evidence link without another fetch.
        verified: d.verified,
        sourceUrl: d.source_url ?? null,
        // Carry the Google Calendar event id forward by index. Dropping it made the next
        // sync POST a NEW event while the old one (same index-based wingmanId) survived the
        // sweep, so the student's real calendar gained a duplicate on every refresh.
        googleEventId: previous[idx]?.googleEventId ?? null,
      }));
    if (JSON.stringify(mapped) !== JSON.stringify(previous)) changed = true;
    item.importantDates = mapped;
  }
  if (typeof info.was_estimated === 'boolean') item.wasEstimated = info.was_estimated;
  if (info.important_date_note) item.note = info.important_date_note;
  return changed;
}

// Merge a catalog action-item list onto a tracked item IN PLACE, keeping per-user state
// (mergeActionItems keys on task TEXT, honours the item's tombstones, and preserves the
// student's own tasks). Returns whether anything changed. An empty/absent incoming list is a
// no-op — it never wipes tasks, so a row the task agent has not reached keeps whatever it had.
export function applyTasksToTrackerItem(
  item: TrackerItem,
  actionItems: TrackerInfo['action_items'] | undefined,
): boolean {
  const incoming = normalizeVerifiedActionItems(actionItems, item.id);
  if (!incoming.length) return false;
  const merged = mergeActionItems(item.actionItems, incoming, item.removedTasks);
  if (JSON.stringify(merged) === JSON.stringify(item.actionItems ?? [])) return false;
  item.actionItems = merged;
  return true;
}

// ---------- FREE catalog sync (the SYNC half of the freshness model, 2026-08-25) ----------
// The per-user snapshot in users.data is frozen at add-time. Without this, an already-tracking
// student never sees a catalog update — an agent run, or another student's on-demand check
// populating the cache — until they pay to re-verify (the button). This pulls the catalog's
// CURRENT cached deadline+task values for every tracked id in ONE free request and merges them
// in, so the snapshot stops drifting. It NEVER triggers a paid check (that is the button and
// the passive 7-day on-view path). Fired on app-open/login and on Quest Log / Home Base focus.
//
// Throttled: the focus triggers fire often (expo-router remounts on every visit), so a sync
// runs at most once per SYNC_MIN_INTERVAL_MS unless forced (login forces, to always pick up
// what changed while the app was closed).
const SYNC_MIN_INTERVAL_MS = 5 * 60 * 1000;
let _lastCatalogSyncAt = 0;
// The freshest opportunities.dates_last_checked_at seen across tracked items on the last real
// sync. Held so a THROTTLED call can still hand the "Last checked" line a stamp (the line must
// not blank just because the 5-minute window has not elapsed).
let _lastCatalogStamp: string | null = null;

export interface CatalogSyncResult {
  /** The updated tracker data, or null when nothing was fetched (throttled / failed). */
  data: TrackerData | null;
  updated: number;
  /** Freshest catalog dates_last_checked_at across tracked items, for the "Last checked"
   *  line — this is when the DATA was verified, not when the mirror ran. Null if unknown. */
  lastCheckedAt: string | null;
}

export async function syncTrackerFromCatalog(
  opts?: { force?: boolean },
): Promise<CatalogSyncResult> {
  const now = Date.now();
  if (!opts?.force && now - _lastCatalogSyncAt < SYNC_MIN_INTERVAL_MS) {
    return { data: null, updated: 0, lastCheckedAt: _lastCatalogStamp };
  }
  let data: TrackerData;
  try {
    data = await loadTrackerData();
  } catch {
    return { data: null, updated: 0, lastCheckedAt: _lastCatalogStamp };
  }
  const items = flattenItems(data);
  const ids = items.map((i) => i.id).filter(Boolean);
  if (!ids.length) {
    _lastCatalogSyncAt = now;
    return { data, updated: 0, lastCheckedAt: _lastCatalogStamp };
  }
  const catalog = await httpClient.syncTracker(ids); // never throws; {} on failure
  // Stamp the throttle only after a real answer, so a failed/empty sync (network down, signed
  // out) is retried on the next trigger instead of being throttled out for 5 minutes.
  if (!Object.keys(catalog).length) {
    return { data: null, updated: 0, lastCheckedAt: _lastCatalogStamp };
  }
  _lastCatalogSyncAt = now;
  // The "Last checked" line means "when was this deadline data verified against the source".
  // That is the catalog's dates_last_checked_at, not now() — the sync only mirrors. Take the
  // freshest across tracked items (ISO strings compare lexicographically for the same offset;
  // the server writes them all in UTC +00:00, so a plain string max is correct here).
  let freshest: string | null = null;
  for (const info of Object.values(catalog)) {
    const t = info.dates_last_checked_at;
    if (t && (!freshest || t > freshest)) freshest = t;
  }
  if (freshest) _lastCatalogStamp = freshest;
  let updated = 0;
  for (const item of items) {
    const info = catalog[item.id];
    if (!info) continue; // user-added row with no catalog match — keep its own snapshot
    let changed = applyDeadlineToTrackerItem(item, info);
    if (applyTasksToTrackerItem(item, info.action_items)) changed = true;
    if (changed) updated++;
  }
  if (updated) {
    try {
      await saveTrackerData(data);
    } catch {
      // The in-memory data is still updated for the caller to render; it just was not
      // persisted. The next sync will re-derive and retry the write.
    }
  }
  return { data, updated, lastCheckedAt: _lastCatalogStamp };
}

// Quest Log's "Check for updates" button — ported from script.js's refreshTracker(), minus
// the extractTrackerInfo() re-classification pass (a separate, heavier AI call this RN port
// never wired up elsewhere). This overlays the same shared on-demand deadline check
// buildTracker() uses (GET /api/opportunities/<id>/deadline), but FORCES a real check
// (force=true -> ?refresh=1): pressing the button is an explicit "look again now", so it
// bypasses the 7-day cache rather than no-opping into a cache hit. A successful check
// re-stamps the row, so it is then cached for another 7 days for everyone. The cost of that
// is one paid Claude check per tracked item per press (~$0.07 each); the button is disabled
// while a pass runs, so it cannot be double-fired. Passive loads (adds, card opens) still
// use the free cache — only this deliberate action pays.
export async function refreshTrackerDeadlines(
  onProgress?: (checked: number, total: number) => void,
): Promise<DeadlineRefreshResult> {
  const data = await loadTrackerData();
  const items = flattenItems(data);
  let checked = 0;
  let updated = 0;
  let deadlineUpdates = 0;
  let taskUpdates = 0;
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
    const res = await httpClient.getDeadlineCheckResult(item.id, true);
    let dateChanged = false;
    if (res.outcome === 'ok' && res.info) {
      checked++;
      dateChanged = applyDeadlineToTrackerItem(item, res.info);
    } else if (res.outcome === 'not-found') {
      // No catalog row — neither check can run for this item.
      skipped++;
      continue;
    } else if (res.outcome === 'blocked') {
      // The 402 gate covers the task endpoint identically, so don't pay a second refusal.
      blocked++;
      continue;
    } else if (res.outcome === 'auth') {
      // The session is gone, so every remaining item would fail the same way. Stop and
      // say so rather than grinding through the rest and reporting them as failures.
      signedOut = true;
      break;
    } else {
      failed++;
    }
    // Re-pull the shared checklist — DECOUPLED from the deadline outcome (P9). It used to
    // run only when the deadline check succeeded, so a wrong task on an item whose deadline
    // fetch happened to fail stayed wrong. The two checks honour independent staleness:
    // the button FORCES the deadline check (an explicit "look again now"), while the task
    // endpoint applies its own server-side 7-day TTL — a stale checklist re-verifies, a
    // fresh one is served free. The merge keeps whatever the student has ticked off.
    // getActionItems never throws (null on failure), and applyTasksToTrackerItem treats an
    // empty/absent list as a no-op — so a task fetch failure leaves the checklist untouched
    // and the next refresh or free sync retries.
    const shared = await httpClient.getActionItems(item.id);
    const taskChanged = applyTasksToTrackerItem(item, shared?.action_items);
    if (dateChanged) deadlineUpdates++;
    if (taskChanged) taskUpdates++;
    if (dateChanged || taskChanged) updated++;
  }
  onProgress?.(items.length, items.length);
  await saveTrackerData(data);
  return {
    data, checked, updated, deadlineUpdates, taskUpdates,
    skipped, blocked, failed, signedOut, total: items.length,
  };
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

// ---------- P10: per-user task delete + user-added tasks ----------
// All three run load-modify-save like the item mutators above, so the persisted shape is the
// single source of truth and a stale in-memory copy can never clobber another screen's write.

function mutateItem(
  data: TrackerData,
  itemId: string,
  fn: (item: TrackerItem) => TrackerItem,
): boolean {
  let hit = false;
  ALL_BUCKETS.forEach((b) => {
    data[b] = data[b].map((i) => {
      if (i.id !== itemId) return i;
      hit = true;
      return fn(i);
    });
  });
  return hit;
}

// Delete one task. A catalog task gets a TOMBSTONE (its taskKey lands in removedTasks) so
// the regenerated shared list cannot bring it back; a user task is a plain splice, since
// nothing regenerates it. Reversible via restoreRemovedTasks below.
export async function deleteTrackerTask(itemId: string, actionId: string): Promise<TrackerData> {
  const data = await loadTrackerData();
  mutateItem(data, itemId, (item) => {
    const target = (item.actionItems ?? []).find((ai) => ai.id === actionId);
    if (!target) return item;
    const rest = (item.actionItems ?? []).filter((ai) => ai.id !== actionId);
    if (target.origin === 'user') return { ...item, actionItems: rest };
    const key = taskKey(target.text);
    const removed = item.removedTasks ?? [];
    return {
      ...item,
      actionItems: rest,
      removedTasks: removed.includes(key) ? removed : [...removed, key],
    };
  });
  await saveTrackerData(data);
  return data;
}

// Clear an item's tombstones. The tasks themselves come back on the next refresh/free sync
// (the merge stops dropping them); restoring does not need to reconstruct them locally —
// but when the current list still holds them nothing changes, so this is always safe.
export async function restoreRemovedTasks(itemId: string): Promise<TrackerData> {
  const data = await loadTrackerData();
  mutateItem(data, itemId, (item) => ({ ...item, removedTasks: [] }));
  await saveTrackerData(data);
  return data;
}

// Add the student's own task. Never page-backed by construction — nothing verified it — so
// it carries basis 'generic' and no tier, exactly like every other claim nothing has proven.
// Refused (returning { added: false }) when a task with the same text already exists on the
// item, because the text IS the identity everything merges on: a duplicate key would make
// state-preservation and tombstoning ambiguous between the two copies.
export async function addUserTask(
  itemId: string,
  text: string,
): Promise<{ data: TrackerData; added: boolean }> {
  const data = await loadTrackerData();
  const trimmed = text.trim();
  let added = false;
  if (trimmed) {
    mutateItem(data, itemId, (item) => {
      const existing = item.actionItems ?? [];
      if (existing.some((ai) => taskKey(ai.text) === taskKey(trimmed))) return item;
      // Random suffix rather than a positional index: user ids must stay stable while
      // catalog regenerations shuffle the `-tN` ids around them.
      const id = `${itemId}-u${Math.random().toString(36).slice(2, 8)}`;
      added = true;
      return {
        ...item,
        actionItems: [...existing, {
          id,
          text: trimmed,
          url: null,
          state: 'not_started',
          basis: 'generic' as const,
          evidence: null,
          sourceTier: null,
          sourceUrl: null,
          sourceDomain: null,
          origin: 'user' as const,
        }],
        // Adding back a task the student previously removed should also lift its tombstone,
        // or the merge would silently delete the re-added task if it ever matches a
        // regenerated catalog line.
        removedTasks: (item.removedTasks ?? []).filter((k) => k !== taskKey(trimmed)),
      };
    });
  }
  if (added) await saveTrackerData(data);
  return { data, added };
}
