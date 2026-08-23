import { useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { StyleSheet, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { loadTrackerItems, type TrackerItem } from '@/api/trackerStore';
import { useAuth } from '@/auth/AuthContext';
import { ALL_BUCKETS, PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { countProfileWords } from '@/lib/profile';
import { PopButton, PopCard, Screen, Txt } from '@/ui/components';
import { colors, space } from '@/ui/theme';

interface StoredProfile {
  synthesized?: string;
}

// Home / Dashboard — where a student lands. Shows how far along they are (profile + tracker)
// and the two actions that move them forward.
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
  const greeting = user?.firstName ? `Hey ${user.firstName} 👋` : 'Hey there 👋';

  return (
    <Screen>
      <View style={styles.head}>
        <Txt variant="label">YOUR DASHBOARD</Txt>
        <Txt variant="hero">{greeting}</Txt>
        <Txt variant="body">Here's where things stand. Keep the momentum going.</Txt>
      </View>

      <View style={styles.statRow}>
        <PopCard color={colors.lime} style={styles.stat}>
          <Txt variant="hero" style={styles.statNum}>
            {items.length}
          </Txt>
          <Txt variant="bodyStrong">tracked</Txt>
        </PopCard>
        <PopCard color={colors.purple} style={styles.stat}>
          <Txt variant="hero" style={[styles.statNum, styles.onDark]}>
            {bucketsUsed}
          </Txt>
          <Txt variant="bodyStrong" style={styles.onDark}>
            of {ALL_BUCKETS.length} categories
          </Txt>
        </PopCard>
      </View>

      <PopCard style={styles.profileCard} color={profileReady ? colors.greenSoft : colors.white}>
        <Txt variant="label">{profileReady ? 'PROFILE READY' : 'PROFILE'}</Txt>
        <Txt variant="h2">
          {profileWords === null
            ? 'Loading…'
            : profileReady
              ? 'Your profile is looking good'
              : 'Tell us about yourself'}
        </Txt>
        <Txt variant="body">
          {profileReady
            ? 'Better matches come from a richer profile — keep it fresh as things change.'
            : 'A few sentences about your interests and projects unlocks personalized matches.'}
        </Txt>
        <PopButton
          label={profileReady ? 'Update profile' : 'Build your profile'}
          variant={profileReady ? 'secondary' : 'purple'}
          onPress={() => router.push('/(app)/profile')}
        />
      </PopCard>

      <PopCard style={styles.ctaCard} color={colors.orange}>
        <Txt variant="label" style={styles.onDark}>
          FIND SOMETHING NEW
        </Txt>
        <Txt variant="h2" style={styles.onDark}>
          Discover opportunities
        </Txt>
        <Txt variant="body" style={styles.onDarkSoft}>
          Search 1,200+ programs, internships, competitions, and more — ranked for you.
        </Txt>
        <PopButton label="Open the Finder" variant="secondary" onPress={() => router.push('/(app)/finder')} />
      </PopCard>
    </Screen>
  );
}

const styles = StyleSheet.create({
  head: { gap: space.xs, marginBottom: space.xs },
  statRow: { flexDirection: 'row', gap: space.lg },
  stat: { flex: 1, gap: 2, alignItems: 'flex-start' },
  statNum: { fontSize: 40, lineHeight: 44 },
  onDark: { color: colors.white },
  onDarkSoft: { color: '#FFF4EC' },
  profileCard: { gap: space.sm },
  ctaCard: { gap: space.sm },
});
