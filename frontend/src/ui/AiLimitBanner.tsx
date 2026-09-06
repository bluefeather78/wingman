import { useRouter } from 'expo-router';
import { useCallback, useEffect, useReducer, useRef } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
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

const DEFAULT_LIMIT = 10;

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

  if (!reached || isAiLimitBannerDismissed()) return null;

  const limit = typeof allowance?.limit === 'number' && allowance.limit > 0 ? allowance.limit : DEFAULT_LIMIT;
  const remaining = typeof allowance?.remaining === 'number' ? Math.max(0, allowance.remaining) : 0;
  const segs = Array.from({ length: limit });

  return (
    <View style={styles.bar}>
      <View style={styles.inner}>
        <Pressable style={styles.close} onPress={dismissAiLimitBanner} hitSlop={10} accessibilityLabel="Dismiss">
          <Text style={styles.closeText}>✕</Text>
        </Pressable>

        {/* Message */}
        <View style={styles.message}>
          <Text style={styles.title}>You're out of AI actions for today</Text>
          <Text style={styles.sub}>
            Free plan includes {limit} AI actions a day — profile chats, match-finding, deadline checks. Yours reset at midnight UTC.
          </Text>
        </View>

        {/* Meter */}
        <View style={styles.meter}>
          <View style={styles.meterHead}>
            <Text style={styles.meterLabel}>Today's actions</Text>
            <Text style={styles.meterCount}>{remaining} / {limit}</Text>
          </View>
          <View style={styles.meterBar}>
            {segs.map((_, i) => (
              <View key={i} style={[styles.seg, i < remaining && styles.segFilled]} />
            ))}
          </View>
        </View>

        {/* Upsell + CTA */}
        <View style={styles.upsell}>
          <Text style={styles.upsellText}>
            <Text style={styles.upsellStrong}>Wingman Unlimited</Text> removes the daily cap — <Text style={styles.upsellPrice}>$9.99/mo</Text>.
          </Text>
          <Pressable style={styles.cta} onPress={() => router.push('/(app)/subscription' as never)}>
            <Text style={styles.ctaText}>Go Unlimited →</Text>
          </Pressable>
        </View>
      </View>
    </View>
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
    maxWidth: 1120,
    alignSelf: 'center',
    paddingVertical: 18,
    paddingHorizontal: 24,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 24,
    flexWrap: 'wrap',
  },
  close: { position: 'absolute', top: 6, right: 10, zIndex: 2 },
  closeText: { fontFamily: fonts.bodyXBold, fontSize: 16, color: 'rgba(255,255,255,0.6)' },

  message: { flexGrow: 2, flexShrink: 1, flexBasis: 260, minWidth: 260 },
  title: { fontFamily: fonts.display, fontSize: 18, lineHeight: 24, color: colors.white },
  sub: { fontFamily: fonts.bodyMed, fontSize: 13, lineHeight: 19, color: colors.cream, opacity: 0.85, marginTop: 4 },

  meter: { flexGrow: 1, flexShrink: 1, flexBasis: 180, minWidth: 180 },
  meterHead: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between' },
  meterLabel: { fontFamily: fonts.bodyXBold, fontSize: 10, letterSpacing: 0.4, textTransform: 'uppercase', color: '#B7D3E8' },
  meterCount: { fontFamily: fonts.display, fontSize: 14, color: colors.white },
  meterBar: { flexDirection: 'row', gap: 3, marginTop: 6 },
  seg: { flex: 1, height: 6, borderRadius: 3, backgroundColor: 'rgba(255,255,255,0.2)' },
  segFilled: { backgroundColor: colors.white },

  upsell: { flexGrow: 1, flexShrink: 1, flexBasis: 280, minWidth: 280, flexDirection: 'row', alignItems: 'center', gap: 14, justifyContent: 'flex-end', flexWrap: 'wrap' },
  upsellText: { fontFamily: fonts.bodyMed, fontSize: 13, lineHeight: 18, color: colors.cream, flexShrink: 1 },
  upsellStrong: { fontFamily: fonts.bodyXBold, color: colors.white },
  upsellPrice: { fontFamily: fonts.bodyXBold, color: colors.orange },
  cta: {
    borderRadius: radius.pill,
    paddingVertical: 10,
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

  // Applied to any AI control while the allowance is spent.
  dimmed: { opacity: 0.5 },
});
