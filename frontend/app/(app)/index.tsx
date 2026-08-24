import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { flattenItems, loadTrackerData, type TrackerItem } from '@/api/trackerStore';
import { useAuth } from '@/auth/AuthContext';
import { PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { countProfileWords } from '@/lib/profile';
import { Badge, PopButton, ProgressBar, Screen, SoftCard, Txt } from '@/ui/components';
import { colors, radius, space } from '@/ui/theme';

interface StoredProfile {
  synthesized?: string;
}

// Home Base — the dashboard. Welcome + Due Soon, the story card (or empty nudge), "What
// You're Chasing" (tracker progress + legend), and "Your Next Moves" (task status).
export default function Home() {
  const router = useRouter();
  const { user } = useAuth();
  const [items, setItems] = useState<TrackerItem[]>([]);
  const [profile, setProfile] = useState<string>('');

  useFocusEffect(
    useCallback(() => {
      let alive = true;
      Promise.all([
        loadTrackerData().then(flattenItems).catch(() => [] as TrackerItem[]),
        httpClient.loadData<StoredProfile>('student-profile').catch(() => null),
      ]).then(([tracked, p]) => {
        if (!alive) return;
        setItems(tracked);
        setProfile(p?.synthesized ?? '');
      });
      return () => {
        alive = false;
      };
    }, []),
  );

  const hasProfile = countProfileWords(profile) >= PROFILE_SUFFICIENT_LENGTH;

  const { dueSoon, happeningNow, future } = useMemo(() => {
    const now = Date.now();
    const soon = now + 45 * 24 * 3600 * 1000;
    let due = 0;
    let nowCount = 0;
    let futureCount = 0;
    items.forEach((it) => {
      const dates = it.importantDates ?? [];
      const hasFuture = dates.some((d) => {
        const t = Date.parse(d.dateISO);
        return !Number.isNaN(t) && t >= now;
      });
      const hasDueSoon = dates.some((d) => {
        const t = Date.parse(d.dateISO);
        return !Number.isNaN(t) && t >= now && t <= soon;
      });
      if (hasDueSoon) due += 1;
      // A future-dated item is an upcoming event; only items with no upcoming date but a
      // running status count as "happening now".
      if (hasFuture) futureCount += 1;
      else if (it.status === 'running') nowCount += 1;
    });
    return { dueSoon: due, happeningNow: nowCount, future: futureCount };
  }, [items]);

  return (
    <Screen>
      {/* Welcome banner */}
      <SoftCard style={styles.banner}>
        <View style={styles.bannerLeft}>
          <Ionicons name="stats-chart" size={24} color={colors.orange} />
          <Txt variant="h1">
            Hey <Txt variant="h1" style={{ color: colors.orange }}>{user?.firstName || 'there'}</Txt>, ready?
          </Txt>
        </View>
        <View style={styles.dueBadge}>
          <Txt variant="h2" style={styles.dueNum}>{dueSoon}</Txt>
          <Txt style={styles.dueLabel}>DUE SOON</Txt>
        </View>
      </SoftCard>

      {/* Story card / empty nudge */}
      {hasProfile ? (
        <SoftCard style={{ gap: space.sm }}>
          <View style={styles.rowBetween}>
            <Txt variant="h2">Your Story So Far</Txt>
            <PopButton label="View & deepen it →" small onPress={() => router.push('/(app)/profile')} />
          </View>
          <Txt variant="body" numberOfLines={3}>{profile}</Txt>
        </SoftCard>
      ) : (
        <SoftCard color={colors.navy} style={styles.promo}>
          <View style={styles.flex1}>
            <Txt variant="h2" style={styles.onDark}>Your profile is empty</Txt>
            <Txt variant="body" style={styles.onDarkSoft}>
              Every match in the Finder gets better once we know you. Takes 2 minutes — go build it now.
            </Txt>
          </View>
          <PopButton label="Build my profile" variant="secondary" onPress={() => router.push('/(app)/profile')} />
        </SoftCard>
      )}

      {/* What You're Chasing */}
      <SoftCard style={{ gap: space.md }}>
        <View style={styles.rowBetween}>
          <Txt variant="h2">What You're Chasing</Txt>
          <View style={styles.trackedPill}>
            <Txt style={styles.trackedText}>{items.length} tracked</Txt>
          </View>
        </View>
        <ProgressBar value={items.length ? 1 : 0} color={colors.teal} />
        {items.length === 0 ? (
          <>
            <Txt variant="body" style={styles.italic}>Nothing here yet.</Txt>
            <PopButton label="Find your first opportunity to track" onPress={() => router.push('/(app)/finder')} style={styles.selfStart} />
          </>
        ) : (
          <>
            <View style={styles.legend}>
              <LegendDot color={colors.teal} label={`Happening Now (${happeningNow})`} />
              <LegendDot color={colors.green} label={`Future Event (${future})`} />
            </View>
            <PopButton label="Look for Fresh Finds" variant="secondary" small onPress={() => router.push('/(app)/finder')} style={styles.selfStart} />
          </>
        )}
      </SoftCard>

      {/* Your Next Moves */}
      <SoftCard style={{ gap: space.md }}>
        <View style={styles.rowBetween}>
          <Txt variant="h2">Your Next Moves</Txt>
          <View style={styles.pillRow}>
            <Badge label="0 NOT STARTED" bg="transparent" fg={colors.orange} outline />
            <Badge label="0 IN PROGRESS" bg="transparent" fg={colors.navy} outline />
            <Badge label="0 COMPLETED" bg="transparent" fg={colors.teal} outline />
          </View>
        </View>
        <ProgressBar value={0} />
        <Txt variant="body" style={styles.italic}>Nothing due this month or next — you're all caught up.</Txt>
        <Txt variant="small" style={styles.andBeyond}>and beyond →</Txt>
      </SoftCard>
    </Screen>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <View style={styles.legendItem}>
      <View style={[styles.dot, { backgroundColor: color }]} />
      <Txt variant="small" style={{ color: colors.ink }}>{label}</Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.md },
  bannerLeft: { flexDirection: 'row', alignItems: 'center', gap: space.sm, flex: 1, flexWrap: 'wrap' },
  dueBadge: { backgroundColor: colors.navy, borderRadius: radius.lg, paddingHorizontal: space.md, paddingVertical: 6, alignItems: 'center' },
  dueNum: { color: colors.white },
  dueLabel: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 8, color: colors.orange, letterSpacing: 0.8 },
  promo: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  onDark: { color: colors.white },
  onDarkSoft: { color: '#D6E4F5' },
  flex1: { flex: 1 },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.sm, flexWrap: 'wrap' },
  trackedPill: { backgroundColor: colors.navy, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 5 },
  trackedText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 12, color: colors.white },
  italic: { fontStyle: 'italic' },
  selfStart: { alignSelf: 'flex-start' },
  legend: { flexDirection: 'row', gap: space.lg, flexWrap: 'wrap' },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  dot: { width: 9, height: 9, borderRadius: 5 },
  pillRow: { flexDirection: 'row', gap: 6, flexWrap: 'wrap' },
  andBeyond: { textAlign: 'center', color: colors.muted },
});
