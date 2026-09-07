import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type GestureResponderEvent } from 'react-native';
import { openBackendPage } from '@/ui/openPage';
import { useAuth } from '@/auth/AuthContext';
import { googleHandoffFromUrl } from '@/auth/googleSignIn';
import { PopButton, PopCard, Screen, Txt } from '@/ui/components';
import { colors, fonts, radius, space } from '@/ui/theme';

// The one-time token, read ONCE at module scope on web.
//
// S0-9: the server now returns it in the URL FRAGMENT for a web destination, and
// useLocalSearchParams only ever sees the query string — fragments are not sent to a server
// and expo-router does not surface them. So the fragment is read straight off location, and
// then SCRUBBED from the history entry: leaving it there would put the token back into the
// one place (browser history) the fragment move exists to keep it out of.
//
// Read at module scope, not in the effect, because the scrub has to happen before any
// re-render can re-read a now-empty hash.
function takeWebHandoff(): string {
  if (typeof globalThis === 'undefined' || !globalThis.location) return '';
  const token = googleHandoffFromUrl(globalThis.location.href) ?? '';
  if (token && globalThis.history?.replaceState) {
    const { pathname, search } = globalThis.location;
    globalThis.history.replaceState(null, '', `${pathname}${search}`);
  }
  return token;
}

const webHandoff = takeWebHandoff();

// Resumes the Google redirect flow: reads the one-time google_token, resolves it, then either
// enters the app (existing/linked account) or collects consent for a new account.
export default function GoogleAuth() {
  const router = useRouter();
  const { googleSession, googleFinish } = useAuth();
  // The query-string form is still accepted: it is what a custom-scheme (native) redirect
  // carries, and what a deep link opened straight into this route would carry.
  const params = useLocalSearchParams<{ google_token?: string }>();
  const handoff =
    webHandoff || (typeof params.google_token === 'string' ? params.google_token : '');

  const [phase, setPhase] = useState<'resolving' | 'pending' | 'error'>('resolving');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<{ firstName?: string; lastName?: string; email?: string }>({});
  const [busy, setBusy] = useState(false);

  const [isAdult, setIsAdult] = useState(false);
  const [parentalConsent, setParentalConsent] = useState(false);
  const [acceptedTerms, setAcceptedTerms] = useState(false);

  // The exact gate the server enforces (_check_signup_consent): the Terms must be accepted,
  // and the account holder must be an adult OR have a guardian's permission. "Create account"
  // stays disabled until this holds, so the button can't be pressed into a server rejection.
  const canSubmit = acceptedTerms && (isAdult || parentalConsent);

  const resolved = useRef(false);
  useEffect(() => {
    if (resolved.current) return;
    resolved.current = true;
    if (!handoff) {
      setError('Missing sign-in token. Please try again.');
      setPhase('error');
      return;
    }
    (async () => {
      try {
        const result = await googleSession(handoff);
        if (result.status === 'session') {
          router.replace('/(app)');
        } else {
          setInfo(result);
          setPhase('pending');
        }
      } catch (e) {
        setError((e as Error).message || 'Google sign-in failed.');
        setPhase('error');
      }
    })();
  }, [handoff, googleSession, router]);

  async function finish() {
    if (!canSubmit) return;
    setError(null);
    setBusy(true);
    try {
      await googleFinish(handoff, { isAdult, parentalConsent, acceptedTerms });
      router.replace('/(app)');
    } catch (e) {
      setError((e as Error).message || 'Could not finish sign-up.');
    } finally {
      setBusy(false);
    }
  }

  if (phase === 'resolving') {
    return (
      <Screen scroll={false}>
        <View style={styles.center}>
          <ActivityIndicator color={colors.navy} />
          <Txt variant="body">Signing you in…</Txt>
        </View>
      </Screen>
    );
  }

  if (phase === 'error') {
    return (
      <Screen scroll={false}>
        <View style={styles.center}>
          <PopCard color={colors.white} style={{ gap: space.md }}>
            <Txt variant="h2">Sign-in didn't finish</Txt>
            <Txt variant="body">{error}</Txt>
            <PopButton label="Back to sign in" onPress={() => router.replace('/login')} />
          </PopCard>
        </View>
      </Screen>
    );
  }

  // Consent screen — centered card (mockup: Google Sign-Up - Consent).
  const openLink = (path: string) => (e: GestureResponderEvent) => {
    // Don't let the tap also toggle the row this link sits inside.
    e.stopPropagation();
    openBackendPage(path);
  };

  return (
    <View style={styles.page}>
      <View style={styles.wrap}>
        <View style={styles.headBlock}>
          <Text style={styles.kicker}>GOOGLE SIGN-IN</Text>
          <Text style={styles.title}>Almost there</Text>
          <Text style={styles.subtitle}>
            Finish setting up{info.firstName ? `, ${info.firstName}` : ''}
            {info.email ? ` (${info.email})` : ''}.
          </Text>
        </View>

        <View style={styles.card}>
          {/* Consent wording is kept identical to the password-registration form (login.tsx):
              same three items, and the parental-consent row shown only when the account holder
              is not an adult. The server gate (_check_signup_consent) is the same for both paths. */}
          <ConsentRow label="I am 18 years of age or older." value={isAdult} onValueChange={setIsAdult} />
          {!isAdult && (
            <ConsentRow
              label="I am at least 13, and my parent or legal guardian has given me permission to use Wingman and agrees to the Terms of Use on my behalf."
              value={parentalConsent}
              onValueChange={setParentalConsent}
            />
          )}
          <ConsentRow
            label={
              <>
                I have read and agree to the{' '}
                <Text style={styles.legalLink} onPress={openLink('/terms.html')}>Terms of Use</Text>
                {' '}and the{' '}
                <Text style={styles.legalLink} onPress={openLink('/privacy.html')}>Privacy Policy</Text>.
              </>
            }
            value={acceptedTerms}
            onValueChange={setAcceptedTerms}
          />

          {!!error && <Text style={styles.error}>{error}</Text>}

          <Pressable
            onPress={finish}
            disabled={!canSubmit || busy}
            accessibilityRole="button"
            accessibilityState={{ disabled: !canSubmit || busy }}
            style={[styles.cta, (!canSubmit || busy) && styles.ctaDisabled]}
          >
            {busy ? (
              <ActivityIndicator color={colors.white} />
            ) : (
              <Text style={styles.ctaText}>Create account</Text>
            )}
          </Pressable>
        </View>
      </View>
    </View>
  );
}

