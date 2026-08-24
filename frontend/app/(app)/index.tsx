import { LinearGradient } from 'expo-linear-gradient';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import {
  loadTrackerData,
  loadTrackerSaved,
  peekTrackerData,
  peekTrackerSaved,
  saveTrackerData,
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
} from '@/ui/components';
import { colors, fonts, radius, space } from '@/ui/theme';

interface StoredProfile {
  synthesized?: string;
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
  const NEXT_STATE: Record<string, OppStatus> = { not_started: 'in_progress', in_progress: 'completed', completed: 'not_started' };
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
  const dueSoon = taskCounts.not_started + taskCounts.in_progress;

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
        <View style={styles.dueBadge}>
          <Txt style={styles.dueNum}>{dueSoon}</Txt>
          <Txt style={styles.dueLabel}>DUE SOON</Txt>
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
            {(['not_started', 'in_progress', 'completed'] as OppStatus[]).map((k) => (
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
              {upcoming.map(({ item, nextDate, nextLabel }) => (
                <View key={item.id} style={styles.taskCard}>
                  <View style={styles.taskCardHead}>
                    <View style={styles.flex1}>
                      <Text style={styles.taskCardName} numberOfLines={1}>{item.name}</Text>
                      {!!item.meta && <Text style={styles.taskCardMeta} numberOfLines={1}>{item.meta}</Text>}
                    </View>
                    <StatusPill status={computeProgressStatus(item)} />
                  </View>
                  <Text style={styles.taskCardDate}>
                    {nextDate ? `${shortDate(nextDate)} · ${nextLabel}` : nextLabel}{item.wasEstimated ? ' (est.)' : ''}
                  </Text>
                  {(item.actionItems ?? []).length ? (
                    <View style={styles.taskRows}>
                      {(item.actionItems ?? []).map((ai) => (
                        <View key={ai.id} style={styles.taskRow}>
                          <Text style={[styles.taskText, ai.state === 'completed' && styles.taskDone]}>{ai.text}</Text>
                          <StatusPill
                            status={(ai.state as OppStatus) in ACTION_ITEM_STATUS_LABEL ? (ai.state as OppStatus) : 'not_started'}
                            kind="task"
                            onPress={() => cycleActionItem(item.id, ai.id)}
                          />
                        </View>
                      ))}
                    </View>
                  ) : (
                    <Text style={styles.taskNone}>No sub-tasks generated for this one.</Text>
                  )}
                </View>
              ))}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </Screen>
  );
}

const styles = StyleSheet.create({
  banner: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.lg, paddingVertical: space.xl },
  bannerLeft: { flexDirection: 'row', alignItems: 'center', gap: space.md, flex: 1, flexWrap: 'wrap' },
  greeting: { color: colors.navy },
  dueBadge: { backgroundColor: colors.navy, borderRadius: radius.lg, paddingHorizontal: 16, paddingVertical: 8, alignItems: 'center' },
  dueNum: { fontFamily: fonts.display, fontSize: 18, lineHeight: 18, color: colors.white },
  dueLabel: { fontFamily: fonts.bodyBold, fontSize: 9, lineHeight: 13, color: colors.orange, letterSpacing: 0.45, marginTop: 2, textTransform: 'uppercase' },

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

  pillRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
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
  taskRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  taskText: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: '#334155', flex: 1 },
  taskDone: { textDecorationLine: 'line-through', color: colors.slate400 },
  taskNone: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate400, fontStyle: 'italic' },
});
