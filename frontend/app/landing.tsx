import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React, { useEffect, useRef, useState } from 'react';
import { Linking, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { backendUrl } from '@/api/httpClient';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Logo, PopButton, PopCard, SoftCard, usePopInteraction } from '@/ui/components';
import { PersonIcon } from '@/ui/icons';
import { colors, fonts, LANDING_MAX_WIDTH, navShadow, popShadow, radius, space } from '@/ui/theme';

// Self-contained bundle (its own React runtime + fonts), same shape as the retired SPA's
// walkthrough.html — served from the repo root by app/main.py's static route. Too heavy to
// eagerly embed, so it only mounts once someone actually asks to see it (web: inline iframe;
// native: hands off to the system browser, since there's no in-app webview dependency here).
const WALKTHROUGH_URL = backendUrl('/walkthrough.html');

// Mounts the walkthrough iframe and best-effort auto-starts its own internal player (it has
// no autoplay query param or postMessage API of its own, so this reaches in and clicks its
// "Play/pause" control once the bundle has unpacked). Same-origin in production (the API
// service serves both the app and this file), so this actually works there; on a local dev
// setup where Metro (8081) and the API (8000) are different origins it can't reach across
// the iframe boundary, and silently no-ops — the poster's own play chip is the fallback.
function WalkthroughFrame({ frameKey }: { frameKey: number }) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    const tryPlay = () => {
      if (cancelled) return;
      attempts += 1;
      try {
        const doc = ref.current?.contentDocument;
        const btn = doc?.querySelector<HTMLElement>('button[aria-label*="play" i]');
        if (btn) {
          btn.click();
          return;
        }
      } catch {
        return; // cross-origin (dev) — nothing more we can do from here
      }
      if (attempts < 30) setTimeout(tryPlay, 300);
    };
    const t = setTimeout(tryPlay, 300);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [frameKey]);
  return React.createElement('iframe', {
    ref,
    src: WALKTHROUGH_URL,
    title: 'Wingman product walkthrough',
    style: { width: '100%', height: '100%', border: 'none', display: 'block' },
    allow: 'autoplay',
  });
}

