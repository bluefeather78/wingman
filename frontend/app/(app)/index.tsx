import { LinearGradient } from 'expo-linear-gradient';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { ActivityIndicator, Linking, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import {
  addUserTask,
  deleteTrackerTask,
  loadTrackerData,
  loadTrackerSaved,
  peekTrackerData,
  peekTrackerSaved,
  restoreRemovedTasks,
  saveTrackerData,
  syncTrackerFromCatalog,
  isSetAsideTask,
  taskTrustTier,
  type ActionItem,
  type SavedState,
  type TrackerData,
} from '@/api/trackerStore';
import { useAuth } from '@/auth/AuthContext';
import {
  allTodoUnitCounts,
  computeProgressStatus,
  computeStats,
  getAllDeadlineItems,
  shortDate,
} from '@/lib/status';
import {
  ACTION_ITEM_STATUS_LABEL,
  LegendItem,
  Logo,
  PopButton,
  ProgressTrack,
  PROGRESS_STATUS_LABEL,
  Screen,
  SoftCard,
  StatusPill,
  Txt,
  type OppStatus,
  type TaskStatus,
} from '@/ui/components';
import { colors, fonts, radius, space } from '@/ui/theme';

interface StoredProfile {
  synthesized?: string;
}

// One task row. Extracted when the list split into page-backed and generic groups, so the
// groups cannot drift in how a row actually renders — only the heading above them differs.
// The per-row source chips were removed in the 2026-08-26 redesign: the group heading
// already states the provenance, so the chip said the same thing twice per row.
function TaskRow({ ai, onPress, onDelete }: {
  ai: ActionItem;
  onPress: () => void;
  onDelete: () => void;
}) {
  const [delHovered, setDelHovered] = useState(false);
  // The trailing ↗ prefers the step's ACTION url. A verified task with no action url falls
  // back to its EVIDENCE url (the fetched page the quote was verified against) — the link
  // the removed source chip used to carry, without which a trusted-guide step would have no
  // way back to its source.
  const tier = ai.origin === 'user' ? null : taskTrustTier(ai);
  const linkUrl = ai.url || (tier && tier !== 'generic' ? ai.sourceUrl : null) || null;
  return (
    <View style={styles.taskRow}>
      <View style={styles.taskLeft}>
        <Text style={[styles.taskText, (ai.state === 'completed' || isSetAsideTask(ai)) && styles.taskDone]}>
          {ai.text}
          {!!linkUrl && (
            <Text style={styles.taskStepLink} onPress={() => Linking.openURL(linkUrl)}>
              {'  ↗'}
            </Text>
          )}
        </Text>
      </View>
      {/* Tapping cycles the state, and "Not Needed" is the last stop before it wraps back
          round — a step that does not apply to THIS student, set aside rather than deleted.
          The delete ✕ beside it is the P10 remove: for a catalog task it writes a per-user
          tombstone (the shared list regenerates, so a plain splice would come straight
          back), for the student's own task it deletes outright. Reversible via the Restore
          line under the list. The fixed-width right column keeps the pills vertically
          aligned across rows instead of ragged against each row's text length. */}
      <View style={styles.taskRight}>
        <StatusPill
          status={(ai.state as TaskStatus) in ACTION_ITEM_STATUS_LABEL ? (ai.state as TaskStatus) : 'not_started'}
          kind="task"
          onPress={onPress}
        />
        <Pressable
          onPress={onDelete}
          hitSlop={8}
          onHoverIn={() => setDelHovered(true)}
          onHoverOut={() => setDelHovered(false)}
        >
          <Text style={[styles.taskDelete, delHovered && styles.taskDeleteHover]}>✕</Text>
        </Pressable>
      </View>
    </View>
  );
}

// Home Base — ported from the live app's #page-home: welcome banner + DUE SOON badge,
// profile teaser card, "What You're Chasing" (segmented progress + legend + CTA), and
// "Your Next Moves" (task pills + task progress + the full tracked list).
export default function Home() {
  const router = useRouter();
  const { user } = useAuth();
  // Seed from whatever the client already has. This screen is remounted by expo-router on
  // every visit, so without it a tab switch back to Home Base showed a full-screen spinner
  // for a round trip it had already paid for once. The fetch below still runs and still
  // overwrites these — the cache only decides whether the student watches it happen.
  const cachedProfile = httpClient.peekData<StoredProfile>('student-profile');
  const [data, setData] = useState<TrackerData | null>(() => peekTrackerData() ?? null);
  const [saved, setSaved] = useState<SavedState>(() => peekTrackerSaved() ?? {});
  const [profile, setProfile] = useState<string>(cachedProfile?.synthesized ?? '');
  const [tasksOpen, setTasksOpen] = useState(false);
  // Every card below has an empty state that is indistinguishable from "not loaded yet",
  // so rendering before the first load resolves flashes "Your profile is empty" / "Nothing
  // here yet" on every tab switch. Hold the shell until the fetch lands — unless the cache
  // already gave us a tracker to draw, in which case there is nothing to hold for.
  const [loaded, setLoaded] = useState(() => peekTrackerData() !== undefined);

  // Cycle an action item's status (not_started → in_progress → completed → …), persisting
  // to the shared tracker data — ported from cycleActionItemState().
  const NEXT_STATE: Record<string, TaskStatus> = { not_started: 'in_progress', in_progress: 'completed', completed: 'not_needed', not_needed: 'not_started' };
  async function cycleActionItem(itemId: string, actionId: string) {
    if (!data) return;
    const next: TrackerData = { ...data };
    for (const bucket of Object.keys(next) as (keyof TrackerData)[]) {
      next[bucket] = next[bucket].map((it) =>
        it.id === itemId
          ? { ...it, actionItems: (it.actionItems ?? []).map((ai) => (ai.id === actionId ? { ...ai, state: NEXT_STATE[ai.state] ?? 'not_started' } : ai)) }
          : it,
      );
    }
    setData(next);
    await saveTrackerData(next);
  }

  // ---------- P10: per-user task delete / restore / add ----------
  // Draft text for each item's "add a task" input, keyed by item id.
  const [taskDrafts, setTaskDrafts] = useState<Record<string, string>>({});

  async function deleteTask(itemId: string, actionId: string) {
    setData(await deleteTrackerTask(itemId, actionId));
  }

  async function restoreTasks(itemId: string) {
    setData(await restoreRemovedTasks(itemId));
    // The restored catalog tasks come back through the merge on the next task pull; force a
    // free sync now so the student watches them return instead of wondering if it worked.
    const r = await syncTrackerFromCatalog({ force: true }).catch(() => null);
    if (r?.data) setData(r.data);
  }

  async function submitTask(itemId: string) {
    const text = (taskDrafts[itemId] ?? '').trim();
    if (!text) return;
    const { data: next, added } = await addUserTask(itemId, text);
    setData(next);
    if (added) setTaskDrafts((d) => ({ ...d, [itemId]: '' }));
  }

  useFocusEffect(
    useCallback(() => {
      let alive = true;
      Promise.all([
        loadTrackerData().catch(() => null),
        loadTrackerSaved().catch(() => ({}) as SavedState),
        httpClient.loadData<StoredProfile>('student-profile').catch(() => null),
      ]).then(([d, s, p]) => {
        if (!alive) return;
        if (d) setData(d);
        setSaved(s);
        setProfile(p?.synthesized ?? '');
        setLoaded(true);
      });
      // Free catalog sync (throttled, shared with the Quest Log): Home Base also renders
      // status/next-moves from the tracker snapshot, so it must pick up catalog updates too.
      // No paid check.
      syncTrackerFromCatalog()
        .then((r) => {
          if (alive && r.updated && r.data) setData(r.data);
        })
        .catch(() => null);
      return () => {
        alive = false;
      };
    }, []),
  );

  // On RN-web a nested Pressable's click bubbles to the card wrapping it, so an inner CTA
  // must swallow the event or both destinations fire.
  const stop = (e?: { stopPropagation?: () => void }) => e?.stopPropagation?.();
  const goProfile = () => router.push('/(app)/profile');

  const stats = useMemo(() => (data ? computeStats(data, saved) : { total: 0, not_started: 0, in_progress: 0, completed: 0 }), [data, saved]);
  const upcoming = useMemo(() => (data ? getAllDeadlineItems(data, saved) : []), [data, saved]);
  const { counts: taskCounts, total: taskTotal } = useMemo(() => allTodoUnitCounts(upcoming), [upcoming]);

  // Home progress bar shows in_progress + not_started segments only (script.js renderStats).
  const OPP_ORDER: OppStatus[] = ['in_progress', 'not_started'];
  const OPP_COLOR: Record<OppStatus, string> = { in_progress: colors.teal, not_started: colors.mint, completed: colors.peach };
  const oppSegments = stats.total
    ? OPP_ORDER.map((k) => ({ pct: (stats[k] / stats.total) * 100, color: OPP_COLOR[k] }))
    : [];

  const TASK_ORDER: OppStatus[] = ['not_started', 'in_progress', 'completed'];
  const TASK_COLOR: Record<OppStatus, string> = { not_started: colors.orange, in_progress: colors.teal, completed: colors.mint };
  const taskSegments = taskTotal
    ? TASK_ORDER.map((k) => ({ pct: (taskCounts[k] / taskTotal) * 100, color: TASK_COLOR[k] }))
    : [];

  if (!loaded) {
    return (
      <Screen scroll={false}>
        <View style={styles.loadingWrap}>
          <ActivityIndicator color={colors.navy} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      {/* Welcome banner */}
      <SoftCard style={styles.banner} hoverTint>
        <View style={styles.bannerLeft}>
          <Logo size={32} />
          <Txt variant="h1" style={styles.greeting}>
            Hey <Txt variant="h1" style={{ color: colors.orange }}>{user?.firstName || 'there'}</Txt>, ready?
          </Txt>
        </View>
        <View style={styles.statPills}>
          <View style={styles.statPill}>
            <Txt style={styles.statNum}>{stats.total}</Txt>
            <Txt style={styles.statLabel}>Quests being tracked</Txt>
          </View>
          <View style={styles.statPill}>
            <Txt style={styles.statNum}>{taskCounts.not_started}</Txt>
            <Txt style={styles.statLabel}>Tasks not started yet</Txt>
          </View>
          <View style={styles.statPill}>
            <Txt style={styles.statNum}>{taskCounts.in_progress}</Txt>
            <Txt style={styles.statLabel}>Tasks in progress</Txt>
          </View>
        </View>
      </SoftCard>

      {/* Profile teaser */}
      {profile ? (
        <SoftCard style={{ gap: space.lg }} hoverTint onPress={goProfile}>
          <View style={styles.rowBetween}>
            <Txt variant="h2" style={styles.cardTitle}>Your Story So Far</Txt>
            <PopButton label="View & deepen it →" small square onPress={(e) => { stop(e); goProfile(); }} />
          </View>
          <Txt style={styles.teaserText} numberOfLines={3}>{profile}</Txt>
        </SoftCard>
      ) : (
        <Pressable onPress={goProfile} style={styles.clickable}>
          <LinearGradient
            colors={[colors.teal, colors.navy]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.emptyProfile}
          >
            <View style={styles.flex1}>
              <Txt variant="h3" style={styles.onDark}>Your profile is empty</Txt>
              <Txt variant="body" style={styles.onDarkSoft}>
                Every match in the Finder gets better once we know you. Takes 2 minutes — go build it now.
              </Txt>
            </View>
            <PopButton label="Build my profile" variant="secondary" onPress={(e) => { stop(e); goProfile(); }} />
          </LinearGradient>
        </Pressable>
      )}

      {/* What You're Chasing */}
      <SoftCard style={{ gap: space.lg }} hoverTint onPress={() => router.push('/(app)/tracker')}>
        <View style={styles.rowBetween}>
          <Txt variant="h2" style={styles.cardTitle}>What You're Chasing</Txt>
          <View style={styles.trackedPill}>
            <Txt style={styles.trackedText}>{stats.total} tracked</Txt>
          </View>
        </View>
        {stats.total > 0 ? (
          <>
            <ProgressTrack segments={oppSegments} />
            <View style={styles.legend}>
              {OPP_ORDER.map((k) => (
                <LegendItem key={k} color={OPP_COLOR[k]} label={`${PROGRESS_STATUS_LABEL[k]} (${stats[k]})`} />
              ))}
            </View>
            <PopButton
              label="Look for Fresh Finds"
              variant="secondary"
              small
              square
              textStyle={styles.freshFindsText}
              onPress={(e) => { stop(e); router.push('/(app)/finder'); }}
              style={[styles.selfStart, styles.freshFindsBtn]}
            />
          </>
        ) : (
          <>
            <ProgressTrack segments={[]} />
            <Txt variant="small" style={styles.emptyState}>Nothing here yet.</Txt>
            <PopButton label="Find your first opportunity to track" onPress={(e) => { stop(e); router.push('/(app)/finder'); }} style={styles.selfStart} />
          </>
        )}
      </SoftCard>

      {/* Your Next Moves */}
      <SoftCard style={{ gap: space.lg }} hoverTint onPress={upcoming.length ? () => setTasksOpen(true) : undefined}>
        <View style={styles.rowBetween}>
          <Txt variant="h2" style={styles.cardTitle}>Your Next Moves</Txt>
          <View style={styles.pillRow}>
            {/* 'not_needed' joins the row only when something is in it: it is an exception
                state, and a permanent "0 Not Needed" pill would read as a fourth stage of
                the workflow rather than an escape hatch. */}
            {(['not_started', 'in_progress', 'completed', 'not_needed'] as TaskStatus[])
              .filter((k) => k !== 'not_needed' || taskCounts.not_needed > 0)
              .map((k) => (
              <StatusPill key={k} status={k} kind="task" label={`${taskCounts[k]} ${ACTION_ITEM_STATUS_LABEL[k]}`} />
            ))}
          </View>
        </View>
        <ProgressTrack segments={taskSegments} />
        {upcoming.length === 0 ? (
          <Txt variant="small" style={styles.emptyState}>Nothing tracked yet — you're all caught up.</Txt>
        ) : (
          <View>
            {upcoming.map(({ item, nextDate, nextLabel }) => {
              const taskCount = (item.actionItems ?? []).length;
              return (
                <View key={item.id} style={styles.todoRow}>
                  <View style={styles.flex1}>
                    <Txt style={styles.todoName} numberOfLines={1}>{item.name}</Txt>
                    <Txt style={styles.todoMeta}>
                      {nextDate ? `${shortDate(nextDate)} · ${nextLabel}` : nextLabel}
                      {taskCount ? ` · ${taskCount} task${taskCount > 1 ? 's' : ''}` : ''}
                    </Txt>
                  </View>
                  <StatusPill status={computeProgressStatus(item)} />
                </View>
              );
            })}
            <View style={styles.seeAllWrap}>
              <PopButton
                label="See all tasks"
                variant="secondary"
                small
                square
                textStyle={styles.freshFindsText}
                style={styles.freshFindsBtn}
                onPress={(e) => { stop(e); setTasksOpen(true); }}
              />
            </View>
          </View>
        )}
      </SoftCard>

      {/* "All Your Tasks" modal — ported from the live app's #todoModal. */}
      <Modal visible={tasksOpen} transparent animationType="fade" onRequestClose={() => setTasksOpen(false)}>
        <Pressable style={styles.modalScrim} onPress={() => setTasksOpen(false)}>
          <Pressable style={styles.modalPanel} onPress={(e) => e.stopPropagation()}>
            <ScrollView contentContainerStyle={styles.modalScroll} showsVerticalScrollIndicator={false}>
              <View style={styles.modalHead}>
                <View style={styles.flex1}>
                  <Txt variant="h2" style={styles.cardTitle}>All Your Tasks</Txt>
                  <Text style={styles.modalSub}>Everything you're tracking - manage it all in one place</Text>
                </View>
                <Pressable onPress={() => setTasksOpen(false)} hitSlop={10}>
                  <Text style={styles.modalClose}>✕</Text>
                </Pressable>
              </View>
              {upcoming.map(({ item, nextDate, nextLabel }) => {
                // Tasks grouped by trust tier — the P7 gradient. `taskTrustTier` is the only
                // test (it builds on isPageBackedTask): a task with no `basis` counts as
                // generic, because those came from a prompt told to fill gaps with "what's
                // typical", i.e. to make something up. A page-backed task verified against
                // an operator-approved guide gets its own middle group, labelled by the
                // guide's domain — verified the same way as official, trusted less.
                const allTasks = item.actionItems ?? [];
                // The student's own tasks are their own group (P10) — they'd otherwise
                // classify as generic, and "Typical steps" is not what "my own reminder to
                // email my counselor" is.
                const userTasks = allTasks.filter((ai) => ai.origin === 'user');
                const catalogTasks = allTasks.filter((ai) => ai.origin !== 'user');
                const official = catalogTasks.filter((ai) => taskTrustTier(ai) === 'official');
                const trusted = catalogTasks.filter((ai) => taskTrustTier(ai) === 'trusted');
                const generic = catalogTasks.filter((ai) => taskTrustTier(ai) === 'generic');
                const trustedDomains = [...new Set(trusted.map((ai) => ai.sourceDomain).filter(Boolean))];
                const trustedHeading = trustedDomains.length === 1
                  ? `From a trusted guide · ${trustedDomains[0]}`
                  : 'From trusted guides';
                const removedCount = (item.removedTasks ?? []).length;
                return (
                <View key={item.id} style={styles.taskCard}>
                  <View style={styles.taskCardHead}>
                    <View style={styles.flex1}>
                      {/* The name is the link to the program's own page — ported from the old
                          #todoModal, where every task card's title was an <a href={item.url}>.
                          Underlined rather than bare: it was invisible as an affordance in RN. */}
                      {item.url ? (
                        <Text
                          style={[styles.taskCardName, styles.taskCardNameLink]}
                          numberOfLines={1}
                          onPress={() => Linking.openURL(item.url as string)}
                        >
                          {item.name}
                        </Text>
                      ) : (
                        <Text style={styles.taskCardName} numberOfLines={1}>{item.name}</Text>
                      )}
                      {!!item.meta && <Text style={styles.taskCardMeta} numberOfLines={1}>{item.meta}</Text>}
                    </View>
                    <StatusPill status={computeProgressStatus(item)} />
                  </View>
                  <Text style={styles.taskCardDate}>
                    {nextDate ? `${shortDate(nextDate)} · ${nextLabel}` : nextLabel}{item.wasEstimated ? ' (est.)' : ''}
                  </Text>
                  {allTasks.length ? (
                    <View style={styles.taskRows}>
                      {/* Three groups, highest trust first, and the heading is the whole
                          point: a step we can point at a line of the program's OWN page
                          for, a step a trusted guide said (verified the same way, trusted
                          less — logistics only, never eligibility, enforced server-side),
                          and a step that is merely how applications usually work.
                          Rendering them alike is what let an invented "Algebra 2"
                          prerequisite read as fact. The dates next to these carry
                          "(estimated)"/verified markers for exactly this reason. */}
                      {official.length > 0 && (
                        <>
                          <Text style={styles.taskGroupLabel}>From the program's own page</Text>
                          {official.map((ai) => (
                            <TaskRow key={ai.id} ai={ai} onPress={() => cycleActionItem(item.id, ai.id)} onDelete={() => deleteTask(item.id, ai.id)} />
                          ))}
                        </>
                      )}
                      {trusted.length > 0 && (
                        <>
                          <Text style={styles.taskGroupLabel}>{trustedHeading}</Text>
                          {trusted.map((ai) => (
                            <TaskRow key={ai.id} ai={ai} onPress={() => cycleActionItem(item.id, ai.id)} onDelete={() => deleteTask(item.id, ai.id)} />
                          ))}
                        </>
                      )}
                      {generic.length > 0 && (
                        <>
                          <Text style={styles.taskGroupLabel}>
                            Typical steps — confirm on the site
                          </Text>
                          {generic.map((ai) => (
                            <TaskRow key={ai.id} ai={ai} onPress={() => cycleActionItem(item.id, ai.id)} onDelete={() => deleteTask(item.id, ai.id)} />
                          ))}
                        </>
                      )}
                      {userTasks.length > 0 && (
                        <>
                          <Text style={styles.taskGroupLabel}>Your own tasks</Text>
                          {userTasks.map((ai) => (
                            <TaskRow key={ai.id} ai={ai} onPress={() => cycleActionItem(item.id, ai.id)} onDelete={() => deleteTask(item.id, ai.id)} />
                          ))}
                        </>
                      )}
                    </View>
                  ) : (
                    <Text style={styles.taskNone}>No sub-tasks generated for this one.</Text>
                  )}
                  {/* P10: the undo for deleted catalog tasks. The tombstones are per-user,
                      so restoring only clears them — the tasks themselves come back through
                      the shared-list merge (restoreTasks forces a free sync so it happens
                      while the student is looking). */}
                  {removedCount > 0 && (
                    <Pressable onPress={() => restoreTasks(item.id)}>
                      <Text style={styles.restoreLine}>
                        {removedCount} removed task{removedCount > 1 ? 's' : ''} — restore
                      </Text>
                    </Pressable>
                  )}
                  {/* P10: the student's own task. Enter or the + submits. */}
                  <View style={styles.addTaskRow}>
                    <TextInput
                      style={styles.addTaskInput}
                      value={taskDrafts[item.id] ?? ''}
                      onChangeText={(t) => setTaskDrafts((d) => ({ ...d, [item.id]: t }))}
                      onSubmitEditing={() => submitTask(item.id)}
                      placeholder="Add your own task…"
                      placeholderTextColor="#94A3B8"
                    />
                    <Pressable onPress={() => submitTask(item.id)} hitSlop={8}>
                      <Text style={styles.addTaskBtn}>＋</Text>
                    </Pressable>
                  </View>
                </View>
                );
              })}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </Screen>
  );
}

const styles = StyleSheet.create({
  banner: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.lg, paddingVertical: space.xl, flexWrap: 'wrap' },
  bannerLeft: { flexDirection: 'row', alignItems: 'center', gap: space.md, flex: 1, flexWrap: 'wrap' },
  greeting: { color: colors.navy },
  statPills: { flexDirection: 'row', gap: 10, flexWrap: 'wrap', flexShrink: 1, minWidth: 0 },
  statPill: { backgroundColor: colors.navy, borderRadius: radius.lg, paddingHorizontal: 14, paddingVertical: 8, alignItems: 'center', justifyContent: 'center', minWidth: 96 },
  statNum: { fontFamily: fonts.display, fontSize: 18, lineHeight: 18, color: colors.white },
  statLabel: { fontFamily: fonts.bodyBold, fontSize: 8.5, lineHeight: 12, color: colors.orange, letterSpacing: 0.4, marginTop: 3, textTransform: 'uppercase', textAlign: 'center', maxWidth: 84 },

  emptyProfile: {
    borderRadius: radius.xl,
    padding: space.xl,
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.lg,
    flexWrap: 'wrap',
  },
  onDark: { color: colors.white },
  clickable: { cursor: 'pointer' },
  loadingWrap: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingVertical: 64 },
  onDarkSoft: { color: 'rgba(255,255,255,0.9)', maxWidth: 448 },
  flex1: { flex: 1, minWidth: 200 },

  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.sm, flexWrap: 'wrap' },
  // Card h2s inherit the body's text-slate-900, NOT the navy from styles.css — the
  // body tag's Tailwind class wins. Measured via computed-style diff.
  cardTitle: { color: colors.slate900 },
  teaserText: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 21, color: colors.inkSoft },

  trackedPill: { backgroundColor: colors.navy, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  trackedText: { fontFamily: fonts.bodyBold, fontSize: 12, lineHeight: 16, color: colors.white },
  legend: { flexDirection: 'row', gap: space.lg, flexWrap: 'wrap' },
  selfStart: { alignSelf: 'flex-start' },
  freshFindsText: { fontSize: 12, lineHeight: 16, color: colors.navy },
  freshFindsBtn: { paddingVertical: 8 },
  emptyState: { color: '#9AA9B8', fontStyle: 'italic', fontSize: 13 },

  // flexShrink + minWidth:0 are load-bearing: RN-web defaults flex items to flex-shrink:0,
  // so as the right-hand child of `rowBetween` this group expanded to its full content
  // width and its flexWrap never engaged — the fourth pill ("Not Needed") ran off the right
  // edge on a phone. Letting it shrink to the line width makes the pills wrap instead.
  pillRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', flexShrink: 1, minWidth: 0, justifyContent: 'flex-end' },
  todoRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.md, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: colors.slate100 },
  todoName: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.navy },
  todoMeta: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.inkSoft },
  seeAllWrap: { paddingTop: 8, alignItems: 'flex-start' },

  modalScrim: { flex: 1, backgroundColor: 'rgba(15,23,42,0.55)', alignItems: 'center', paddingTop: 40, paddingHorizontal: 16 },
  modalPanel: { backgroundColor: colors.white, borderRadius: radius.xl, width: '100%', maxWidth: 640, maxHeight: '88%' },
  modalScroll: { padding: 32, gap: 12 },
  modalHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, marginBottom: 4 },
  modalSub: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: colors.slate400, marginTop: 2 },
  modalClose: { fontFamily: fonts.bodyBold, fontSize: 18, color: colors.slate500 },
  taskCard: { backgroundColor: colors.slate50, borderWidth: 2, borderColor: colors.slate200, borderRadius: radius.lg, padding: 16, gap: 4 },
  taskCardHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  taskCardName: { fontFamily: fonts.bodyBold, fontSize: 14, lineHeight: 20, color: colors.slate900 },
  taskCardMeta: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: colors.slate500 },
  taskCardDate: { fontFamily: fonts.bodyBold, fontSize: 12, lineHeight: 16, color: colors.indigo600, marginBottom: 4 },
  taskRows: { borderTopWidth: 1, borderTopColor: colors.slate200, paddingTop: 8, gap: 6 },
  // Deliberately quiet: this is a provenance label, not a section header competing with
  // the program name. It has to be readable, not loud.
  taskGroupLabel: { fontFamily: fonts.bodyMed, fontSize: 10, letterSpacing: 0.4,
    textTransform: 'uppercase', color: colors.slate400, marginTop: 2 },
  taskRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  // flexShrink+minWidth keep the text column from pushing the status pill off a phone —
  // the same RN-web flex-shrink:0 default the navbar fix documents.
  taskLeft: { flex: 1, flexShrink: 1, minWidth: 0, gap: 3, alignItems: 'flex-start' },
  taskText: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: '#334155' },
  // Fixed-width right column so the status pills align down the card; flexShrink 0 keeps
  // the pill from being crushed by a long task text (taskLeft is the shrinking side).
  taskRight: { flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 0, minWidth: 120, justifyContent: 'flex-end' },
  taskDelete: { fontFamily: fonts.bodyBold, fontSize: 13, lineHeight: 16, color: colors.slate400, paddingHorizontal: 2 },
  taskDeleteHover: { color: '#D64545' },
  restoreLine: { fontFamily: fonts.bodyBold, fontSize: 11, color: colors.indigo600, textDecorationLine: 'underline', marginTop: 6 },
  addTaskRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 },
  addTaskInput: { flex: 1, borderWidth: 1, borderColor: colors.slate200, borderRadius: 8, paddingHorizontal: 8, paddingVertical: 5, fontFamily: fonts.bodyMed, fontSize: 12, color: '#334155', backgroundColor: colors.white },
  addTaskBtn: { fontFamily: fonts.bodyBold, fontSize: 16, color: colors.indigo600 },
  taskDone: { textDecorationLine: 'line-through', color: colors.slate400 },
  taskCardNameLink: { textDecorationLine: 'underline' },
  taskStepLink: { fontFamily: fonts.bodyBold, color: colors.indigo600 },
  taskNone: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate400, fontStyle: 'italic' },
});
