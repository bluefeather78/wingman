import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Badge, PopButton, SoftCard, Txt } from '@/ui/components';
import { colors, fonts, radius, space } from '@/ui/theme';

// Marketing landing for signed-out visitors (the app's public front door). The vendored
// 37-second walkthrough film is web-only and heavy; here the "See how it works" beats are
// shown as caption cards so the story crosses to native. "Get started" / "Sign in" -> login.
const STUDENT_POINTS = [
  'Discovery matched to who you are — not another database to search',
  "Opportunities you'd never find on your own: local, niche, overlooked",
  'Every deadline in view, so nothing sneaks up on you',
];
const PARENT_POINTS = [
  'Help without having to hunt',
  'Less frantic searching, fewer spreadsheets and bookmarks',
  'Peace of mind from not having to do it all yourself',
];
const HOW_POINTS = [
  'The work starts early — intelligent forecasting of deadlines',
  "Including the hyper-local ones that don't show up on Google",
  'Finding you the right matches from amongst thousands',
  'Thoughtful questions that probe deeper and build your profile',
];

export default function Landing() {
  const router = useRouter();
  return (
    <SafeAreaView style={styles.safe}>
      {/* Top bar */}
      <View style={styles.nav}>
        <View style={styles.brand}>
          <Ionicons name="stats-chart" size={18} color={colors.orange} />
          <Txt style={styles.word}>Wingman</Txt>
          <View style={styles.beta}><Txt style={styles.betaText}>BETA</Txt></View>
        </View>
        <Pressable onPress={() => router.push('/login')} style={styles.signIn}>
          <Ionicons name="person-outline" size={14} color={colors.white} />
          <Txt style={styles.signInText}>Sign in</Txt>
        </Pressable>
      </View>

      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
        {/* Hero */}
        <View style={styles.hero}>
          <Badge label="◈ BETA — EVOLVING FAST" bg={colors.yellow} fg={colors.navyDeep} />
          <Txt variant="label" style={{ color: colors.orangeDeep }}>FOR HIGH SCHOOL FAMILIES</Txt>
          <Txt style={styles.heroTitle}>A wingman for the high school years.</Txt>
          <Txt variant="h3" style={styles.heroSub}>Find opportunities. Never miss a deadline.</Txt>
          <Txt variant="body" style={styles.heroBody}>
            Wingman helps high schoolers discover opportunities that fit who they are, while keeping every
            deadline in view. For parents, it's peace of mind. For students, it's someone who's got their back.
          </Txt>
          <View style={styles.ctaRow}>
            <PopButton label="Get started free" onPress={() => router.push('/login')} />
            <PopButton label="See how it works" variant="secondary" onPress={() => router.push('/login')} />
          </View>
          <Txt variant="small">3-day free trial. No card required.</Txt>
        </View>

        {/* Audience cards */}
        <View style={styles.cardsRow}>
          <SoftCard style={styles.audCard}>
            <Badge label="FOR STUDENTS" bg={colors.navy} fg={colors.white} />
            <Txt variant="h2">Four years. Three summers. Make them count.</Txt>
            {STUDENT_POINTS.map((p, i) => (
              <Bullet key={i} text={p} color={colors.teal} />
            ))}
            <Txt variant="small" style={styles.foot}>Wingman is yours — not something your parents use to check up on you.</Txt>
          </SoftCard>
          <SoftCard style={styles.audCard}>
            <Badge label="FOR PARENTS" bg={colors.orange} fg={colors.white} />
            <Txt variant="h2">A wingman for your kids, peace of mind for you.</Txt>
            {PARENT_POINTS.map((p, i) => (
              <Bullet key={i} text={p} color={colors.orange} />
            ))}
            <Txt variant="small" style={styles.foot}>Peace of mind for $9.99/month — less than a Netflix subscription.</Txt>
          </SoftCard>
        </View>

        {/* How it works */}
        <View style={styles.how}>
          <Txt variant="hero" style={styles.center}>See how it works</Txt>
          <Txt variant="body" style={styles.center}>
            Thirty-seven seconds, start to finish: a student's story goes in, opportunities that actually fit
            come back, and every deadline lands on the calendar.
          </Txt>
          <View style={styles.howGrid}>
            {HOW_POINTS.map((p, i) => (
              <SoftCard key={i} color={colors.navy} style={styles.howCard}>
                <Txt variant="h2" style={{ color: colors.orange }}>{String(i + 1).padStart(2, '0')}</Txt>
                <Txt variant="bodyStrong" style={{ color: colors.white }}>{p}</Txt>
              </SoftCard>
            ))}
          </View>
          <PopButton label="Get started free" onPress={() => router.push('/login')} style={styles.center} />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function Bullet({ text, color }: { text: string; color: string }) {
  return (
    <View style={styles.bullet}>
      <View style={[styles.dot, { backgroundColor: color }]} />
      <Txt variant="body" style={styles.flex1}>{text}</Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  nav: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: colors.navy, borderRadius: radius.pill, margin: space.md, paddingVertical: 10, paddingHorizontal: space.lg },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  word: { fontFamily: fonts.display, fontSize: 17, color: colors.white },
  beta: { backgroundColor: colors.yellow, borderRadius: radius.pill, paddingHorizontal: 7, paddingVertical: 1 },
  betaText: { fontFamily: fonts.bodyBold, fontSize: 9, color: colors.navyDeep },
  signIn: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  signInText: { fontFamily: fonts.bodyBold, fontSize: 13, color: colors.white },
  scroll: { padding: space.lg, gap: space.xxl, maxWidth: 980, width: '100%', alignSelf: 'center', paddingBottom: 48 },
  hero: { alignItems: 'center', gap: space.md, marginTop: space.lg },
  heroTitle: { fontFamily: fonts.display, fontSize: 40, lineHeight: 44, color: colors.navy, textAlign: 'center', maxWidth: 640 },
  heroSub: { color: colors.ink, textAlign: 'center' },
  heroBody: { textAlign: 'center', maxWidth: 560 },
  ctaRow: { flexDirection: 'row', gap: space.md, flexWrap: 'wrap', justifyContent: 'center' },
  cardsRow: { flexDirection: 'row', gap: space.lg, flexWrap: 'wrap' },
  audCard: { flexGrow: 1, flexBasis: 320, gap: space.sm },
  bullet: { flexDirection: 'row', alignItems: 'flex-start', gap: space.sm, marginTop: 2 },
  dot: { width: 7, height: 7, borderRadius: 4, marginTop: 7 },
  flex1: { flex: 1 },
  foot: { marginTop: space.sm, fontStyle: 'italic' },
  how: { gap: space.lg, alignItems: 'center' },
  center: { textAlign: 'center', alignSelf: 'center' },
  howGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: space.md, justifyContent: 'center' },
  howCard: { flexGrow: 1, flexBasis: 220, gap: 4 },
});
