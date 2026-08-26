import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
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
  addTrackerItem,
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
import {
  applyDeadlineCheckToInfo,
  intakeExtractAndClassify,
  normalizeVerifiedActionItems,
  slugifyTracker,
  staticGenericChecklist,
  type NormalizedActionItem,
} from '@/lib/tracker';
import {
  assignCalendarColors,
  BUCKET_LABELS,
  computeProgressStatus,
  earliestUpcoming,
  formatMonthDay,
  getDisplayMilestones,
  MONTH_NAMES,
  type CalColor,
  type Milestone,
} from '@/lib/status';

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
import { IconBtn, MiniBadge, PopButton, ReviewBadge, Screen, SoftCard, StatusPill, Txt, usePopInteraction } from '@/ui/components';
import { CalendarIcon, CalendarSyncIcon, ListIcon, RefreshIcon, StarIcon, XIcon } from '@/ui/icons';
import { colors, fonts, popShadow, radius, space } from '@/ui/theme';

// Quest Log — ported from the live app's #page-tracker: header controls (refresh status,
// calendar-sync + Add Opportunity), "Actively Tracked" + count + Calendar/List view-tabs,
// the swimlane month-card calendar, and the list view with pop cards + Saved for Later.
export default function Tracker() {
  const router = useRouter();
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

  // "Add Opportunity" intake dropdown — the retired SPA's #trackerIntakeForm panel.
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [intakeUrl, setIntakeUrl] = useState('');
  const [intakeNotes, setIntakeNotes] = useState('');
  const [intakeBusy, setIntakeBusy] = useState(false);
  const [intakeStatus, setIntakeStatus] = useState('');
  const [intakeError, setIntakeError] = useState<string | null>(null);
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
      if (out.kind === 'not-connected') {
        const url = httpClient.googleCalendarConnectUrl(googleCalendarReturnUri());
        setSyncState('error');
        setSyncNote(
          url
            ? 'Google Calendar isn’t connected yet — opening the connect page…'
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

  // ---------- Intake: add a custom opportunity by URL ----------
  // trackerAnalyzeAndAdd(), ported: extract + classify, push into the right bucket, jump to
  // the new card, then queue the row for the review queue in the background.
  async function analyzeAndAdd() {
    if (intakeBusy) return;
    const url = intakeUrl.trim();
    const notes = intakeNotes.trim();
    setIntakeError(null);
    if (!url) {
      setIntakeError('Paste a URL first.');
      return;
    }
    try {
      new URL(url);
    } catch {
      setIntakeError('That doesn’t look like a valid URL — include https://');
      return;
    }
    setIntakeBusy(true);
    setIntakeStatus('');
    try {
      const extracted = await intakeExtractAndClassify(httpClient.callGemini.bind(httpClient), url, notes);
      const section = extracted.section ?? '';
      const bucket: Bucket = (ALL_BUCKETS as readonly string[]).includes(section)
        ? (section as Bucket)
        : 'researchCompetitions';
      const current = data ?? (await loadTrackerData());
      const name = extracted.name || 'Custom Opportunity';
      const meta = extracted.meta || '';
      const fit = extracted.fit || '';
      const note = extracted.note || 'Added manually via URL.';

      // Register the opportunity in the catalog FIRST, and track it under the id that comes
      // back. That id is the whole reason this happens before the item is built: it is what
      // makes /api/opportunities/<id>/deadline resolve, so a hand-added opportunity gets the
      // same shared, cached, web-searched deadline check a Fresh Finds one does — both on
      // add and on every later "Check for updates". Previously this was fired and forgotten
      // after the fact, the item kept a local slug, and the deadline endpoint 404'd forever.
      //
      // The row lands is_active=false and stays there until someone activates it in the
      // console; being addressable is not being published. If the submission cannot be
      // resolved we fall back to the slug and the item is simply un-auto-checkable, which
      // the refresh now says out loud instead of reporting "no changes found".
      const catalogId = await httpClient.submitUserOpportunity({
        name,
        url,
        type: extracted.category || 'Program',
        section: bucket,
        meta,
        fit,
        note,
        important_dates: extracted.important_dates ?? [],
        requirements: extracted.requirements ?? [],
        apply_url: extracted.apply_url || url,
        category: extracted.category ?? null,
      });
      const id = catalogId ?? slugifyTracker(extracted.name || url, current[bucket].map((i) => i.id));

      // Same two-step sequence a Fresh Finds add uses: the Gemini extraction above, then the
      // shared deadline check overlaid on top of it. A brand-new row is never a cache hit,
      // so this is a real (paid) check; a URL that deduped into an existing catalog row may
      // come back free and already verified.
      // The verified checklist for the row we just resolved. A URL that deduped into an
      // existing catalog row comes back already generated and free; a genuinely new row is
      // generated once here and then cached for every student after this one.
      let sharedItems: NormalizedActionItem[] = [];
      if (catalogId) {
        applyDeadlineCheckToInfo(extracted, await httpClient.getDeadlineCheck(catalogId));
        sharedItems = normalizeVerifiedActionItems(
          (await httpClient.getActionItems(catalogId))?.action_items, catalogId);
      }

      const item: TrackerItem = {
        id,
        name,
        url,
        type: extracted.category || '',
        bucket,
        progressStatus: 'not_started',
        status: ['running', 'not_running', 'rolling', 'unknown'].includes(extracted.status) ? extracted.status : 'unknown',
        meta,
        fit,
        // applyDeadlineCheckToInfo may have replaced `note` with the check's own
        // important_date_note, which is the more authoritative caveat of the two.
        note: extracted.note || note,
        noteType: extracted.status === 'not_running' ? 'flag' : (extracted.noteType || 'plain'),
        importantDates: Array.isArray(extracted.important_dates)
          ? extracted.important_dates
              .filter((d) => d && d.date_iso)
              // verified/sourceUrl survive the deadline-check overlay above; the intake
              // model's own dates never carry them (nothing verified those).
              .map((d) => ({
                label: d.label || 'Date',
                dateISO: d.date_iso,
                type: d.type || 'deadline',
                estimated: d.estimated,
                verified: d.verified,
                sourceUrl: d.source_url ?? null,
              }))
              .sort((a, b) => a.dateISO.localeCompare(b.dateISO))
          : [],
        deadlineLabel: extracted.deadline_label || 'CHECK SITE',
        wasEstimated: !!extracted.was_estimated,
        applyUrl: extracted.apply_url || url,
        applyLabel: extracted.apply_label || 'Apply / learn more',
        // Verified list when the submission resolved to a catalog row; otherwise the
        // static generic checklist (P8) — an unresolvable row has no page anything could
        // have verified, and a generic list asserts nothing, so it cannot be wrong.
        actionItems: sharedItems.length
          ? sharedItems
          : staticGenericChecklist(id, url),
      };
      setData(await addTrackerItem(bucket, item));
      setIntakeStatus(`Added “${item.name}” ✓`);
      setIntakeUrl('');
      setIntakeNotes('');
      // Same treatment a Fresh Finds add gets: badged NEW, floated to the top, jumped to.
      markNewlyAdded([id]);
      setNewIds(new Set([id]));
      goToTrackerCard(id);
    } catch (err) {
      setIntakeError(
        `Couldn’t extract details — this only works with live API access. Error: ${(err as Error).message}`,
      );
    } finally {
      setIntakeBusy(false);
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
          <IconBtn onPress={checkForUpdates}>
            <RefreshIcon size={14} color={refreshing ? colors.slate400 : colors.indigo600} />
          </IconBtn>
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
            <IconBtn onPress={syncing ? undefined : syncToCalendar}>
              {syncing ? <SpinningRefresh size={16} /> : <CalendarSyncIcon size={16} color={colors.navy} />}
            </IconBtn>
          </View>
          <View style={styles.intakeWrap}>
            <PopButton label="Add Opportunity" onPress={() => setIntakeOpen((o) => !o)} />
            {intakeOpen && (
              <View style={styles.intakePanel}>
                <Text style={styles.intakeTitle}>Add Custom Opportunity</Text>
                <Text style={styles.intakeLabel}>URL of the opportunity</Text>
                <TextInput
                  style={styles.intakeInput}
                  value={intakeUrl}
                  onChangeText={setIntakeUrl}
                  placeholder="https://example.com/apply"
                  placeholderTextColor={colors.slate400}
                  autoCapitalize="none"
                  autoCorrect={false}
                  inputMode="url"
                />
                <Text style={styles.intakeLabel}>Extra context (optional)</Text>
                <TextInput
                  style={[styles.intakeInput, styles.intakeArea]}
                  value={intakeNotes}
                  onChangeText={setIntakeNotes}
                  placeholder="Anything you already know..."
                  placeholderTextColor={colors.slate400}
                  multiline
                />
                <PopButton
                  full
                  label={intakeBusy ? 'Fetching and analyzing…' : 'Add'}
                  loading={intakeBusy}
                  onPress={analyzeAndAdd}
                />
                {!!intakeStatus && <Text style={styles.intakeStatusText}>{intakeStatus}</Text>}
                {!!intakeError && (
                  <View style={styles.intakeErrorBox}>
                    <Text style={styles.intakeErrorText}>{intakeError}</Text>
                  </View>
                )}
              </View>
            )}
          </View>
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
    </Screen>
  );
}

// ---------- Calendar (one card-soft holding a swimlane per opportunity type) ----------
function CalendarCard({
  entries,
  onEntryPress,
}: {
  entries: { item: TrackerItem; bucket: Bucket }[];
  onEntryPress: (id: string) => void;
}) {
  const lanes = useMemo(() => {
    const byBucket = new Map<Bucket, { item: TrackerItem; milestones: Milestone[] }[]>();
    entries.forEach(({ item, bucket }) => {
      if (item.status === 'not_running') return;
      const ms = getDisplayMilestones(item);
      if (!ms.length) return;
      const list = byBucket.get(bucket) ?? [];
      list.push({ item, milestones: ms });
      byBucket.set(bucket, list);
    });
    return ALL_BUCKETS.filter((b) => byBucket.has(b)).map((b) => ({ bucket: b, rows: byBucket.get(b)! }));
  }, [entries]);

  // P11: rolling / always-open programs. They carry NO dates by design (G3 — a genuinely
  // continuous program has no deadline to place on a month lane), so without this band the
  // Calendar view simply never shows them and a student scanning "what can I apply to right
  // now" misses every always-open program. The band lists them OUTSIDE the month lanes and
  // the date sort — deliberately no placeholder date is invented to force them onto a lane
  // ("never anchor a date"). Saved-for-later is already excluded upstream (entries), and
  // not_running has no business here. Dated programs whose window is currently open are NOT
  // duplicated into the band: they already sit on a month lane with their real dates.
  const openNow = useMemo(
    () => entries.filter(({ item }) => item.status === 'rolling'),
    [entries],
  );

  const colorMap = useMemo(() => {
    const ids: string[] = [];
    lanes.forEach((l) => l.rows.forEach((r) => r.milestones.forEach(() => ids.push(r.item.id))));
    return assignCalendarColors(ids);
  }, [lanes]);

  if (!lanes.length && !openNow.length) {
    return (
      <SoftCard>
        <Text style={styles.emptyState}>Nothing on the calendar yet — add opportunities via the Finder or the button above.</Text>
      </SoftCard>
    );
  }

  const now = new Date();
  const currentYM = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const next = new Date(now.getFullYear(), now.getMonth() + 1, 1);
  const nextYM = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}`;

  return (
    <SoftCard style={{ gap: 20 }}>
      {openNow.length > 0 && (
        <View style={styles.openNowBand}>
          <Text style={styles.openNowHead}>OPEN NOW — APPLY ANYTIME</Text>
          <View style={styles.openNowRow}>
            {openNow.map(({ item }) => (
              <Pressable key={item.id} onPress={() => onEntryPress(item.id)} style={styles.openNowPill}>
                <Text style={styles.openNowPillText} numberOfLines={1}>
                  {item.name.length > 32 ? item.name.slice(0, 30) + '…' : item.name}
                </Text>
              </Pressable>
            ))}
          </View>
        </View>
      )}
      {lanes.map(({ bucket, rows }) => {
        const byMonth = new Map<string, { day: number; label: string; text: string; type: string; isPast: boolean; venueId: string }[]>();
        rows.forEach(({ item, milestones }) => {
          const shortLabel = item.name.length > 22 ? item.name.slice(0, 20) + '…' : item.name;
          milestones.forEach((m) => {
            const ym = m.date.slice(0, 7);
            const list = byMonth.get(ym) ?? [];
            list.push({ day: parseInt(m.date.slice(8, 10), 10), label: shortLabel, text: m.label, type: m.type, isPast: m.isPast, venueId: item.id });
            byMonth.set(ym, list);
          });
        });
        const months = Array.from(byMonth.keys()).sort();
        return (
          <View key={bucket} style={{ gap: 8 }}>
            <Text style={styles.laneHead}>{BUCKET_LABELS[bucket].toUpperCase()}</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator contentContainerStyle={styles.strip}>
              {months.map((ym) => {
                const isCurrent = ym === currentYM;
                const isNext = ym === nextYM;
                const [y, m] = ym.split('-');
                const evs = (byMonth.get(ym) ?? []).sort((a, b) => a.day - b.day);
                return (
                  <View
                    key={ym}
                    style={[
                      styles.monthCard,
                      isCurrent && [styles.monthCurrent, popShadow(3, colors.indigo)],
                      isNext && [styles.monthNext, popShadow(3, '#A5B4FC')],
                    ]}
                  >
                    <View style={styles.monthHead}>
                      <Text style={styles.monthHeadText}>{MONTH_NAMES[parseInt(m, 10) - 1]} {y}</Text>
                      {isCurrent && <View style={styles.nowBadge}><Text style={styles.nowBadgeText}>NOW</Text></View>}
                      {isNext && <View style={[styles.nowBadge, styles.nextBadge]}><Text style={[styles.nowBadgeText, { color: '#1E1B4B' }]}>NEXT</Text></View>}
                    </View>
                    <View style={{ gap: 6 }}>
                      {evs.map((e, i) => {
                        const c: CalColor = colorMap.get(e.venueId) ?? { bg: '#eee', border: '#999', text: '#333' };
                        return (
                          <Pressable
                            key={i}
                            onPress={() => onEntryPress(e.venueId)}
                            style={[styles.entry, { backgroundColor: c.bg, borderLeftColor: c.border }, e.isPast && styles.entryPast]}
                          >
                            <Text style={[styles.entryDay, { color: c.text }]}>{e.day}</Text>
                            <View style={styles.flex1}>
                              <Text style={[styles.entryName, { color: c.text }]}>{e.label}</Text>
                              <Text style={[styles.entryText, { color: c.text }]}>
                                — {e.text} <Text style={[styles.entryType, { color: c.text }]}>{e.type.toUpperCase()}{e.isPast ? ' · PASSED' : ''}</Text>
                              </Text>
                            </View>
                          </Pressable>
                        );
                      })}
                    </View>
                  </View>
                );
              })}
            </ScrollView>
          </View>
        );
      })}
    </SoftCard>
  );
}

// ---------- List card (trackerCardHTML) ----------
function ListCard({
  item,
  bucket,
  isSaved,
  isNew,
  onRemove,
  onToggleSaved,
  highlighted,
  cardRef,
  reviewOpen,
  onToggleReview,
}: {
  item: TrackerItem;
  bucket: Bucket;
  isSaved: boolean;
  isNew?: boolean;
  onRemove: (id: string) => void;
  onToggleSaved: (id: string) => void;
  highlighted?: boolean;
  cardRef?: (el: unknown) => void;
  reviewOpen?: boolean;
  onToggleReview?: (id: string) => void;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const cardPop = usePopInteraction(4, colors.navy, 2);
  const applyPop = usePopInteraction(3, colors.navy, 1);
  const milestones = getDisplayMilestones(item);
  const allPast = milestones.length > 0 && milestones.every((m) => m.isPast);
  // Dates rolled forward to the next annual cycle rather than read off the page.
  const projected = milestones.some((m) => m.projected);
  const progress = computeProgressStatus(item);
  const notRunning = item.status === 'not_running';
  // Rolling / always-open: no cycle, no dates — the deadline checker's "apply anytime"
  // answer (G3). It reads as Happening Now via computeProgressStatus; the badge and note
  // below make the "no dates is correct here" explicit so an empty card doesn't look broken.
  const rolling = item.status === 'rolling';

  // Group milestone rows by year, split into two balanced columns past 5 entries.
  const entries: ({ kind: 'tag'; year: string; cont?: boolean } | { kind: 'date'; m: Milestone })[] = [];
  const byYear = new Map<string, Milestone[]>();
  milestones.forEach((m) => {
    const y = m.date.slice(0, 4);
    const list = byYear.get(y) ?? [];
    list.push(m);
    byYear.set(y, list);
  });
  Array.from(byYear.keys()).sort().forEach((y) => {
    entries.push({ kind: 'tag', year: y });
    byYear.get(y)!.forEach((m) => entries.push({ kind: 'date', m }));
  });
  let col1 = entries;
  let col2: typeof entries = [];
  if (entries.length > 5) {
    const size = Math.ceil(entries.length / 2);
    col1 = entries.slice(0, size);
    col2 = entries.slice(size);
    if (col2.length && col2[0].kind !== 'tag') {
      let lastYear: string | null = null;
      for (let i = size - 1; i >= 0; i--) {
        const e = entries[i];
        if (e.kind === 'tag') { lastYear = e.year; break; }
      }
      if (lastYear) col2 = [{ kind: 'tag', year: lastYear, cont: true }, ...col2];
    }
  }

  const renderCol = (col: typeof entries) => (
    <View style={styles.flex1}>
      {col.map((e, i) =>
        e.kind === 'tag' ? (
          <View key={i} style={styles.yearTag}><Text style={styles.yearTagText}>{e.year}{e.cont ? ' (cont.)' : ''}</Text></View>
        ) : (
          <View key={i} style={styles.dateRow}>
            <Text style={styles.dateRowDate}>{formatMonthDay(e.m.date)}</Text>
            <Text style={styles.dateRowLabel}>
              {e.m.label}
              {/* Per-date, so a confirmed deadline and a projected opening on the SAME card
                  are told apart. The card-level "Predicted dates from past cycle" banner
                  can only say that something here is a guess. Suppressed when the label
                  already says it — some rows were written before the flag existed and the
                  model put "(estimated)" in the label text itself. */}
              {e.m.estimated && !/estimat/i.test(e.m.label) && (
                <Text style={styles.dateRowEstimated}>{'  (estimated)'}</Text>
              )}
              {/* P7 verified marker — shown only on verified === true (P6c found this exact
                  date on a page it fetched; absent = unknown, never rendered as proof). Green
                  matches the official-tier task chip: one visual language for "we checked
                  this against the source" (T4). Tapping opens the evidence page when the
                  check recorded one. Mutually exclusive with (estimated) by design — an
                  estimated date is verified:false. */}
              {e.m.verified === true && !e.m.estimated && (
                <Text
                  style={styles.dateRowVerified}
                  onPress={e.m.sourceUrl ? () => Linking.openURL(e.m.sourceUrl as string) : undefined}
                >
                  {e.m.sourceUrl ? '  ✓ verified ↗' : '  ✓ verified'}
                </Text>
              )}
            </Text>
          </View>
        ),
      )}
    </View>
  );

  return (
    <Pressable
      ref={cardRef as never}
      {...cardPop.handlers}
      // reviewOpen raises this card above the ones after it in source order, or the popover is
      // painted over by the next card instead of overlapping it.
      style={[styles.listCard, cardPop.shadowStyle, notRunning && { opacity: 0.6 }, highlighted && styles.listCardHighlighted, reviewOpen && styles.listCardReviewOpen]}
    >
      {isNew && (
        <View style={styles.newMarker}>
          <Text style={styles.newMarkerText}>New</Text>
        </View>
      )}
      <View style={styles.cardTop}>
        <View style={styles.badgeRow}>
          <MiniBadge label={BUCKET_LABELS[bucket]} bg={colors.violet200} fg={colors.violet900} />
          {notRunning && <MiniBadge label="Not running" bg="#FFE4E6" fg="#881337" />}
          {rolling && <MiniBadge label="Open now" bg="#DCFCE7" fg="#166534" />}
          <ReviewBadge
            status={item.reviewStatus}
            summary={item.reviewSummary}
            open={!!reviewOpen}
            onToggle={() => onToggleReview?.(item.id)}
          />
        </View>
        <View style={styles.iconRow}>
          <IconBtn onPress={() => onToggleSaved(item.id)}>
            <StarIcon size={15} color={isSaved ? colors.orange : colors.navy} filled={isSaved} />
          </IconBtn>
          <IconBtn onPress={() => onRemove(item.id)}>
            <XIcon size={14} color={colors.slate400} />
          </IconBtn>
        </View>
      </View>

      <View>
        <Pressable onPress={() => item.url && Linking.openURL(item.url)}>
          <Text style={styles.cardName}>{item.name}</Text>
        </Pressable>
        {!!item.meta && <Text style={styles.cardMeta} numberOfLines={1}>{item.meta}</Text>}
      </View>

      {(item.wasEstimated || projected) && !notRunning && (
        <View style={styles.estimatedNote}>
          <Text style={styles.estimatedText}>Predicted dates from past cycle.</Text>
        </View>
      )}
      {/* Only the "this program is over" case is worth a banner. The running-but-past-dates
          note said nothing the dates themselves don't and appeared on most cards. */}
      {allPast && item.status !== 'running' && (
        <View style={[styles.estimatedNote, styles.staleBad]}>
          <Text style={[styles.estimatedText, styles.staleBadText]}>
            ⚠ No upcoming dates — this program's last cycle has ended.
          </Text>
        </View>
      )}

      {rolling && (
        <View style={[styles.estimatedNote, styles.rollingNote]}>
          <Text style={[styles.estimatedText, styles.rollingText]}>
            Open now — rolling admission, apply anytime.
          </Text>
        </View>
      )}

      {milestones.length > 0 && (
        <View style={col2.length ? styles.dateCols : undefined}>
          {renderCol(col1)}
          {col2.length > 0 && renderCol(col2)}
        </View>
      )}

      <Pressable onPress={() => setShowDetails(!showDetails)}>
        <Text style={styles.detailsToggle}>▶ Show details</Text>
      </Pressable>
      {showDetails && (
        <View style={styles.detailsBox}>
          {!!item.fit && <Text style={styles.detailsText}>{item.fit}</Text>}
          {!!item.note && <Text style={styles.detailsNote}>{item.note}</Text>}
        </View>
      )}

      <View style={styles.cardFoot}>
        <StatusPill status={progress} />
        {!!(item.applyUrl || item.url) && (
          <Pressable
            onPress={() => Linking.openURL((item.applyUrl || item.url) as string)}
            {...applyPop.handlers}
            style={[styles.applyBtn, applyPop.shadowStyle]}
          >
            <Text style={styles.applyText}>{item.applyLabel || 'Apply'}</Text>
          </Pressable>
        )}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  // zIndex here (and on topRight) is what keeps the Add Opportunity dropdown above the
  // cards below it: the panel is absolute inside intakeWrap, but without a z-index on its
  // ancestors the whole header row still paints under later siblings in the Screen.
  topRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: space.lg, flexWrap: 'wrap', zIndex: 50 },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  lastChecked: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate400 },
  topRight: { flexDirection: 'row', alignItems: 'center', gap: 10, zIndex: 50 },
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

  intakeWrap: { position: 'relative', zIndex: 50 },
  intakePanel: {
    position: 'absolute',
    top: '100%',
    right: 0,
    marginTop: 8,
    width: 340,
    backgroundColor: colors.white,
    borderWidth: 2,
    borderColor: colors.slate900,
    borderRadius: radius.lg,
    padding: 16,
    zIndex: 50,
  },
  intakeTitle: { fontFamily: fonts.display, fontSize: 16, color: colors.ink, marginBottom: 12 },
  intakeLabel: { fontFamily: fonts.bodyBold, fontSize: 10, color: colors.slate500, textTransform: 'uppercase', marginBottom: 4 },
  intakeInput: { borderWidth: 2, borderColor: colors.slate900, borderRadius: 8, padding: 8, fontFamily: fonts.bodyMed, fontSize: 14, color: colors.ink, marginBottom: 12 },
  intakeArea: { minHeight: 56, textAlignVertical: 'top' },
  intakeStatusText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.indigo600, textAlign: 'center', marginTop: 8 },
  intakeErrorBox: { backgroundColor: colors.redSoft, borderWidth: 2, borderColor: '#881337', borderRadius: 8, padding: 8, marginTop: 8 },
  intakeErrorText: { fontFamily: fonts.bodyBold, fontSize: 12, color: '#881337' },

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

  laneHead: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500, letterSpacing: 0.6, textTransform: 'uppercase' },
  // P11: the rolling-programs band. Same green family as the card's "Open now" badge and
  // the rolling note — one visual language for "open right now".
  openNowBand: { backgroundColor: '#DCFCE7', borderWidth: 2, borderColor: '#166534', borderRadius: radius.lg, padding: 12, gap: 8 },
  openNowHead: { fontFamily: fonts.bodyXBold, fontSize: 11, color: '#166534', letterSpacing: 0.55 },
  openNowRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  openNowPill: { backgroundColor: colors.white, borderWidth: 2, borderColor: '#166534', borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 5, maxWidth: '100%' },
  openNowPillText: { fontFamily: fonts.bodyBold, fontSize: 12, color: '#166534' },
  strip: { gap: 16, paddingBottom: 8 },
  monthCard: { width: 200, backgroundColor: colors.slate50, borderWidth: 2, borderColor: '#CBD5E1', borderRadius: radius.lg, padding: 12 },
  monthCurrent: { borderWidth: 3, borderColor: colors.indigo, backgroundColor: '#EEF2FF' },
  monthNext: { borderWidth: 3, borderColor: '#A5B4FC', backgroundColor: '#F5F5FF' },
  monthHead: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 },
  monthHeadText: { fontFamily: fonts.bodyXBold, fontSize: 11, color: colors.slate500, letterSpacing: 0.55, textTransform: 'uppercase' },
  nowBadge: { backgroundColor: colors.indigo, borderRadius: radius.pill, paddingHorizontal: 6, paddingVertical: 2 },
  nextBadge: { backgroundColor: '#A5B4FC' },
  nowBadgeText: { fontFamily: fonts.bodyXBold, fontSize: 9, color: colors.white, letterSpacing: 0.45 },
  entry: { borderRadius: 8, paddingVertical: 6, paddingHorizontal: 8, borderLeftWidth: 3, flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  entryPast: { opacity: 0.5 },
  entryDay: { fontFamily: fonts.bodyXBold, fontSize: 18, lineHeight: 18 },
  entryText: { fontFamily: fonts.bodyMed, fontSize: 11, lineHeight: 15 },
  entryName: { fontFamily: fonts.bodyBold, fontSize: 11 },
  entryType: { fontFamily: fonts.bodyMed, fontSize: 9, opacity: 0.7, marginTop: 1 },
  flex1: { flex: 1 },

  savedHead: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 24 },

  listCard: { backgroundColor: colors.white, borderWidth: 4, borderColor: colors.slate900, borderRadius: radius.xxl, padding: 24, gap: 16 },
  listCardReviewOpen: { zIndex: 20 },
  listCardHighlighted: { backgroundColor: colors.lavender, borderColor: colors.indigo },
  // The retired SPA's newBanner, restored value-for-value (script.js trackerCardHTML): a
  // lime tab notched over the card's top-left corner, not a badge in the flow. The negative
  // offsets put it OUTSIDE the 4px border, which is what makes it read as a marker ON the
  // card rather than content IN it — so listCard must never gain overflow: 'hidden'.
  newMarker: {
    position: 'absolute', left: -8, top: -8, zIndex: 10,
    backgroundColor: colors.lime, borderRadius: 8,
    borderWidth: 2, borderColor: colors.navy,
    paddingHorizontal: 10, paddingVertical: 3,
    ...popShadow(2),
  },
  newMarkerText: { fontFamily: fonts.bodyBold, fontSize: 10, letterSpacing: 0.5, textTransform: 'uppercase', color: colors.ink },
  // zIndex on both rows is what lets ReviewBadge's popover paint OVER the card's dates and
  // meta below it rather than under them — RN-web makes every View its own stacking context
  // at z-index 0, so the popover cannot escape this row on its own. See the STACKING note on
  // ReviewBadge in src/ui/components.tsx. Kept at 1, well clear of topRow's 50.
  cardTop: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, zIndex: 1 },
  badgeRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', flex: 1, zIndex: 1 },
  iconRow: { flexDirection: 'row', gap: 6 },
  cardName: { fontFamily: fonts.display, fontSize: 30, lineHeight: 34, color: colors.slate900 },
  cardMeta: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.slate500, marginTop: 4 },
  estimatedNote: { backgroundColor: '#FEF08A', borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, paddingHorizontal: 16, paddingVertical: 10 },
  estimatedText: { fontFamily: fonts.bodyBold, fontSize: 12, color: '#92400E' },
  staleBad: { backgroundColor: '#FFE4E6' },
  staleBadText: { color: '#9F1239' },
  rollingNote: { backgroundColor: '#DCFCE7' },
  rollingText: { color: '#166534' },
  dateCols: { flexDirection: 'row', gap: 24 },
  yearTag: { backgroundColor: '#EEE9DD', borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, alignSelf: 'flex-start', marginTop: 10, marginBottom: 6 },
  yearTagText: { fontFamily: fonts.bodyXBold, fontSize: 10, color: '#0F1C33', letterSpacing: 0.3 },
  dateRow: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 9, borderBottomWidth: 1, borderBottomColor: '#EEEEEE' },
  dateRowDate: { fontFamily: fonts.bodyBold, fontSize: 14, color: '#0F1C33', width: 52 },
  dateRowLabel: { fontFamily: fonts.bodyMed, fontSize: 14, color: '#33404F', flex: 1 },
  dateRowEstimated: { fontFamily: fonts.bodyMed, fontSize: 12, color: '#92400E' },
  // Same green as the official-tier source chip — the shared "checked against the source"
  // colour (T4).
  dateRowVerified: { fontFamily: fonts.bodyBold, fontSize: 11, color: '#166534' },
  detailsToggle: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.indigo600 },
  detailsBox: { backgroundColor: colors.slate50, borderWidth: 1, borderColor: colors.slate200, borderRadius: radius.md, padding: 12, gap: 4 },
  detailsText: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500 },
  detailsNote: { fontFamily: fonts.bodyMed, fontSize: 10, color: colors.slate500, fontStyle: 'italic' },
  cardFoot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingTop: 12, borderTopWidth: 2, borderTopColor: colors.slate100, flexWrap: 'wrap' },
  applyBtn: { backgroundColor: '#F97316', borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 20, paddingVertical: 10 },
  applyText: { fontFamily: fonts.bodyXBold, fontSize: 12, color: colors.slate900 },
});
