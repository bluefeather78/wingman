import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Switch, View } from 'react-native';
import { useAuth } from '@/auth/AuthContext';
import { PopButton, PopCard, Screen, Txt } from '@/ui/components';
import { colors, fonts, space } from '@/ui/theme';

// Resumes the Google redirect flow: reads the one-time google_token, resolves it, then either
// enters the app (existing/linked account) or collects consent for a new account.
export default function GoogleAuth() {
  const router = useRouter();
  const { googleSession, googleFinish } = useAuth();
  const params = useLocalSearchParams<{ google_token?: string }>();
  const handoff = typeof params.google_token === 'string' ? params.google_token : '';

  const [phase, setPhase] = useState<'resolving' | 'pending' | 'error'>('resolving');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<{ firstName?: string; lastName?: string; email?: string }>({});
  const [busy, setBusy] = useState(false);

  const [isAdult, setIsAdult] = useState(false);
  const [parentalConsent, setParentalConsent] = useState(false);
  const [acceptedTerms, setAcceptedTerms] = useState(false);

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

  return (
    <Screen>
      <View style={styles.head}>
        <Txt variant="label">GOOGLE SIGN-IN</Txt>
        <Txt variant="hero">Almost there</Txt>
        <Txt variant="body">
          Finish setting up{info.firstName ? `, ${info.firstName}` : ''}
          {info.email ? ` (${info.email})` : ''}.
        </Txt>
      </View>

      <PopCard style={{ gap: space.md }}>
        <ConsentRow label="I'm 18 or older" value={isAdult} onValueChange={setIsAdult} />
        <ConsentRow
          label="If under 18, I have a parent/guardian's permission (Terms §2)"
          value={parentalConsent}
          onValueChange={setParentalConsent}
        />
        <ConsentRow
          label="I accept the Terms and Privacy Policy"
          value={acceptedTerms}
          onValueChange={setAcceptedTerms}
        />
        {!!error && <Txt style={styles.error}>{error}</Txt>}
        <PopButton label="Create account" onPress={finish} loading={busy} full />
      </PopCard>
    </Screen>
  );
}

function ConsentRow({ label, value, onValueChange }: { label: string; value: boolean; onValueChange: (v: boolean) => void }) {
  return (
    <View style={styles.row}>
      <Switch value={value} onValueChange={onValueChange} trackColor={{ true: colors.purple, false: colors.hairline }} thumbColor={colors.white} />
      <Txt variant="small" style={styles.rowLabel}>
        {label}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.md, padding: space.xl },
  head: { gap: space.xs, marginBottom: space.xs },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  rowLabel: { flex: 1 },
  error: { color: colors.red, fontFamily: fonts.bodyBold },
});