// The signed-out marketing page — ported section-for-section from index.html #page-landing:
// floating pill header, hero (badge → eyebrow → one-line title → CTAs), the two bordered
// audience pop-cards, the walkthrough film poster, three soft feature cards, the dark
// gradient CTA banner, the founder story card, and the footer.
export default function Landing() {
  const router = useRouter();
  // 0 = poster showing. >0 = iframe mounted, keyed by this value so every play click forces
  // a fresh mount (fresh <iframe>, video restarts from 0:00) even if it was already playing.
  const [filmKey, setFilmKey] = useState(0);
  const ctaSecondaryPop = usePopInteraction(3, colors.slate900, 1);
  const scrollRef = useRef<ScrollView>(null);
  const filmSectionY = useRef(0);

  function playWalkthrough() {
    if (Platform.OS === 'web') {
      setFilmKey((k) => k + 1);
    } else {
      Linking.openURL(WALKTHROUGH_URL);
    }
  }

  function seeHowItWorks() {
    scrollRef.current?.scrollTo({ y: Math.max(filmSectionY.current - 24, 0), animated: true });
    playWalkthrough();
  }

  return (
    <SafeAreaView style={styles.safe}>
      <ScrollView ref={scrollRef} style={styles.scroll} showsVerticalScrollIndicator={false}>
        {/* Header pill */}
        <View style={styles.headerWrap}>
          <View style={[styles.headerBar, navShadow()]}>
            <View style={styles.brand}>
              <Logo size={30} />
              <Text style={styles.brandWord}>Wingman</Text>
              <View style={styles.brandBeta}>
                <Text style={styles.brandBetaText}>BETA</Text>
              </View>
            </View>
            <Pressable style={styles.signIn} onPress={() => router.push('/login')}>
              <PersonIcon size={16} color={colors.white} />
              <Text style={styles.signInText}>Sign In</Text>
            </Pressable>
          </View>
        </View>

        {/* Hero */}
        <View style={[styles.section, styles.hero]}>
          <View style={styles.heroBadge}>
            <Text style={styles.heroBadgeText}>🚧 BETA - EVOLVING FAST</Text>
          </View>
          <Text style={styles.eyebrow}>FOR HIGH SCHOOL FAMILIES</Text>
          <Text style={styles.h1}>A wingman for the high school years.</Text>
          <Text style={styles.heroSub}>Find opportunities. Never miss a deadline.</Text>
          <Text style={styles.heroBody}>
            Wingman helps high schoolers discover opportunities that fit who they are, while keeping every deadline in
            view. For parents, it's peace of mind. For students, it's someone who's got their back.
          </Text>
          <View style={styles.ctaRow}>
            <PopButton label="Get started free" onPress={() => router.push('/login')} style={styles.ctaMain} textStyle={styles.ctaMainText} />
            <Pressable {...ctaSecondaryPop.handlers} style={[styles.ctaSecondary, ctaSecondaryPop.shadowStyle]} onPress={seeHowItWorks}>
              <Text style={styles.ctaSecondaryText}>See how it works</Text>
            </Pressable>
          </View>
          <Text style={styles.trialNote}>3-day free trial. No card required.</Text>
        </View>

        {/* Audience cards */}
        <View style={[styles.section, styles.cardsRow]}>
          <PopCard style={[styles.audCard]} offset={4}>
            <View style={[styles.audPill, { backgroundColor: colors.navy }]}>
              <Text style={styles.audPillText}>FOR STUDENTS</Text>
            </View>
            <Text style={styles.audTitle}>Four years. Three summers. Make them count.</Text>
            <View style={styles.bullets}>
              <Bullet color={colors.teal} text="Discovery matched to who you are — not another database to search" />
              <Bullet color={colors.teal} text="Opportunities you'd never find on your own: local, niche, overlooked" />
              <Bullet color={colors.teal} text="Every deadline in view, so nothing sneaks up on you" />
            </View>
            <View style={styles.audFoot}>
              <Text style={styles.audFootText}>Wingman is yours — not something your parents use to check up on you.</Text>
            </View>
          </PopCard>
          <PopCard style={[styles.audCard]} offset={4}>
            <View style={[styles.audPill, { backgroundColor: colors.orange }]}>
              <Text style={styles.audPillText}>FOR PARENTS</Text>
            </View>
            <Text style={styles.audTitle}>A wingman for your kids, peace of mind for you.</Text>
            <View style={styles.bullets}>
              <Bullet color={colors.orange} text="Help without having to hunt" />
              <Bullet color={colors.orange} text="Less frantic searching, fewer spreadsheets and bookmarks" />
              <Bullet color={colors.orange} text="Peace of mind from not having to do it all yourself" />
            </View>
            <View style={styles.audFoot}>
              <Text style={styles.audFootText}>Peace of mind for $9.99/month — less than a Netflix subscription.</Text>
            </View>
          </PopCard>
        </View>

        {/* See how it works — film poster, mounts the walkthrough iframe on play */}
        <View style={styles.section} onLayout={(e) => { filmSectionY.current = e.nativeEvent.layout.y; }}>
          <Text style={[styles.sectionTitle, styles.sectionTitleTight]}>See how it works</Text>
          <View style={[styles.filmFrame, popShadow(4)]}>
            <View style={styles.filmStage}>
              {filmKey > 0 && Platform.OS === 'web' ? (
                <WalkthroughFrame key={filmKey} frameKey={filmKey} />
              ) : (
                <Pressable
                  style={styles.filmStagePressable}
                  onPress={playWalkthrough}
                  accessibilityRole="button"
                  accessibilityLabel="Play the Wingman walkthrough"
                >
                  <View style={styles.filmTitleRow}>
                    <Logo size={30} />
                    <Text style={styles.filmTitle}>Wingman, in 47 seconds</Text>
                  </View>
                  <View style={styles.playChip}>
                    <Ionicons name="play" size={22} color={colors.white} style={{ marginLeft: 3 }} />
                  </View>
                  <Text style={styles.filmNote}>No sound. Nothing to sign up for.</Text>
                </Pressable>
              )}
            </View>
          </View>
        </View>

        {/* Feature cards */}
        <View style={[styles.section, styles.featRow]}>
          <SoftCard style={styles.featCard}>
            <Text style={styles.featTitle}>Find What Fits</Text>
            <Text style={styles.featBody}>
              We build a picture of what you care about and surface opportunities relevant to you, not another giant
              database to search yourself.
            </Text>
          </SoftCard>
          <SoftCard style={styles.featCard}>
            <Text style={styles.featTitle}>Go Beyond the Usual</Text>
            <Text style={styles.featBody}>
              We dig up hyperlocal, overlooked opportunities, not just the ones already famous enough to top a Google
              search.
            </Text>
          </SoftCard>
          <SoftCard style={styles.featCard}>
            <Text style={styles.featTitle}>Don't Miss the Deadline</Text>
            <Text style={styles.featBody}>
              High school is four years and three summers. Wingman keeps deadlines in view so you know what's coming and
              when to act.
            </Text>
          </SoftCard>
        </View>

        {/* Dark gradient CTA */}
        <View style={styles.section}>
          <LinearGradient colors={[colors.bannerFrom, colors.bannerTo]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={styles.darkCta}>
            <Text style={styles.darkCtaTitle}>Your story is ready to work for you.</Text>
            <Text style={styles.darkCtaSub}>Tell us what you love. We'll find what fits.</Text>
            <PopButton label="Get started free" variant="primaryDeep" onPress={() => router.push('/login')} style={styles.darkCtaBtn} textStyle={styles.ctaMainText} />
          </LinearGradient>
        </View>

        {/* Founder story */}
        <View style={styles.section}>
          <SoftCard style={styles.founderCard}>
            <Text style={styles.founderTitle}>Why We Built Wingman</Text>
            <Text style={styles.founderBody}>
              Wingman started with a spreadsheet, and a nagging fear of missing something. When my son was in high
              school, I started looking for summer programs that would help him explore his interests. What I quickly
              discovered was that finding the right opportunities was surprisingly difficult.
            </Text>
            <Text style={[styles.founderBody, styles.founderItalic]}>What if I missed something?</Text>
            <Text style={styles.founderBody}>
              Wingman started as something I built for my own sons. I'm opening it up to other families because I
              believe this process shouldn't require endless Google searches, spreadsheets, bookmarks, and calendar
              reminders.
            </Text>
            <Pressable onPress={() => Linking.openURL(backendUrl('/about.html'))}>
              <Text style={styles.founderLink}>Read our full story →</Text>
            </Pressable>
          </SoftCard>
        </View>

        {/* Footer */}
        <View style={[styles.section, styles.footer]}>
          <View style={styles.footerLeft}>
            <Logo size={20} />
            <Text style={styles.footerBrand}>Wingman</Text>
          </View>
          <View style={styles.footerLinks}>
            <Text style={styles.footerLink}>Terms</Text>
            <Text style={styles.footerLink}>Privacy</Text>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function Bullet({ color, text }: { color: string; text: string }) {
  return (
    <View style={styles.bulletRow}>
      <Text style={[styles.bulletDot, { color }]}>•</Text>
      <Text style={styles.bulletText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  scroll: { flex: 1 },
  section: { width: '100%', maxWidth: LANDING_MAX_WIDTH, alignSelf: 'center', paddingHorizontal: 24, paddingBottom: 64 },

  headerWrap: {
    width: '100%',
    maxWidth: LANDING_MAX_WIDTH,
    alignSelf: 'center',
    paddingHorizontal: 24,
    paddingTop: 16,
    zIndex: 50,
    ...(Platform.OS === 'web' ? ({ position: 'sticky', top: 16 } as object) : null),
  },
  headerBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.navy,
    borderRadius: radius.pill,
    paddingLeft: 16,
    paddingRight: 16,
    paddingVertical: 8,
    gap: 16,
  },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  brandWord: { fontFamily: fonts.display, fontSize: 16, color: colors.white },
  brandBeta: { backgroundColor: colors.orange, borderRadius: radius.pill, paddingHorizontal: 9, paddingVertical: 3 },
  brandBetaText: { fontFamily: fonts.bodyXBold, fontSize: 9, color: colors.white, letterSpacing: 0.5 },
  signIn: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingRight: 8 },
  signInText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white, opacity: 0.9 },

  hero: { alignItems: 'center', paddingTop: 80, paddingBottom: 64 },
  heroBadge: { backgroundColor: colors.yellow300, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 4 },
  heroBadgeText: { fontFamily: fonts.bodyXBold, fontSize: 11, color: colors.slate900, letterSpacing: 0.3 },
  eyebrow: { fontFamily: fonts.display, fontSize: 13, color: colors.orange, letterSpacing: 1, marginTop: 24, marginBottom: 10, textTransform: 'uppercase' },
  h1: { fontFamily: fonts.display, fontSize: 48, lineHeight: 58, color: colors.navy, textAlign: 'center', marginBottom: 12 },
  heroSub: { fontFamily: fonts.display, fontSize: 20, lineHeight: 26, color: colors.ink, textAlign: 'center', marginBottom: 16 },
  heroBody: { fontFamily: fonts.bodyMed, fontSize: 18, lineHeight: 29, color: colors.slate500, textAlign: 'center', maxWidth: 576, marginBottom: 32 },
  ctaRow: { flexDirection: 'row', gap: 16, flexWrap: 'wrap', justifyContent: 'center' },
  ctaMain: { paddingHorizontal: 32, paddingVertical: 16 },
  ctaMainText: { fontFamily: fonts.bodyXBold, fontSize: 15, lineHeight: 22 },
  ctaSecondary: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, paddingHorizontal: 32, paddingVertical: 16 },
  ctaSecondaryText: { fontFamily: fonts.bodyXBold, fontSize: 15, color: colors.slate900 },
  trialNote: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500, marginTop: 16 },

  cardsRow: { flexDirection: 'row', gap: 24, flexWrap: 'wrap' },
  audCard: { flex: 1, minWidth: 300, borderRadius: radius.lg, padding: 32, gap: 16 },
  audPill: { borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 4, alignSelf: 'flex-start' },
  audPillText: { fontFamily: fonts.bodyXBold, fontSize: 11, color: colors.white, letterSpacing: 0.5 },
  audTitle: { fontFamily: fonts.display, fontSize: 24, lineHeight: 32, color: colors.navy },
  bullets: { gap: 12 },
  bulletRow: { flexDirection: 'row', gap: 8 },
  bulletDot: { fontFamily: fonts.bodyXBold, fontSize: 14, lineHeight: 22 },
  bulletText: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.ink, flex: 1 },
  audFoot: { borderTopWidth: 1, borderTopColor: colors.slate200, paddingTop: 12, marginTop: 'auto' },
  audFootText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted },

  sectionTitle: { fontFamily: fonts.display, fontSize: 32, color: colors.navy, textAlign: 'center', marginBottom: 8 },
  sectionTitleTight: { marginBottom: 32 },
  filmFrame: { borderWidth: 3, borderColor: colors.navy, borderRadius: radius.lg, overflow: 'hidden' },
  filmStage: { width: '100%', aspectRatio: 16 / 9, backgroundColor: '#0A0A0A' },
  filmStagePressable: { width: '100%', height: '100%', alignItems: 'center', justifyContent: 'center', gap: 16 },
  filmTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  filmTitle: { fontFamily: fonts.display, fontSize: 20, color: colors.white },
  playChip: { width: 64, height: 64, borderRadius: 32, backgroundColor: colors.orangeDeep, alignItems: 'center', justifyContent: 'center' },
  filmNote: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted },

  featRow: { flexDirection: 'row', gap: 24, flexWrap: 'wrap' },
  featCard: { flex: 1, minWidth: 240, padding: 32, gap: 8 },
  featTitle: { fontFamily: fonts.display, fontSize: 18, color: colors.navy },
  featBody: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.ink },

  darkCta: { borderRadius: radius.lg, padding: 48, alignItems: 'center' },
  darkCtaTitle: { fontFamily: fonts.display, fontSize: 24, color: colors.white, marginBottom: 8, textAlign: 'center' },
  darkCtaSub: { fontFamily: fonts.bodyMed, fontSize: 15, color: colors.grayLighter, marginBottom: 24, textAlign: 'center' },
  darkCtaBtn: { paddingHorizontal: 32, paddingVertical: 14 },

  founderCard: { padding: 40, gap: 12 },
  founderTitle: { fontFamily: fonts.display, fontSize: 20, color: colors.navy },
  founderBody: { fontFamily: fonts.bodyMed, fontSize: 15, lineHeight: 24, color: colors.ink },
  founderItalic: { fontStyle: 'italic', fontFamily: fonts.bodyBold },
  founderLink: { fontFamily: fonts.bodyXBold, fontSize: 14, color: colors.purple },

  footer: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderTopWidth: 1, borderTopColor: colors.slate200, paddingTop: 32, paddingBottom: 32, flexWrap: 'wrap', gap: 12 },
  footerLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  footerBrand: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500 },
  footerLinks: { flexDirection: 'row', gap: 20 },
  footerLink: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500 },
});