// A whole tappable consent row: the pill toggle + its label (the mockup makes the entire row
// cursor:pointer). Links inside the label stop propagation so they open instead of toggling.
function ConsentRow({ label, value, onValueChange }: { label: ReactNode; value: boolean; onValueChange: (v: boolean) => void }) {
  return (
    <Pressable
      style={styles.row}
      onPress={() => onValueChange(!value)}
      accessibilityRole="switch"
      accessibilityState={{ checked: value }}
    >
      <View style={[styles.track, value && styles.trackOn]}>
        <View style={[styles.thumb, value && styles.thumbOn]} />
      </View>
      <Text style={styles.rowLabel}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.md, padding: space.xl },

  // Consent screen: full-bleed cream, one centered column.
  page: { flex: 1, backgroundColor: colors.cream, alignItems: 'center', justifyContent: 'center', paddingVertical: 24, paddingHorizontal: 16 },
  wrap: { width: '100%', maxWidth: 420, gap: 20 },

  headBlock: {},
  kicker: { fontFamily: fonts.bodyXBold, fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', color: colors.slate500, marginBottom: 6 },
  title: { fontFamily: fonts.display, fontSize: 30, lineHeight: 36, color: colors.navy },
  subtitle: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: '#4A6685', marginTop: 4 },

  card: {
    backgroundColor: colors.white,
    borderWidth: 3,
    borderColor: colors.navy,
    borderRadius: radius.lg,
    padding: 24,
    gap: 16,
    // 4px 4px 0 pop shadow (mockup).
    shadowColor: colors.navy,
    shadowOffset: { width: 4, height: 4 },
    shadowOpacity: 1,
    shadowRadius: 0,
  },

  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  rowLabel: { flex: 1, fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 17, color: colors.slate900, marginTop: 1 },
  legalLink: { fontFamily: fonts.bodyBold, color: colors.purple, textDecorationLine: 'underline' },

  // Pill toggle: 40x22 track, 18x18 thumb sliding 18px.
  track: { width: 40, height: 22, borderRadius: 11, backgroundColor: '#D9DEEB', marginTop: 1 },
  trackOn: { backgroundColor: colors.purple },
  thumb: {
    position: 'absolute',
    top: 2,
    left: 2,
    width: 18,
    height: 18,
    borderRadius: 9,
    backgroundColor: colors.white,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.2,
    shadowRadius: 2,
  },
  thumbOn: { left: 20 },

  cta: {
    borderRadius: radius.pill,
    paddingVertical: 14,
    paddingHorizontal: 24,
    backgroundColor: colors.orange,
    borderWidth: 2,
    borderColor: colors.navy,
    alignItems: 'center',
    marginTop: 4,
    shadowColor: colors.navy,
    shadowOffset: { width: 3, height: 3 },
    shadowOpacity: 1,
    shadowRadius: 0,
  },
  // Disabled until the consent prerequisites are met.
  ctaDisabled: { opacity: 0.5 },
  ctaText: { fontFamily: fonts.bodyXBold, fontSize: 15, color: colors.white },

  error: { color: colors.red, fontFamily: fonts.bodyBold, fontSize: 13 },
});
