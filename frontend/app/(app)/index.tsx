import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import {
  loadTrackerData,
  loadTrackerSaved,
  type SavedState,
  type TrackerData,
} from '@/api/trackerStore';
import { useAuth } from '@/auth/AuthContext';
import {
  allTodoUnitCounts,
  computeProgressStatus,
  computeStats,
  getUpcomingDeadlineItems,
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
// "Your Next Moves" (task pills + task progress + upcoming list + "and beyond →").
export default function Home() {
  const router = useRouter();
  const { user } = useAuth();
  const [data, setData] = useState<TrackerData | null>(null);
  const [saved, setSaved] = useState<SavedState>({});
  const [profile, setProfile] = useState<string>('');

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
      });
      return () => {
        alive = false;
      };
    }, []),
  );

  const stats = useMemo(() => (data ? computeStats(data, saved) : { total: 0, not_started: 0, in_progress: 0, completed: 0 }), [data, saved]);
  const upcoming = useMemo(() => (data ? getUpcomingDeadlineItems(data, saved) : []), [data, saved]);
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

  return (
    <Screen>
      {/* Welcome banner */}
      <SoftCard style={styles.banner}>
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
        <SoftCard style={{ gap: space.lg }}>
          <View style={styles.rowBetween}>
            <Txt variant="h2" style={styles.cardTitle}>Your Story So Far</Txt>
            <PopButton label="View & deepen it →" small square onPress={() => router.push('/(app)/profile')} />
          </View>
          <Txt style={styles.teaserText} numberOfLines={3}>{profile}</Txt>
        </SoftCard>
      ) : (
        <View style={[styles.emptyProfile]}>
          <View style={styles.flex1}>
            <Txt variant="h3" style={styles.onDark}>Your profile is empty</Txt>
            <Txt variant="body" style={styles.onDarkSoft}>
              Every match in the Finder gets better once we know you. Takes 2 minutes — go build it now.
            </Txt>
          </View>
          <PopButton label="Build my profile" variant="secondary" onPress={() => router.push('/(app)/profile')} />
        </View>
      )}

      {/* What You're Chasing */}
      <SoftCard style={{ gap: space.lg }}>
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
              onPress={() => router.push('/(app)/finder')}
              style={styles.selfStart}
            />
          </>
        ) : (
          <>
            <ProgressTrack segments={[]} />
            <Txt variant="small" style={styles.emptyState}>Nothing here yet.</Txt>
            <PopButton label="Find your first opportunity to track" onPress={() => router.push('/(app)/finder')} style={styles.selfStart} />
          </>
        )}
      </SoftCard>

      {/* Your Next Moves */}
      <SoftCard style={{ gap: space.lg }}>
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
          <Txt variant="small" style={styles.emptyState}>Nothing due this month or next — you're all caught up.</Txt>
        ) : (
          <View>
            {upcoming.map(({ item, nextDate, nextLabel }) => {
              const taskCount = (item.actionItems ?? []).length;
              return (
                <View key={item.id} style={styles.todoRow}>
                  <View style={styles.flex1}>
                    <Txt style={styles.todoName} numberOfLines={1}>{item.name}</Txt>
                    <Txt style={styles.todoMeta}>
                      {shortDate(nextDate)} · {nextLabel}
                      {taskCount ? ` · ${taskCount} task${taskCount > 1 ? 's' : ''}` : ''}
                    </Txt>
                  </View>
                  <StatusPill status={computeProgressStatus(item)} />
                </View>
              );
            })}
          </View>
        )}
        <Txt variant="small" style={styles.andBeyond}>and beyond →</Txt>
      </SoftCard>
    </Screen>
  );
}

const styles = StyleSheet.create({
  banner: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.lg, paddingVertical: space.xl },
  bannerLeft: { flexDirection: 'row', alignItems: 'center', gap: space.md, flex: 1, flexWrap: 'wrap' },
  greeting: { color: colors.navy },
  dueBadge: { backgroundColor: colors.navy, borderRadius: radius.lg, paddingHorizontal: 16, paddingVertical: 8, alignItems: 'center' },
  dueNum: { fontFamily: fonts.display, fontSize: 18, lineHeight: 20, color: colors.white },
  dueLabel: { fontFamily: fonts.bodyBold, fontSize: 9, color: colors.orange, letterSpacing: 1, marginTop: 2 },

  emptyProfile: {
    borderRadius: radius.xl,
    padding: space.xl,
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.lg,
    flexWrap: 'wrap',
    backgroundColor: colors.teal,
  },
  onDark: { color: colors.white },
  onDarkSoft: { color: 'rgba(255,255,255,0.9)', maxWidth: 448 },
  flex1: { flex: 1, minWidth: 200 },

  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.sm, flexWrap: 'wrap' },
  cardTitle: { color: colors.navy },
  teaserText: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 21, color: colors.inkSoft },

  trackedPill: { backgroundColor: colors.navy, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  trackedText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.white },
  legend: { flexDirection: 'row', gap: space.lg, flexWrap: 'wrap' },
  selfStart: { alignSelf: 'flex-start' },
  freshFindsText: { fontSize: 12, color: colors.navy },
  emptyState: { color: '#9AA9B8', fontStyle: 'italic', fontSize: 13 },

  pillRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
  todoRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.md, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: colors.slate100 },
  todoName: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.navy },
  todoMeta: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.inkSoft },
  andBeyond: { textAlign: 'center', color: colors.muted, paddingTop: 8, fontFamily: fonts.bodyBold, fontSize: 13 },
});
