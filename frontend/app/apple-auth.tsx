import { useRouter } from 'expo-router';
import { useState, type ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type GestureResponderEvent } from 'react-native';
import { openBackendPage } from '@/ui/openPage';
import { useAuth } from '@/auth/AuthContext';
import { takeApplePending, type ApplePending } from '@/auth/appleSignIn';
import { PopButton, PopCard, Screen, Txt } from '@/ui/components';
import { colors, fonts, radius, space } from '@/ui/theme';

// Consent screen for a NEW Sign in with Apple account (App Store 4.8). login.tsx has already
// resolved the identity token: an existing/linked account went straight into the app, and only
// a pending (new) account lands here. It re-POSTs the SAME identity token together with the
// consent booleans via appleFinish. Deliberately self-contained — it mirrors google-auth.tsx's
// consent card rather than sharing it, so the (live, web) Google flow is never touched. Native
// iOS only; there is no web/Android entry point to this route.
export default function AppleAuth() {
  const router = useRouter();
  const { appleFinish } = useAuth();
  // Read the stashed credential ONCE, on first render (not at module scope — it is set at
  // runtime by login.tsx just before navigating here).
  const [pending] = useState<ApplePending | null>(() => takeApplePending());

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [isAdult, setIsAdult] = useState(false);
  const [parentalConsent, setParentalConsent] = useState(false);
  const [acceptedTerms, setAcceptedTerms] = useState(false);

  // The exact gate the server enforces (_check_signup_consent): Terms accepted, and adult OR a
  // guardian's permission. The button stays disabled until this holds.
  const canSubmit = acceptedTerms && (isAdult || parentalConsent);

  async function finish() {
    if (!canSubmit || !pending) return;
    setError(null);
    setBusy(true);
    try {
      await appleFinish(pending.credential, { isAdult, parentalConsent, acceptedTerms });
      router.replace('/(app)');
    } catch (e) {
      setError((e as Error).message || 'Could not finish sign-up.');
    } finally {
      setBusy(false);
    }
  }

  // No stashed credential means this route was reached out of order (or after a reload dropped
  // the in-memory credential). Send the student back to sign in rather than showing a dead form.
  if (!pending) {
    return (
      <Screen scroll={false}>
        <View style={styles.center}>
          <PopCard color={colors.white} style={{ gap: space.md }}>
            <Txt variant="h2">Sign-in didn't finish</Txt>
            <Txt variant="body">Your Apple sign-in expired. Please try again.</Txt>
            <PopButton label="Back to sign in" onPress={() => router.replace('/login')} />
          </PopCard>
        </View>
      </Screen>
    );
  }

  const openLink = (path: string) => (e: GestureResponderEvent) => {
    e.stopPropagation();
    openBackendPage(path);
  };

  return (
    <View style={styles.page}>
      <View style={styles.wrap}>
        <View style={styles.headBlock}>
          <Text style={styles.kicker}>SIGN IN WITH APPLE</Text>
          <Text style={styles.title}>Almost there</Text>
          <Text style={styles.subtitle}>
            Finish setting up{pending.firstName ? `, ${pending.firstName}` : ''}
            {pending.email ? ` (${pending.email})` : ''}.
          </Text>
        </View>

        <View style={styles.card}>
          {/* Same three consent items and gate as the Google and password paths. */}
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
    shadowColor: colors.navy,
    shadowOffset: { width: 4, height: 4 },
    shadowOpacity: 1,
    shadowRadius: 0,
  },

  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  rowLabel: { flex: 1, fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 17, color: colors.slate900, marginTop: 1 },
  legalLink: { fontFamily: fonts.bodyBold, color: colors.purple, textDecorationLine: 'underline' },

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
  ctaDisabled: { opacity: 0.5 },
  ctaText: { fontFamily: fonts.bodyXBold, fontSize: 15, color: colors.white },

  error: { color: colors.red, fontFamily: fonts.bodyBold, fontSize: 13 },
});
