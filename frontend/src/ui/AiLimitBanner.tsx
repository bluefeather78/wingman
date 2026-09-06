import { useRouter } from 'expo-router';
import { useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState } from 'react';
import { Animated, Easing, Pressable, StyleSheet, Text, View } from 'react-native';
import { useAuth } from '@/auth/AuthContext';
import {
  aiLimitReached,
  dismissAiLimitBanner,
  isAiLimitBannerDismissed,
  reopenAiLimitBanner,
  subscribeAiLimitBanner,
} from '@/lib/aiLimit';
import { APP_MAX_WIDTH, colors, fonts, radius } from './theme';

// The full-width "You're out of AI actions for today" bar (mockup: AI Limit Reached - Modal).
// Mounted once, above the NavBar in (app)/_layout, so it shows on every tab. Visible only for a
// Free account that has spent its daily allowance AND has not dismissed it this occurrence.
//
// Dismissible via ✕, but it comes back whenever the student next initiates an AI action —
// every greyed-out AI control calls reopenAiLimitBanner() when tapped (see useAiGate). It also
// re-surfaces itself the moment the allowance first hits zero (the transition effect below).

export function AiLimitBanner() {
  const router = useRouter();
  const { user, allowance } = useAuth();
  const reached = aiLimitReached(user, allowance);

  // Re-render when the module's dismissed flag flips (dismiss / reopen from anywhere).
  const [, bump] = useReducer((n: number) => n + 1, 0);
  useEffect(() => subscribeAiLimitBanner(bump), []);

  // Surface the banner the moment the account crosses into "out of actions", even if it was
  // dismissed on a previous day/occurrence. reopen is a no-op when already visible.
  const wasReached = useRef(reached);
  useEffect(() => {
    if (reached && !wasReached.current) reopenAiLimitBanner();
    wasReached.current = reached;
  }, [reached]);

  const visible = reached && !isAiLimitBannerDismissed();

  // Roll-in: every time the banner appears — the first time the allowance hits zero, or when a
  // greyed AI control is tapped after a dismiss — it slides down from above its slot and fades
  // in. useLayoutEffect resets the value before paint, so there's no flash of the shown bar.
  const anim = useRef(new Animated.Value(0)).current;
  const [barH, setBarH] = useState(120);
  useLayoutEffect(() => {
    if (!visible) return;
    anim.setValue(0);
    const run = Animated.timing(anim, {
      toValue: 1,
      duration: 320,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: false,
    });
    run.start();
    return () => run.stop();
  }, [visible, anim]);

  if (!visible) return null;

  const translateY = anim.interpolate({ inputRange: [0, 1], outputRange: [-barH, 0] });

  return (
    <Animated.View
      onLayout={(e) => setBarH(e.nativeEvent.layout.height)}
      style={[styles.bar, { opacity: anim, transform: [{ translateY }] }]}
    >
      <View style={styles.inner}>
        <View style={styles.message}>
          <Text style={styles.line}>
            You're out of AI actions for today.{' '}
            <Text style={styles.lineSub}>Resets at midnight UTC, or go unlimited now.</Text>
          </Text>
        </View>

        <View style={styles.right}>
          <Pressable style={styles.cta} onPress={() => router.push('/(app)/subscription' as never)}>
            <Text style={styles.ctaText}>Go Unlimited, $9.99/mo</Text>
          </Pressable>
          <Pressable onPress={dismissAiLimitBanner} hitSlop={10} accessibilityLabel="Dismiss">
            <Text style={styles.closeText}>✕</Text>
          </Pressable>
        </View>
      </View>
    </Animated.View>
  );
}

// The shared AI gate. Screens call this and, for each AI control, apply `dimStyle` and wrap the
// press handler with `guard`. When out of quota, `guard` re-shows the banner instead of running
// the (server-refused) action — so a greyed control still explains itself on tap.
export function useAiGate() {
  const { user, allowance } = useAuth();
  const reached = aiLimitReached(user, allowance);
  const guard = useCallback(
    <A extends unknown[]>(fn: (...args: A) => void) =>
      (...args: A) => {
        if (reached) {
          reopenAiLimitBanner();
          return;
        }
        fn(...args);
      },
    [reached],
  );
  return { reached, guard, dimStyle: reached ? styles.dimmed : null };
}

const styles = StyleSheet.create({
  bar: {
    backgroundColor: colors.navy,
    borderBottomWidth: 3,
    borderBottomColor: colors.slate900,
    // A soft drop shadow like the mockup's — RN maps these to elevation on native.
    shadowColor: colors.slate900,
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.25,
    shadowRadius: 24,
    zIndex: 100,
  },
  inner: {
    width: '100%',
    paddingVertical: 14,
    paddingHorizontal: 24,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 20,
    flexWrap: 'wrap',
  },
  message: { flex: 1, minWidth: 260 },
  line: { fontFamily: fonts.bodyBold, fontSize: 14, lineHeight: 20, color: colors.white },
  lineSub: { fontFamily: fonts.bodyMed, color: '#B7D3E8' },

  right: { flexDirection: 'row', alignItems: 'center', gap: 16, flexShrink: 0 },
  cta: {
    borderRadius: radius.pill,
    paddingVertical: 9,
    paddingHorizontal: 18,
    backgroundColor: colors.orange,
    borderWidth: 2,
    borderColor: colors.slate900,
    // pop shadow (3,3)
    shadowColor: colors.slate900,
    shadowOffset: { width: 3, height: 3 },
    shadowOpacity: 1,
    shadowRadius: 0,
  },
  ctaText: { fontFamily: fonts.bodyBold, fontSize: 13, color: colors.white },
  closeText: { fontFamily: fonts.bodyXBold, fontSize: 16, color: 'rgba(255,255,255,0.6)' },

  // Applied to any AI control while the allowance is spent.
  dimmed: { opacity: 0.5 },
});
