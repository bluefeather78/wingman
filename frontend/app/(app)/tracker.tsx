import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  Easing,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import {
  loadTrackerData,
  loadTrackerSaved,
  refreshTrackerDeadlines,
  removeTrackerItem,
  saveTrackerSaved,
  syncTrackerFromCatalog,
  type SavedState,
  type TrackerData,
  type TrackerItem,
} from '@/api/trackerStore';
import { syncTrackerToCalendar } from '@/api/calendarSync';
import { httpClient } from '@/api/httpClient';
import { ALL_BUCKETS, type Bucket } from '@/lib/constants';
import { googleCalendarReturnUri } from '@/auth/googleSignIn';
import { clearNewlyAdded, getNewlyAdded, markNewlyAdded } from '@/lib/newlyAdded';
import { getLastCheckedLabel, setLastCheckedLabel as rememberLastChecked } from '@/lib/lastChecked';
import { addCatalogOpportunity, bucketForOpp } from '@/api/trackerAdd';
import type { Opportunity } from '@/api/types';
import { computeProgressStatus, earliestUpcoming } from '@/lib/status';

// The sync button's own spinner while a sync is in flight (the design's rotating refresh
// glyph). useNativeDriver is off on web — RN-web's driver can't animate transforms there.
function SpinningRefresh({ size = 16, color = colors.navy }: { size?: number; color?: string }) {
  const spin = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.timing(spin, { toValue: 1, duration: 800, easing: Easing.linear, useNativeDriver: Platform.OS !== 'web' }),
    );
    loop.start();
    return () => loop.stop();
  }, [spin]);
  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  return (
    <Animated.View style={{ transform: [{ rotate }] }}>
      <RefreshIcon size={size} color={color} />
    </Animated.View>
  );
}

// script.js sortedByTrackerDeadline: status group first (Happening Now, Future, Past),
// then soonest upcoming date within each group.
const STATUS_ORDER = { in_progress: 0, not_started: 1, completed: 2 } as const;
// `newIds` is the batch just added from Fresh Finds. It sorts AHEAD of the status/date
// order rather than being folded into it, so a newly-added opportunity with a far-off (or
// missing) deadline is still the first thing the student sees — which is the whole point of
// arriving here straight from adding it. Within the batch the normal order still applies.
function sortEntries(entries: { item: TrackerItem; bucket: Bucket }[], newIds?: Set<string>) {
  const dateOf = (item: TrackerItem) => earliestUpcoming(item)?.date ?? '9999-12-31';
  return [...entries].sort((a, b) => {
    if (newIds && newIds.size) {
      const n = (newIds.has(b.item.id) ? 1 : 0) - (newIds.has(a.item.id) ? 1 : 0);
      if (n !== 0) return n;
    }
    const s = STATUS_ORDER[computeProgressStatus(a.item)] - STATUS_ORDER[computeProgressStatus(b.item)];
    if (s !== 0) return s;
    return dateOf(a.item).localeCompare(dateOf(b.item));
  });
}
import { IconBtn, PopButton, RightDrawer, Screen, SoftCard, Txt } from '@/ui/components';
import { useAiGate } from '@/ui/AiLimitBanner';
import { CalendarIcon, CalendarSyncIcon, ListIcon, RefreshIcon, SearchIcon } from '@/ui/icons';
import { colors, fonts, popShadow, radius, space } from '@/ui/theme';
import { CalendarCard } from '@/ui/tracker/CalendarCard';
import { ListCard } from '@/ui/tracker/ListCard';

