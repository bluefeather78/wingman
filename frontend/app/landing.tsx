import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React, { useEffect, useRef, useState } from 'react';
import { Linking, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { backendUrl } from '@/api/httpClient';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Logo, PopButton, PopCard, SoftCard, usePopInteraction } from '@/ui/components';
import { openBackendPage } from '@/ui/openPage';
import { ContractIcon, ExpandIcon, LockIcon, PersonIcon, PlayIcon } from '@/ui/icons';
import { colors, fonts, LANDING_MAX_WIDTH, navShadow, popShadow, radius, space } from '@/ui/theme';

// Self-contained bundle (its own React runtime + fonts), same shape as the retired SPA's
// walkthrough.html — served from the repo root by app/main.py's static route. Too heavy to
// eagerly embed, so it only mounts once someone actually asks to see it (web: inline iframe;
// native: hands off to the system browser, since there's no in-app webview dependency here).
const WALKTHROUGH_URL = backendUrl('/walkthrough.html');

// The secondary header links — shown inline on desktop, and behind the hamburger on mobile.
const NAV_LINKS = [
  { label: 'Pricing', path: '/pricing.html' },
  { label: 'How we use AI', path: '/how-we-use-ai.html' },
  { label: 'FAQ', path: '/faq.html' },
  { label: 'About', path: '/about.html' },
  { label: 'Terms', path: '/terms.html' },
  { label: 'Privacy', path: '/privacy.html' },
];

// The film's own player persists its playhead in localStorage under
// 'animstage-v3:t', and this composition is authored to play exactly once
// ({"mode":"times","count":1}) — so a second viewing restores time === duration,
// immediately re-hits the end, and holds the final frame instead of playing.
// Remounting the iframe cannot fix that on its own: the stored time is read while
// the player builds its initial state, so it has to be cleared BEFORE the new
// document runs. Same-origin in production (the API service serves both the app
// and walkthrough.html), so the parent shares that storage area; on a local dev
// setup where Metro (8081) and the API (8000) differ it throws and we no-op.
const PLAYHEAD_STORAGE_KEY = 'animstage-v3:t';

function clearWalkthroughPlayhead(win?: Window | null) {
  try {
    (win ?? window).localStorage.removeItem(PLAYHEAD_STORAGE_KEY);
  } catch {
    // cross-origin (dev) or storage disabled — nothing we can do from here
  }
}

// Fullscreen API with the WebKit-prefixed fallback older Safari still needs. The film
// plays in the top layer, so no ancestor stacking context can clip or cover it — which
// a position:fixed pseudo-fullscreen inside this ScrollView could not guarantee.
// iPhone Safari has neither form for non-<video> elements, so a play there simply
// stays inline, same as before fullscreen existed. All three helpers are web-only
// callers' responsibility (they touch `document`).
function requestStageFullscreen(el: HTMLElement | null) {
  const req = (el as any)?.requestFullscreen ?? (el as any)?.webkitRequestFullscreen;
  if (!req) return;
  try {
    const p = req.call(el);
    if (p && typeof p.catch === 'function') p.catch(() => {});
  } catch {
    // denied (no user gesture, permissions policy) — the film still plays inline
  }
}

function exitStageFullscreen() {
  const anyDoc = document as any;
  const exit = anyDoc.exitFullscreen ?? anyDoc.webkitExitFullscreen;
  if (!exit) return;
  try {
    const p = exit.call(document);
    if (p && typeof p.catch === 'function') p.catch(() => {});
  } catch {
    // already left (Esc raced the click) — nothing to do
  }
}

function fullscreenElement(): Element | null {
  const anyDoc = document as any;
  return anyDoc.fullscreenElement ?? anyDoc.webkitFullscreenElement ?? null;
}

