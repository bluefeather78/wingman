import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { loadTrackerItems, type TrackerItem } from '@/api/trackerStore';
import { useAuth } from '@/auth/AuthContext';
import { ALL_BUCKETS, PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { countProfileWords } from '@/lib/profile';
import { Badge, PopButton, ProgressBar, Screen, SoftCard, Txt } from '@/ui/components';
import { colors, radius, space } from '@/ui/theme';

interface StoredProfile {
  synthesized?: string;
}

// Home Base — the dashboard. Welcome banner + Due Soon, a profile nudge, "What You're
// Chasing" (tracker progress), and "Your Next Moves" (upcoming dated items).
export default function Home() {
  const router = useRouter();
  const { user } = useAuth();
  const [items, setItems] = useState<TrackerItem[]>([]);
  const [profileWords, setProfileWords] = useState<number | null>(null);

  useFocusEffect(
    useCallback(() => {
      let alive = true;
      Promise.all([
        loadTrackerItems().catch(() => [] as TrackerItem[]),
        httpClient.loadData<StoredProfile>('student-profile').catch(() => null),
      ]).then(([tracked, profile]) => {
        if (!alive) return;
        setItems(tracked);
        setProfileWords(countProfileWords(profile?.synthesized));
      });
      return () => {
        alive = false;
      };
    }, []),
  );

  const profileReady = (profileWords ?? 0) >= PROFILE_SUFFICIENT_LENGTH;
  const bucketsUsed = ALL_BUCKETS.filter((b) => items.some((i) => i.bucket === b)).length;

  // "Due Soon" + "Next Moves" from any dated items within the next ~45 days.
  const upcoming = useMemo(() => {
    const now = Date.now();
    const horizon = now + 45 * 24 * 3600 * 1000;
    const rows: { name: string; label: string; dateISO: string; t: number }[] = [];
    items.forEach((it) =>
      (it.dates ?? []).forEach((d) => {
        const t = Date.parse(d.dateISO);
        if (!Number.isNaN(t) && t >= now && t <= horizon) rows.push({ name: it.name, label: d.label, dateISO: d.dateISO, t });
      }),
    );
    return rows.sort((a, b) => a.t - b.t);
  }, [items]);

  return (
    <Screen>
      {/* Welcome banner */}
      <SoftCard style={styles.banner}>
        <View style={styles.bannerLeft}>
          <Ionicons name="stats-chart" size={26} color={colors.orange} />
          <Txt variant="h1">
            Hey <Txt variant="h1" style={{ color: colors.orange }}>{user?.firstName || 'there'}</Txt>, ready?
          </Txt>
        </View>
        <View style={styles.dueBadge}>
          <Txt variant="h2" style={styles.dueNum}>
            {upcoming.length}
          </Txt>
          <Txt style={styles.dueLabel}>DUE SOON</Txt>
        </View>
      </SoftCard>

      {/* Profile nudge */}
      {!profileReady ? (
        <LinearGradient colors={[colors.bannerFrom, colors.bannerTo]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.promo}>
          <View style={styles.flex1}>
            <Txt variant="h2" style={styles.onDark}>
              Your profile is {profileWords ? 'thin' : 'empty'}
            </Txt>
            <Txt variant="body" style={styles.onDarkSoft}>
              Every match in the Finder gets better once we know you. Takes 2 minutes — go build it now.
            </Txt>
          </View>
          <PopButton label="Build my profile" variant="secondary" onPress={() => router.push('/(app)/profile')} />
        </LinearGradient>
      ) : (
        <SoftCard color={colors.greenSoft} style={styles.readyRow}>
          <View style={styles.flex1}>
            <Txt variant="label" style={{ color: colors.green }}>
              PROFILE READY
            </Txt>
            <Txt variant="h3">Your profile is looking good</Txt>
          </View>
          <PopButton label="Update" variant="secondary" small onPress={() => router.push('/(app)/profile')} />
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
        <ProgressBar value={ALL_BUCKETS.length ? bucketsUsed / ALL_BUCKETS.length : 0} />
        {items.length === 0 ? (
          <>
            <Txt variant="body" style={styles.italic}>
              Nothing here yet.
            </Txt>
            <PopButton label="Find your first opportunity to track" onPress={() => router.push('/(app)/finder')} />
          </>
        ) : (
          <Txt variant="small">
            Across {bucketsUsed} of {ALL_BUCKETS.length} categories.
          </Txt>
        )}
      </SoftCard>

      {/* Your Next Moves */}
      <SoftCard style={{ gap: space.md }}>
        <View style={styles.rowBetween}>
          <Txt variant="h2">Your Next Moves</Txt>
          <View style={styles.pillRow}>
            <Badge label={`${upcoming.length} UPCOMING`} bg="transparent" fg={colors.orange} outline />
          </View>
        </View>
        {upcoming.length === 0 ? (
          <Txt variant="body" style={styles.italic}>
            Nothing due this month or next — you're all caught up.
          </Txt>
        ) : (
          <View style={{ gap: space.sm }}>
            {upcoming.slice(0, 5).map((m, i) => (
              <View key={i} style={styles.moveRow}>
                <Ionicons name="ellipse" size={8} color={colors.orange} />
                <Txt variant="bodyStrong" style={styles.flex1} numberOfLines={1}>
                  {m.label} — {m.name}
                </Txt>
                <Txt variant="small">{formatShort(m.dateISO)}</Txt>
              </View>
            ))}
          </View>
        )}
      </SoftCard>
    </Screen>
  );
}

function formatShort(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  if (!y || !m || !d) return iso;
  return `${months[m - 1]} ${d}`;
}

const styles = StyleSheet.create({
  banner: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.md },
  bannerLeft: { flexDirection: 'row', alignItems: 'center', gap: space.sm, flex: 1, flexWrap: 'wrap' },
  dueBadge: { backgroundColor: colors.navy, borderRadius: radius.lg, paddingHorizontal: space.md, paddingVertical: 6, alignItems: 'center' },
  dueNum: { color: colors.white },
  dueLabel: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 8, color: colors.orange, letterSpacing: 0.8 },
  promo: { borderRadius: radius.xl, padding: space.xl, flexDirection: 'row', alignItems: 'center', gap: space.md },
  readyRow: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  onDark: { color: colors.white },
  onDarkSoft: { color: '#D6E4F5' },
  flex1: { flex: 1 },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.sm, flexWrap: 'wrap' },
  trackedPill: { backgroundColor: colors.navy, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 5 },
  trackedText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 12, color: colors.white },
  italic: { fontStyle: 'italic' },
  pillRow: { flexDirection: 'row', gap: 6 },
  moveRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
});