// Quest Log — ported from the live app's #page-tracker: header controls (refresh status,
// calendar-sync + Add Opportunity), "Actively Tracked" + count + Calendar/List view-tabs,
// the swimlane month-card calendar, and the list view with pop cards + Saved for Later.
export default function Tracker() {
  const router = useRouter();
  // Free-tier AI gate: greys the deadline "Check for updates" and the catalog "Add" (both spend
  // an AI action) and re-shows the banner on tap when out of quota.
  const { reached: aiBlocked, guard: aiGuard, dimStyle } = useAiGate();
  const [data, setData] = useState<TrackerData | null>(null);
  const [saved, setSaved] = useState<SavedState>({});
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'calendar' | 'list'>('calendar');
  const [refreshing, setRefreshing] = useState(false);
  // Seeded from the module singleton so switching tabs and coming back still shows the
  // last real result instead of resetting to "never" - see lib/lastChecked.ts.
  const [lastCheckedLabel, setLastCheckedLabelState] = useState(getLastCheckedLabel);
  const setLastCheckedLabel = useCallback((next: string) => {
    rememberLastChecked(next);
    setLastCheckedLabelState(next);
  }, []);
  // Sync has four visible states, per the Quest Log sync designs: idle (nothing shown),
  // syncing (navy "Syncing…" + spinning glyph + a gray in-progress note), done (green
  // "Synced ✓" that fades at 4s over a green note that fades at 8s), and error (which
  // deliberately does NOT auto-clear — a failure the student never saw is a lie).
  const [syncState, setSyncState] = useState<'idle' | 'syncing' | 'done' | 'error'>('idle');
  const [syncNote, setSyncNote] = useState<string | null>(null);
  // Deep link into the Wingman calendar in Google Calendar, so "where did they go?"
  // is one tap rather than a hunt through the sidebar.
  const [syncLink, setSyncLink] = useState<string | null>(null);
  const syncing = syncState === 'syncing';
  const syncLabelAnim = useRef(new Animated.Value(1)).current;
  const syncNoteAnim = useRef(new Animated.Value(1)).current;
  const syncTimers = useRef<ReturnType<typeof setTimeout>[]>([]);

  // "Add Opportunity" search drawer — slides in like the profile chat, searches the catalog
  // by opportunity NAME, and adds any number of picks in one shot via the shared catalog-add
  // flow Fresh Finds uses.
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  // The active catalog, loaded lazily the first time the drawer opens (free — same public
  // /api/opportunities Fresh Finds reads). Filtering happens client-side on `name`.
  const [catalog, setCatalog] = useState<Opportunity[] | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  // Multi-select: the checked result ids, and the batch-add progress while adding them all.
  const [selectedResults, setSelectedResults] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [addProgress, setAddProgress] = useState<{ done: number; total: number } | null>(null);
  const [searchStatus, setSearchStatus] = useState('');
  // Snapshotted on focus rather than read during render: the batch is module state, so
  // reading it inline would make the sort order depend on when a re-render happened.
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  // Calendar tile -> list card jump, ported from script.js's goToTrackerCard(): switch to
  // list view, then once its cards exist scroll the matching one into view and flash it.
  const [highlightId, setHighlightId] = useState<string | null>(null);
  // Which card's review popover is open, lifted out of ListCard so only one is ever open —
  // the rule the retired SPA's toggleReviewInfo() enforced by closing every other panel first.
  const [openReviewId, setOpenReviewId] = useState<string | null>(null);
  const toggleReview = useCallback(
    (id: string) => setOpenReviewId((cur) => (cur === id ? null : id)),
    [],
  );
  const cardRefs = useRef<Map<string, { scrollIntoView?: (opts: unknown) => void }>>(new Map());
  const pendingScrollId = useRef<string | null>(null);

  function goToTrackerCard(id: string) {
    pendingScrollId.current = id;
    setView('list');
  }

  useEffect(() => {
    if (view !== 'list' || !pendingScrollId.current) return;
    const id = pendingScrollId.current;
    const t = setTimeout(() => {
      const el = cardRefs.current.get(id);
      if (Platform.OS === 'web' && typeof el?.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      setHighlightId(id);
      setTimeout(() => setHighlightId((cur) => (cur === id ? null : cur)), 1600);
      pendingScrollId.current = null;
    }, 80);
    return () => clearTimeout(t);
  }, [view]);

  useFocusEffect(
    useCallback(() => {
      let alive = true;
      const justAdded = getNewlyAdded();
      setNewIds(new Set(justAdded));
      // Arriving straight from Fresh Finds: the thing the student just added is a card, not
      // a date on a swimlane, so land on the list where it can be badged NEW and scrolled to.
      if (justAdded.size) setView('list');
      Promise.all([loadTrackerData(), loadTrackerSaved()])
        .then(([d, s]) => {
          if (!alive) return;
          setData(d);
          setSaved(s);
        })
        .catch((e) => alive && setError((e as Error).message));
      // Free catalog sync (throttled): pull any deadline/task updates the catalog has picked
      // up since this snapshot was written, and re-render if anything changed. Runs after the
      // fast local load above so the screen paints immediately, then quietly updates. No paid
      // check — that stays on "Check for updates".
      syncTrackerFromCatalog()
        .then((r) => {
          if (!alive) return;
          if (r.updated && r.data) setData(r.data);
          // Stamp "Last checked" with when the CATALOG last verified these deadlines
          // (dates_last_checked_at), NOT the sync's wall-clock — the sync only mirrors. This
          // is why the line no longer reads "never" on a fresh load of already-verified data.
          if (r.lastCheckedAt) {
            const stamp = new Date(r.lastCheckedAt).toLocaleString('en-US', {
              month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
            });
            setLastCheckedLabel(`Last checked: ${stamp}`);
          }
        })
        .catch(() => null);
      return () => {
        alive = false;
        // Matches script.js showPage(): navigating away from the Quest Log ends the batch,
        // so the marker does not reappear on a later visit. The snapshot in `newIds` keeps
        // the cards rendered until the next focus re-reads the (now empty) set.
        clearNewlyAdded();
      };
    }, []),
  );

  async function remove(id: string) {
    setData(await removeTrackerItem(id));
  }

  async function checkForUpdates() {
    if (refreshing) return;
    const total = data ? Object.values(data).reduce((n, arr) => n + arr.length, 0) : 0;
    if (!total) {
      setLastCheckedLabel('Nothing tracked yet — add opportunities first.');
      return;
    }
    setRefreshing(true);
    // Progress ticks are component-only: they are transient, and remembering one would
    // leave a frozen "Checking (3/12)…" on screen if the student navigates away mid-run.
    // Only terminal outcomes go through setLastCheckedLabel and survive a tab change.
    setLastCheckedLabelState(`Checking (1/${total})…`);
    try {
      const result = await refreshTrackerDeadlines((checked, count) => {
        setLastCheckedLabelState(`Checking (${Math.min(checked + 1, count)}/${count})…`);
      });
      setData(result.data);
      const stamp = new Date().toLocaleString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
      });
      // Report what actually happened, per outcome. This used to say "no changes found"
      // whenever `updated` was 0 - including when nothing had been checked at all, which is
      // the case for every opportunity added by URL before catalog linking existed. Telling
      // a student their deadlines are current when nobody looked is the worst answer here,
      // because it is the one that stops them checking themselves.
      const parts: string[] = [];
      if (result.updated) {
        // Distinct counts (P9): the deadline and task checks are decoupled, so one blended
        // "N updates" cannot say WHICH kind of thing moved — and a changed deadline warrants
        // a different reaction than a changed checklist.
        const kinds: string[] = [];
        if (result.deadlineUpdates) {
          kinds.push(`${result.deadlineUpdates} deadline${result.deadlineUpdates > 1 ? 's' : ''}`);
        }
        if (result.taskUpdates) {
          kinds.push(`${result.taskUpdates} task list${result.taskUpdates > 1 ? 's' : ''}`);
        }
        parts.push(`${kinds.join(' and ')} updated`);
      } else if (result.checked) {
        parts.push('no changes found');
      }
      if (result.skipped) {
        parts.push(`${result.skipped} added by URL can’t be auto-checked`);
      }
      if (result.blocked) {
        parts.push(`${result.blocked} needed an active plan`);
      }
      if (result.signedOut) {
        parts.push('stopped — please sign in again');
      }
      if (result.failed) {
        parts.push(`${result.failed} couldn’t be reached`);
      }
      setLastCheckedLabel(
        result.checked
          ? `Last checked: ${stamp} — ${parts.join(' · ')}`
          : `Nothing could be checked (${stamp}) — ${parts.join(' · ') || 'no tracked opportunities'}`,
      );
    } catch (e) {
      setLastCheckedLabel(`Check failed: ${(e as Error).message}`);
    } finally {
      setRefreshing(false);
    }
  }
  // Sync to Calendar. The sweep half is what makes a removal stick: syncTrackerToCalendar
  // always sends the full tracked set and asks the server to delete the events it wrote for
  // anything no longer in it, so deadlines for opportunities taken out of the Quest Log come
  // off the calendar on the next sync — including ones removed before this app even loaded.
  function clearSyncTimers() {
    syncTimers.current.forEach(clearTimeout);
    syncTimers.current = [];
  }
  useEffect(() => clearSyncTimers, []);
  function fadeOutAfter(anim: Animated.Value, delay: number, done: () => void) {
    syncTimers.current.push(
      setTimeout(() => {
        Animated.timing(anim, { toValue: 0, duration: 600, useNativeDriver: Platform.OS !== 'web' }).start(done);
      }, delay),
    );
  }
  async function syncToCalendar() {
    if (syncing) return;
    clearSyncTimers();
    syncLabelAnim.setValue(1);
    syncNoteAnim.setValue(1);
    setSyncState('syncing');
    setSyncLink(null);
    setSyncNote(`Pulling deadlines into your Google Calendar — this can take up to a minute.`);
    try {
      const out = await syncTrackerToCalendar();
      // Both of these recover the same way — send the student back through Google's consent
      // page. 'reconnect' is a present-but-revoked token (server 502, code
      // "calendar_reconnect"): before this it fell through to the generic error branch and
      // showed a dead-end "could not refresh… reconnect" message with no way to actually
      // reconnect, since the connect page only ever opened for a never-connected account.
      if (out.kind === 'not-connected' || out.kind === 'reconnect') {
        const url = await httpClient.googleCalendarConnectUrl(googleCalendarReturnUri());
        setSyncState('error');
        setSyncNote(
          url
            ? (out.kind === 'reconnect'
                ? 'Your Google Calendar connection expired — reopening the connect page…'
                : 'Google Calendar isn’t connected yet — opening the connect page…')
            : 'Please sign in again to connect Google Calendar.',
        );
        if (url) await Linking.openURL(url);
        return;
      }
      if (out.kind === 'error') {
        setSyncState('error');
        setSyncNote(out.message);
        return;
      }
      // The design's wording for the clean case; anything notable (removals, failures) keeps
      // the counted breakdown instead — dropping "2 failed" to match a mockup would hide it.
      // NAME the calendar. Events go to a dedicated "Highschool Wingman" calendar and can
      // never go anywhere else — the calendar.app.created scope only grants access to
      // calendars this app created. Saying a bare "synced to Google Calendar" is what makes
      // a student check their primary calendar, see nothing, and report the feature broken.
      const where = `in your “${out.calendarName}” calendar`;
      const clean = !out.removed && !out.deduped && !out.failed && !out.sweepErrors.length;
      if (clean) {
        setSyncNote(`Calendar synced — your deadlines are up to date ${where}.`);
      } else {
        const parts: string[] = [`Synced ${out.synced} deadline${out.synced === 1 ? '' : 's'} ${where}`];
        if (out.removed) parts.push(`removed ${out.removed} no longer tracked`);
        if (out.deduped) parts.push(`cleaned up ${out.deduped} duplicate${out.deduped === 1 ? '' : 's'}`);
        if (out.failed) parts.push(`${out.failed} failed`);
        if (out.sweepErrors.length) parts.push('some removals could not be completed');
        setSyncNote(`${parts.join(' · ')}.`);
      }
      setSyncLink(out.calendarLink || null);
      setSyncState('done');
      fadeOutAfter(syncLabelAnim, 4000, () => setSyncState('idle'));
      fadeOutAfter(syncNoteAnim, 8000, () => setSyncNote(null));
    } catch (e) {
      setSyncState('error');
      setSyncNote(`Could not sync: ${(e as Error).message}`);
    }
  }

  // ---------- Search: find a catalog opportunity by name and add it ----------
  // Load the active catalog once, the first time the panel opens. Free — the same public
  // /api/opportunities Fresh Finds reads (active rows only).
  async function ensureCatalog() {
    if (catalog || catalogLoading) return;
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      setCatalog(await httpClient.getOpportunities());
    } catch (err) {
      setCatalogError((err as Error).message || 'Could not load opportunities.');
    } finally {
      setCatalogLoading(false);
    }
  }

  function openSearch() {
    setSearchOpen(true);
    void ensureCatalog();
  }

  function closeSearch() {
    setSearchOpen(false);
    setSearchQuery('');
    setSelectedResults(new Set());
    setSearchStatus('');
  }

  function toggleSelect(id: string) {
    setSelectedResults((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // ids/urls already tracked, so a match already in the Quest Log shows "In Quest Log"
  // instead of an Add button — the same rule addTrackerItemChecked enforces on write.
  const trackedKeys = useMemo(() => {
    const ids = new Set<string>();
    const urls = new Set<string>();
    if (data) {
      ALL_BUCKETS.forEach((b) => data[b].forEach((i) => {
        ids.add(i.id);
        if (i.url) urls.add(i.url);
      }));
    }
    return { ids, urls };
  }, [data]);

  // Case-insensitive substring match on the opportunity NAME or its ORG (organization) name,
  // capped so a broad query does not render the whole catalog. Matched per-field (not on a
  // concatenation) so a query never spans the name/org boundary. An empty query shows nothing
  // (the panel is a search box, not a browser — Fresh Finds is the browse surface).
  const SEARCH_LIMIT = 25;
  const searchResults = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q || !catalog) return [] as Opportunity[];
    return catalog
      .filter((o) => (o.name ?? '').toLowerCase().includes(q) || (o.org ?? '').toLowerCase().includes(q))
      .slice(0, SEARCH_LIMIT);
  }, [searchQuery, catalog]);

  // Add every checked result in one shot. Mirrors Fresh Finds' addSelectedToTracker: each
  // pick runs the shared catalog-add flow (meta/fit + cached deadline check + verified
  // checklist), only the ids the store actually wrote are badged NEW, and duplicates are
  // named rather than silently dropped.
  async function addSelected() {
    if (adding || !selectedResults.size || !catalog) return;
    const byId = new Map(catalog.map((o) => [o.id, o] as const));
    const ids = [...selectedResults];
    setAdding(true);
    setSearchStatus('');
    setAddProgress({ done: 0, total: ids.length });
    const addedIds: string[] = [];
    const duplicates: string[] = [];
    // A failure is not a duplicate (Phase 5, frontend_report finding 17). These used to share
    // one list, so an add that ERRORED was reported as "Already tracked: <name> (<message>)" —
    // a claim about the student's Quest Log that is not true, with a stack-trace-ish aside
    // stapled to it, and no suggestion that trying again would help.
    const failed: string[] = [];
    try {
      for (let i = 0; i < ids.length; i++) {
        const opp = byId.get(ids[i]);
        if (opp) {
          try {
            const outcome = await addCatalogOpportunity(opp, bucketForOpp(opp), (opp.summary as string) || '');
            if (outcome.added) addedIds.push(opp.id);
            else duplicates.push(outcome.existingName || opp.name);
          } catch (err) {
            console.warn(`Could not add ${opp.name}:`, (err as Error).message);
            failed.push(opp.name);
          }
        }
        setAddProgress({ done: i + 1, total: ids.length });
      }
      if (addedIds.length) {
        setData(await loadTrackerData());
        // Same treatment a Fresh Finds add gets: badged NEW, floated to the top.
        markNewlyAdded(addedIds);
        setNewIds(new Set(addedIds));
      }
      const listOf = (names: string[]) => {
        const shown = names.slice(0, 3).join(', ');
        return names.length > 3 ? `${shown} +${names.length - 3} more` : shown;
      };
      const dupNote = [
        duplicates.length ? ` Already tracked: ${listOf(duplicates)}.` : '',
        failed.length ? ` Couldn't add ${listOf(failed)} — try again.` : '',
      ].join('');
      if (addedIds.length) {
        // Close the drawer and jump to the first new card — the point of adding is to go
        // look at what you added.
        const first = addedIds[0];
        closeSearch();
        goToTrackerCard(first);
      } else {
        setSelectedResults(new Set());
        setSearchStatus(`Nothing new to add.${dupNote}`);
      }
    } finally {
      setAdding(false);
      setAddProgress(null);
    }
  }

  async function toggleSaved(id: string) {
    const next = { ...saved, [id]: !saved[id] };
    setSaved(next);
    await saveTrackerSaved(next);
  }

  // Raw bucket order — the calendar's color assignment depends on first-appearance order
  // exactly like the old app's renderCalendarSwimlanes (which never sorts).
  const rawActiveItems = useMemo(() => {
    if (!data) return [] as { item: TrackerItem; bucket: Bucket }[];
    const out: { item: TrackerItem; bucket: Bucket }[] = [];
    ALL_BUCKETS.forEach((b) => data[b].forEach((item) => !saved[item.id] && out.push({ item, bucket: b })));
    return out;
  }, [data, saved]);
  const activeItems = useMemo(() => sortEntries(rawActiveItems, newIds), [rawActiveItems, newIds]);
  const savedItems = useMemo(() => {
    if (!data) return [] as { item: TrackerItem; bucket: Bucket }[];
    const out: { item: TrackerItem; bucket: Bucket }[] = [];
    ALL_BUCKETS.forEach((b) => data[b].forEach((item) => saved[item.id] && out.push({ item, bucket: b })));
    return sortEntries(out);
  }, [data, saved]);

  return (
    <Screen>
      {/* Header controls */}
      <View style={styles.topRow}>
        <View style={styles.topLeft}>
          <Text style={styles.lastChecked}>{lastCheckedLabel}</Text>
          <View style={dimStyle}>
            <IconBtn
              onPress={aiGuard(checkForUpdates)}
              disabled={refreshing}
              label={refreshing ? 'Checking for updates' : 'Check all tracked opportunities for updates'}
            >
              <RefreshIcon size={14} color={refreshing || aiBlocked ? colors.slate400 : colors.indigo600} />
            </IconBtn>
          </View>
        </View>
        <View style={styles.topRight}>
          {syncState !== 'idle' && (
            <Animated.Text
              style={[
                styles.syncLabel,
                syncState === 'done' && styles.syncLabelDone,
                syncState === 'error' && styles.syncLabelError,
                { opacity: syncState === 'done' ? syncLabelAnim : 1 },
              ]}
            >
              {syncing ? 'Syncing…' : syncState === 'done' ? 'Synced ✓' : 'Sync failed'}
            </Animated.Text>
          )}
          <View style={syncing ? styles.syncBtnBusy : null}>
            <IconBtn
              onPress={syncing ? undefined : syncToCalendar}
              disabled={syncing}
              label={syncing ? 'Syncing deadlines to Google Calendar' : 'Sync deadlines to Google Calendar'}
            >
              {syncing ? <SpinningRefresh size={16} /> : <CalendarSyncIcon size={16} color={colors.navy} />}
            </IconBtn>
          </View>
          <IconBtn onPress={openSearch} label="Search the catalog to add an opportunity">
            <SearchIcon size={16} color={colors.navy} />
          </IconBtn>
        </View>
      </View>

      {!!syncNote && (
        <Animated.Text
          style={[
            styles.syncNote,
            syncState === 'done' && styles.syncNoteDone,
            syncState === 'error' && styles.syncNoteError,
            { opacity: syncState === 'done' ? syncNoteAnim : 1 },
          ]}
        >
          {syncNote}
          {syncState === 'done' && !!syncLink && (
            <Text style={styles.syncLink} onPress={() => Linking.openURL(syncLink)}>
              {'  Open calendar ›'}
            </Text>
          )}
        </Animated.Text>
      )}

      {/* Actively Tracked + view toggle */}
      <View style={styles.headRow}>
        <View style={styles.titleWrap}>
          <Txt variant="h2" style={{ color: colors.ink }}>Actively Tracked</Txt>
          <View style={styles.countPill}>
            <Text style={styles.countText}>{String(activeItems.length).padStart(2, '0')}</Text>
          </View>
        </View>
        <View style={styles.viewTabs}>
          {(['calendar', 'list'] as const).map((v) => (
            <Pressable key={v} onPress={() => setView(v)} style={[styles.viewTab, view === v && styles.viewTabActive]}>
              {v === 'calendar' ? (
                <CalendarIcon size={16} color={view === v ? colors.white : '#5B6785'} />
              ) : (
                <ListIcon size={16} color={view === v ? colors.white : '#5B6785'} />
              )}
              <Text style={[styles.viewTabText, view === v && styles.viewTabTextActive]}>
                {v === 'calendar' ? 'Calendar' : 'List'}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>

      {error ? (
        <SoftCard><Txt variant="body">Couldn't load your tracker: {error}</Txt></SoftCard>
      ) : !data ? (
        <SoftCard><Txt variant="body">Loading…</Txt></SoftCard>
      ) : view === 'calendar' ? (
        <CalendarCard entries={rawActiveItems} onEntryPress={goToTrackerCard} />
      ) : (
        <>
          <View style={{ gap: space.lg }}>
          {activeItems.length === 0 ? (
            <SoftCard><Text style={styles.emptyState}>Nothing tracked here yet — add opportunities via the Finder or the button above.</Text></SoftCard>
          ) : (
            activeItems.map(({ item, bucket }) => (
              <ListCard
                key={item.id}
                item={item}
                bucket={bucket}
                isSaved={false}
                isNew={newIds.has(item.id)}
                onRemove={remove}
                onToggleSaved={toggleSaved}
                highlighted={item.id === highlightId}
                cardRef={(el) => { if (el) cardRefs.current.set(item.id, el); else cardRefs.current.delete(item.id); }}
                reviewOpen={openReviewId === item.id}
                onToggleReview={toggleReview}
              />
            ))
          )}
          </View>
          <View style={styles.savedHead}>
            <Txt variant="h2" style={{ color: colors.ink }}>Saved for Later</Txt>
            <View style={styles.countPill}>
              <Text style={styles.countText}>{String(savedItems.length).padStart(2, '0')}</Text>
            </View>
          </View>
          <View style={{ gap: space.lg }}>
          {savedItems.length === 0 ? (
            <Text style={styles.emptyState}>Nothing saved yet — click "☆ Save for later" on any card to move it here.</Text>
          ) : (
            savedItems.map(({ item, bucket }) => (
              <ListCard key={item.id} item={item} bucket={bucket} isSaved onRemove={remove} onToggleSaved={toggleSaved} reviewOpen={openReviewId === item.id} onToggleReview={toggleReview} />
            ))
          )}
          </View>
        </>
      )}

      {/* Search drawer — slides in from the right like the profile chat. Search the catalog
          by name, check any number of results, and add them all in one shot. */}
      <RightDrawer open={searchOpen} onClose={closeSearch} width={440} duration={250} panelStyle={styles.searchDrawer}>
        <>
          <View style={styles.drawerHead}>
            <View style={styles.drawerHeadText}>
              <Text style={styles.drawerTitle}>Add opportunities</Text>
              <Text style={styles.drawerSub}>Search the catalog by name or organization, pick any you want, and add them all at once.</Text>
            </View>
            <Pressable
              onPress={closeSearch}
              hitSlop={10}
              accessibilityRole="button"
              accessibilityLabel="Close add opportunities"
            >
              <Text style={styles.drawerClose}>✕</Text>
            </Pressable>
          </View>

          <View style={styles.searchBarWrap}>
            <SearchIcon size={16} color={colors.slate400} />
            <TextInput
              style={styles.searchBarInput}
              value={searchQuery}
              onChangeText={setSearchQuery}
              placeholder="Search by name or organization…"
              placeholderTextColor={colors.slate400}
              autoCapitalize="none"
              autoCorrect={false}
              autoFocus
            />
          </View>

          <ScrollView style={styles.drawerBody} contentContainerStyle={styles.searchDrawerBody} keyboardShouldPersistTaps="handled">
            {catalogLoading && <ActivityIndicator color={colors.navy} />}
            {!!catalogError && (
              <View style={styles.intakeErrorBox}>
                <Text style={styles.intakeErrorText}>{catalogError}</Text>
              </View>
            )}
            {!catalogLoading && !catalogError && !searchQuery.trim() && (
              <Text style={styles.searchHint}>Start typing a program or organization name to see matches.</Text>
            )}
            {!catalogLoading && !catalogError && !!searchQuery.trim() && searchResults.length === 0 && (
              <Text style={styles.searchHint}>No opportunities match “{searchQuery.trim()}”.</Text>
            )}
            {searchResults.map((opp) => {
              const url = (opp.url as string) ?? '';
              const tracked = trackedKeys.ids.has(opp.id) || (!!url && trackedKeys.urls.has(url));
              const checked = selectedResults.has(opp.id);
              const sub = [opp.org, opp.type].filter(Boolean).join(' · ');
              return (
                <Pressable
                  key={opp.id}
                  style={[styles.searchRow, tracked && styles.searchRowDisabled]}
                  onPress={tracked || adding ? undefined : () => toggleSelect(opp.id)}
                  // A text glyph is not a checkbox to anything but a sighted reader: the ✓
                  // below carries no role and no state, so the row announced as plain text
                  // and gave no way to tell selected from not (finding 20).
                  accessibilityRole="checkbox"
                  accessibilityState={{ checked: checked || tracked, disabled: tracked || adding }}
                  accessibilityLabel={tracked ? `${opp.name} — already in your Quest Log` : opp.name}
                >
                  <View style={[styles.checkbox, checked && styles.checkboxOn, tracked && styles.checkboxTracked]}>
                    {(checked || tracked) && <Text style={styles.checkboxMark}>✓</Text>}
                  </View>
                  <View style={styles.searchRowText}>
                    <Text style={styles.searchRowName} numberOfLines={2}>{opp.name}</Text>
                    {!!sub && <Text style={styles.searchRowSub} numberOfLines={1}>{sub}</Text>}
                  </View>
                  {tracked && <Text style={styles.searchRowTracked}>In Quest Log</Text>}
                </Pressable>
              );
            })}
          </ScrollView>

          <View style={styles.drawerFoot}>
            {!!searchStatus && <Text style={styles.searchStatusText}>{searchStatus}</Text>}
            <PopButton
              full
              label={
                adding
                  ? `Adding ${addProgress ? `${addProgress.done}/${addProgress.total}` : ''}…`
                  : selectedResults.size
                    ? `Add ${selectedResults.size} to Quest Log`
                    : 'Select opportunities to add'
              }
              loading={adding}
              disabled={!selectedResults.size || adding}
              onPress={aiGuard(addSelected)}
              style={dimStyle}
            />
          </View>
        </>
      </RightDrawer>
    </Screen>
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: space.lg, flexWrap: 'wrap' },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  lastChecked: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate400 },
  topRight: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  syncLabel: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.navy },
  syncLabelDone: { color: colors.statusNowFg },
  syncLabelError: { color: colors.red },
  syncBtnBusy: { opacity: 0.85 },
  // Centered under the header rather than at the foot of the page (where the design puts
  // it): List view runs long, and a sync result below all of it would never be seen.
  syncNote: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.muted, textAlign: 'center', marginTop: 6 },
  syncNoteDone: { color: colors.statusNowFg },
  syncNoteError: { color: colors.red },
  syncLink: { color: colors.navy, textDecorationLine: 'underline', fontFamily: fonts.bodySemi },

  // ---- Search drawer (mirrors the profile chat's .story-drawer head/body/foot) ----
  searchDrawer: { borderLeftWidth: 4, borderLeftColor: colors.ink },
  drawerHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, paddingHorizontal: 20, paddingTop: 20, paddingBottom: 16, borderBottomWidth: 2, borderBottomColor: colors.lavender },
  drawerHeadText: { flex: 1, minWidth: 0 },
  drawerTitle: { fontFamily: fonts.display, fontSize: 18, color: colors.ink },
  drawerSub: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.muted, marginTop: 4 },
  drawerClose: { fontFamily: fonts.bodyXBold, fontSize: 20, color: colors.muted },
  searchBarWrap: { flexDirection: 'row', alignItems: 'center', gap: 8, marginHorizontal: 20, marginTop: 16, paddingHorizontal: 12, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.lg, backgroundColor: colors.white },
  searchBarInput: { flex: 1, minWidth: 0, paddingVertical: 10, fontFamily: fonts.bodyMed, fontSize: 15, color: colors.ink },
  drawerBody: { flex: 1, backgroundColor: colors.cream },
  searchDrawerBody: { padding: 20, gap: 4 },
  intakeErrorBox: { backgroundColor: colors.redSoft, borderWidth: 2, borderColor: '#881337', borderRadius: 8, padding: 8 },
  intakeErrorText: { fontFamily: fonts.bodyBold, fontSize: 12, color: '#881337' },
  searchHint: { fontFamily: fonts.bodyMed, fontSize: 13, color: colors.slate500, textAlign: 'center', marginTop: 12 },
  searchRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: colors.lavender },
  searchRowDisabled: { opacity: 0.55 },
  searchRowText: { flex: 1, flexShrink: 1, minWidth: 0 },
  searchRowName: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.ink },
  searchRowSub: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500, marginTop: 2 },
  searchRowTracked: { fontFamily: fonts.bodyBold, fontSize: 10, color: colors.slate400, textTransform: 'uppercase' },
  checkbox: { width: 22, height: 22, borderRadius: 6, borderWidth: 2, borderColor: colors.slate900, backgroundColor: colors.white, alignItems: 'center', justifyContent: 'center' },
  checkboxOn: { backgroundColor: colors.navy, borderColor: colors.navy },
  checkboxTracked: { backgroundColor: colors.slate200, borderColor: colors.slate200 },
  checkboxMark: { color: colors.white, fontFamily: fonts.bodyXBold, fontSize: 13, lineHeight: 15 },
  drawerFoot: { padding: 20, paddingTop: 14, borderTopWidth: 2, borderTopColor: colors.lavender, gap: 8 },
  searchStatusText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.indigo600, textAlign: 'center' },

  headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' },
  titleWrap: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  countPill: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 4 },
  countText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate900 },
  viewTabs: { flexDirection: 'row', backgroundColor: colors.lavender, borderRadius: radius.pill, padding: 3, gap: 2 },
  viewTab: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8, paddingHorizontal: 14, borderRadius: radius.pill },
  viewTabActive: { backgroundColor: colors.navy },
  viewTabText: { fontFamily: fonts.bodyXBold, fontSize: 13, lineHeight: 20, color: '#5B6785' },
  viewTabTextActive: { color: colors.white },

  emptyState: { color: '#94A3B8', fontStyle: 'italic', fontSize: 13, fontFamily: fonts.bodyMed },
  flex1: { flex: 1 },

  savedHead: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 24 },
});