// Mounts the walkthrough iframe and rewinds its internal player to 0:00 (it has no
// autoplay/seek query param or postMessage API of its own, so this reaches in and
// drives its own transport controls once the bundle has unpacked). Belt-and-braces
// over the pre-mount localStorage clear above: whichever of the two lands, the film
// starts from the beginning. Same-origin in production so this actually works there;
// cross-origin in dev, where it silently no-ops.
function WalkthroughFrame({ frameKey }: { frameKey: number }) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    const tryStart = () => {
      if (cancelled) return;
      attempts += 1;
      try {
        const frame = ref.current;
        const doc = frame?.contentDocument;
        // The transport buttons carry title=, not aria-label=.
        const buttons = Array.from(doc?.querySelectorAll('button') ?? []);
        const rewind = buttons.find((b) => /return to start/i.test(b.title));
        const playPause = buttons.find((b) => /play\/pause/i.test(b.title));
        if (rewind && playPause) {
          // The player rewrites the stored playhead every frame, so this reads its
          // CURRENT time — nearly always 0 here, since the pre-mount clear already
          // did the job. Rewinding unconditionally would snap a healthy film back
          // after the ~300ms it took to get here, so only act on a real offset.
          const at = parseFloat(
            frame?.contentWindow?.localStorage.getItem(PLAYHEAD_STORAGE_KEY) || '0',
          );
          if (isFinite(at) && at > 1) {
            rewind.click();
            // The play/pause button draws a triangle only while PAUSED — the state a
            // film that already ran to its end comes back in. Reading the icon is how
            // we tell without reaching into the bundle's React state.
            if (playPause.querySelector('path[d^="M3 2l9 5"]')) playPause.click();
          }
          return;
        }
      } catch {
        return; // cross-origin (dev) — nothing more we can do from here
      }
      if (attempts < 30) setTimeout(tryStart, 300);
    };
    const t = setTimeout(tryStart, 300);
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
  // The header pill can't fit the brand + five text links + Sign In on a phone — the links
  // (and, worse, Sign In) run off the right edge. Below the breakpoint, drop the secondary
  // static-page links and keep only Sign In, mirroring the app NavBar's compact mode.
  const { width } = useWindowDimensions();
  const compactNav = width < 768;
  const [menuOpen, setMenuOpen] = useState(false);
  // 0 = poster showing. >0 = iframe mounted, keyed by this value so every play click forces
  // a fresh mount (fresh <iframe>) even if it was already playing. The remount alone does
  // NOT rewind — see clearWalkthroughPlayhead above for what actually gets it back to 0:00.
  const [filmKey, setFilmKey] = useState(0);
  // Tracks the browser's actual fullscreen state, not our request: Esc, the browser's
  // own UI and our exit button all funnel through the fullscreenchange listener below,
  // so the toggle can never disagree with what's on screen.
  const [isFullscreen, setIsFullscreen] = useState(false);
  const ctaSecondaryPop = usePopInteraction(3, colors.slate900, 1);
  const scrollRef = useRef<ScrollView>(null);
  const filmSectionY = useRef(0);
  const stageRef = useRef<View>(null);

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    const onChange = () => setIsFullscreen(fullscreenElement() != null);
    document.addEventListener('fullscreenchange', onChange);
    document.addEventListener('webkitfullscreenchange', onChange);
    return () => {
      document.removeEventListener('fullscreenchange', onChange);
      document.removeEventListener('webkitfullscreenchange', onChange);
    };
  }, []);

  // react-native-web hands back the host DOM element as the View's ref.
  function stageDomNode(): HTMLElement | null {
    return (stageRef.current as unknown as HTMLElement) ?? null;
  }

  function playWalkthrough() {
    if (Platform.OS === 'web') {
      // Before the remount, not after: the player reads the stored playhead while
      // building its initial state, so a later clear would arrive too late.
      clearWalkthroughPlayhead();
      setFilmKey((k) => k + 1);
      // Same tick as the click: requestFullscreen needs the user gesture, so it goes
      // on the always-mounted stage container rather than waiting for the iframe.
      requestStageFullscreen(stageDomNode());
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
            <View style={styles.navRow}>
              {!compactNav && NAV_LINKS.map((l) => (
                <Pressable key={l.path} onPress={() => openBackendPage(l.path)}>
                  <Text style={styles.navLink}>{l.label}</Text>
                </Pressable>
              ))}
              <Pressable style={styles.signIn} onPress={() => router.push('/login')}>
                <PersonIcon size={16} color={colors.white} />
                <Text style={styles.signInText}>Sign In</Text>
              </Pressable>
              {/* Mobile: the secondary links collapse behind a hamburger next to Sign In. */}
              {compactNav && (
                <Pressable
                  style={styles.hamburger}
                  onPress={() => setMenuOpen(true)}
                  accessibilityRole="button"
                  accessibilityLabel="Open menu"
                >
                  <View style={styles.hbBar} />
                  <View style={styles.hbBar} />
                  <View style={styles.hbBar} />
                </Pressable>
              )}
            </View>
          </View>
        </View>

        {/* Mobile nav menu (opened by the hamburger) — a small sheet under the header. */}
        <Modal visible={menuOpen} transparent animationType="fade" onRequestClose={() => setMenuOpen(false)}>
          <Pressable style={styles.menuScrim} onPress={() => setMenuOpen(false)}>
            <View style={styles.menuSheet}>
              {NAV_LINKS.map((l) => (
                <Pressable
                  key={l.path}
                  style={styles.menuItem}
                  onPress={() => { setMenuOpen(false); openBackendPage(l.path); }}
                >
                  <Text style={styles.menuItemText}>{l.label}</Text>
                </Pressable>
              ))}
            </View>
          </Pressable>
        </Modal>

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
          <Text style={styles.trialNote}>Free forever, with AI limits. No card required.</Text>
        </View>

        {/* Data / privacy reassurance card */}
        <View style={styles.section}>
          <SoftCard style={styles.privacyCard}>
            <View style={styles.privacyIconTile}>
              <LockIcon size={24} color={colors.navy} />
            </View>
            <View style={styles.privacyBody}>
              <Text style={styles.privacyTitle}>Your data stays yours</Text>
              <Text style={styles.privacyLead}>
                We don't sell or share personal information. Everything Wingman knows about you is yours to look at — and
                yours to delete — whenever you want.
              </Text>
              <View style={styles.privacyBullets}>
                <Bullet color={colors.indigo} text="No personal information is ever sold or shared" />
                <Bullet color={colors.indigo} text="Peek into exactly what we have on you, any time" />
                <Bullet color={colors.indigo} text="Delete your data whenever you want, no questions asked" />
              </View>
            </View>
          </SoftCard>
        </View>

        {/* Audience cards */}
        <View style={[styles.section, styles.cardsRow, compactNav && styles.cardsColumn]}>
          <PopCard style={[styles.audCard]} offset={4}>
            <View style={[styles.audPill, { backgroundColor: colors.navy }]}>
              <Text style={styles.audPillText}>FOR STUDENTS</Text>
            </View>
            <Text style={styles.audTitle}>High school goes fast. Make every year count.</Text>
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
              <Text style={styles.audFootText}>Peace of mind for $4.99/month — less than a cup of coffee.</Text>
            </View>
          </PopCard>
        </View>

        {/* See how it works — film poster, mounts the walkthrough iframe on play */}
        <View style={styles.section} onLayout={(e) => { filmSectionY.current = e.nativeEvent.layout.y; }}>
          <Text style={[styles.sectionTitle, styles.sectionTitleTight]}>See how it works</Text>
          <View style={[styles.filmFrame, popShadow(4)]}>
            <View ref={stageRef} style={styles.filmStage}>
              {filmKey > 0 && Platform.OS === 'web' ? (
                <>
                  <WalkthroughFrame key={filmKey} frameKey={filmKey} />
                  <Pressable
                    style={styles.fsToggle}
                    onPress={() => (isFullscreen ? exitStageFullscreen() : requestStageFullscreen(stageDomNode()))}
                    accessibilityRole="button"
                    accessibilityLabel={isFullscreen ? 'Exit full screen' : 'Enter full screen'}
                  >
                    {isFullscreen
                      ? <ContractIcon size={16} color={colors.white} />
                      : <ExpandIcon size={16} color={colors.white} />}
                    {isFullscreen ? <Text style={styles.fsToggleText}>Exit full screen</Text> : null}
                  </Pressable>
                </>
              ) : (
                <Pressable
                  style={styles.filmStagePressable}
                  onPress={playWalkthrough}
                  accessibilityRole="button"
                  accessibilityLabel="Play the Wingman walkthrough"
                >
                  {/* Poster mirrors the film's closing frame, so it never reads as a blank
                      screen before play — the end-card content sits behind the play chip. */}
                  <View style={styles.posterBrandRow}>
                    <Logo size={28} />
                    <Text style={styles.posterBrand}>Wingman</Text>
                  </View>
                  <Text style={styles.posterHeadline}>Find opportunities. Never miss a deadline.</Text>
                  <Text style={styles.posterBody}>
                    Wingman helps high schoolers discover opportunities that fit who they are, while keeping every
                    deadline in view. For parents, it's peace of mind. For students, it's someone who's got their back.
                  </Text>
                  <View style={styles.playChip}>
                    <View style={{ marginLeft: 3 }}><PlayIcon size={22} color={colors.white} /></View>
                  </View>
                </Pressable>
              )}
            </View>
          </View>
        </View>

        {/* Feature cards */}
        <View style={[styles.section, styles.featRow, compactNav && styles.cardsColumn]}>
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

        {/* Footer */}
        <View style={[styles.section, styles.footer]}>
          <View style={styles.footerLeft}>
            <Logo size={20} />
            <Text style={styles.footerBrand}>Wingman</Text>
          </View>
          <View style={styles.footerLinks}>
            <Pressable onPress={() => openBackendPage('/terms.html')}>
              <Text style={styles.footerLink}>Terms</Text>
            </Pressable>
            <Pressable onPress={() => openBackendPage('/privacy.html')}>
              <Text style={styles.footerLink}>Privacy</Text>
            </Pressable>
          </View>
          <Text style={styles.footerLegal}>
            Highschool Wingman is a doing-business-as (DBA) name of Blufeather Labs LLC. © 2026 Blufeather Labs LLC.
          </Text>
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
    // zIndex is web-only: it keeps the position:sticky header above the scrolled content on
    // web. On native the header is not sticky (it scrolls away), so zIndex serves no purpose —
    // and a zIndex'd child inside a ScrollView triggers an iOS compositing bug that ghosts /
    // overlaps scrolled sections on top of each other (the "broken landing" native report).
    ...(Platform.OS === 'web' ? ({ position: 'sticky', top: 16, zIndex: 50 } as object) : null),
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
  navRow: { flexDirection: 'row', alignItems: 'center', gap: 18, flexWrap: 'wrap' },
  navLink: { fontFamily: fonts.bodyBold, fontSize: 13, color: colors.navLinkDim },
  signIn: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingRight: 8 },
  signInText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white, opacity: 0.9 },
  // Mobile hamburger + the sheet it opens.
  hamburger: { paddingVertical: 8, paddingHorizontal: 8, gap: 4, justifyContent: 'center' },
  hbBar: { width: 20, height: 2, borderRadius: 1, backgroundColor: colors.white },
  menuScrim: { flex: 1, backgroundColor: 'rgba(15,23,42,0.35)', paddingTop: 84, paddingHorizontal: 16, alignItems: 'flex-end' },
  menuSheet: {
    backgroundColor: colors.white,
    borderWidth: 2,
    borderColor: colors.navy,
    borderRadius: radius.lg,
    paddingVertical: 6,
    minWidth: 190,
    shadowColor: colors.slate900,
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.2,
    shadowRadius: 24,
  },
  menuItem: { paddingVertical: 11, paddingHorizontal: 16 },
  menuItemText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.navy },

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

  // "Your data stays yours" reassurance card: icon tile + copy, wrapping on narrow screens.
  privacyCard: { flexDirection: 'row', alignItems: 'flex-start', gap: 20, flexWrap: 'wrap', padding: 32 },
  privacyIconTile: { width: 48, height: 48, borderRadius: 12, backgroundColor: colors.lavender, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  privacyBody: { flex: 1, minWidth: 240, gap: 10 },
  privacyTitle: { fontFamily: fonts.display, fontSize: 20, color: colors.navy },
  privacyLead: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.ink },
  privacyBullets: { gap: 10, marginTop: 4 },

  cardsRow: { flexDirection: 'row', gap: 24, flexWrap: 'wrap' },
  // Narrow screens: stack the cards vertically instead of a wrapped row. A wrapped row
  // stretched the two audience cards to equal height, and audFoot's marginTop:'auto' then
  // pinned the footer to the bottom, leaving a large empty gap in the shorter card. Stacked,
  // each card sizes to its own content. Auto-height column, so the cards' flex:1 is inert.
  cardsColumn: { flexDirection: 'column' },
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
  // Web keeps the 16:9 box (it holds the walkthrough iframe, whose player needs that ratio).
  // Native has NO iframe — just a tap-to-open poster — and on a narrow phone the poster content
  // is far taller than a 16:9 box, so forcing the ratio made it overflow and spill its text over
  // the neighbouring sections (Fabric doesn't honour overflow:hidden here). On native the stage
  // therefore sizes to its content instead.
  filmStage: { width: '100%', backgroundColor: colors.cream, ...(Platform.OS === 'web' ? { aspectRatio: 16 / 9 } : null) },
  filmStagePressable: {
    width: '100%', alignItems: 'center', justifyContent: 'center', gap: 16, paddingHorizontal: 32,
    // height:100% fills the fixed 16:9 box on web; on native there is no fixed height to fill, so
    // the pressable sizes to the poster content (with vertical padding for breathing room).
    ...(Platform.OS === 'web' ? { height: '100%' } : { paddingVertical: 40 }),
  },
  posterBrandRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  posterBrand: { fontFamily: fonts.display, fontSize: 26, color: colors.navy },
  posterHeadline: { fontFamily: fonts.display, fontSize: 26, lineHeight: 32, color: colors.navy, textAlign: 'center' },
  posterBody: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.slate500, textAlign: 'center', maxWidth: 520 },
  playChip: { width: 64, height: 64, borderRadius: 32, backgroundColor: colors.orangeDeep, alignItems: 'center', justifyContent: 'center' },
  // Top-right so it stays clear of the film's own transport controls along the bottom.
  fsToggle: { position: 'absolute', top: 12, right: 12, zIndex: 10, flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: 'rgba(10, 10, 10, 0.65)', borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 8 },
  fsToggleText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.white },

  featRow: { flexDirection: 'row', gap: 24, flexWrap: 'wrap' },
  featCard: { flex: 1, minWidth: 240, padding: 32, gap: 8 },
  featTitle: { fontFamily: fonts.display, fontSize: 18, color: colors.navy },
  featBody: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.ink },

  darkCta: { borderRadius: radius.lg, padding: 48, alignItems: 'center' },
  darkCtaTitle: { fontFamily: fonts.display, fontSize: 24, color: colors.white, marginBottom: 8, textAlign: 'center' },
  darkCtaSub: { fontFamily: fonts.bodyMed, fontSize: 15, color: colors.grayLighter, marginBottom: 24, textAlign: 'center' },
  darkCtaBtn: { paddingHorizontal: 32, paddingVertical: 14 },

  footer: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderTopWidth: 1, borderTopColor: colors.slate200, paddingTop: 32, paddingBottom: 32, flexWrap: 'wrap', gap: 12 },
  footerLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  footerBrand: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500 },
  footerLinks: { flexDirection: 'row', gap: 20 },
  footerLink: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500 },
  footerLegal: { width: '100%', fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 18, color: colors.muted },
});
