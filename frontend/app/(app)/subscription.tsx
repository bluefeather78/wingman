import { useEffect, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { useAuth } from '@/auth/AuthContext';
import { PopButton, Screen, SoftCard, usePopInteraction } from '@/ui/components';
import { colors, fonts, popShadow, radius } from '@/ui/theme';
import { isPaidTier, resetsInLabel } from '@/lib/tier';
import type { AllowanceSnapshot } from '@/api/types';

interface SubState {
  status?: string;
  days_left?: number;
  has_access?: boolean;
  in_paid_period?: boolean;
  ai_tier?: string;
  trial_ends_at?: string | null;
  subscription_end_at?: string | null;
  allowance?: AllowanceSnapshot;
  [key: string]: unknown;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '…';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '…';
  return new Date(t).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
}

// Manage Plan, reached from the account drawer. Two-tier model: no lockout, so this is a
// tier dashboard — the Free plan's daily AI-action meter vs Wingman Unlimited — not a paywall.
// Payments stay deferred: the promo-code flow works and Upgrade surfaces the backend's answer
// (Stripe is not configured here, which is what keeps the CTA "gated" until it is). The status
// read on mount write-throughs to the cached session, so redeeming a grant code here flips the
// tier to Unlimited immediately rather than on the next sign-in.
export default function Subscription() {
  const { user, allowance: liveAllowance } = useAuth();
  const [sub, setSub] = useState<SubState | null>((user?.subscription as SubState) ?? null);
  const [promo, setPromo] = useState('');
  const [promoStatus, setPromoStatus] = useState('');
  const [upgradeStatus, setUpgradeStatus] = useState('');
  // Cancel flow — an overlay confirmation tile (mockup), not an inline action.
  const [showCancel, setShowCancel] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [cancelStatus, setCancelStatus] = useState('');
  const promoBtnPop = usePopInteraction(3, colors.navy, 1);

  useEffect(() => {
    let alive = true;
    httpClient.subscriptionStatus().then((s) => alive && setSub(s as SubState)).catch(() => {});
    return () => { alive = false; };
  }, []);

  const status = sub?.status ?? 'free';
  // Two-tier model: no lockout. `paid` (Unlimited) vs the metered Free plan is the only axis.
  const paid = sub?.ai_tier === 'paid' || sub?.in_paid_period === true || isPaidTier(user);
  // Prefer the live meter (429 / AI echo); fall back to the snapshot the status read carried.
  const allowance: AllowanceSnapshot | null = liveAllowance ?? sub?.allowance ?? null;
  const used = typeof allowance?.used === 'number' ? allowance.used : null;
  const limit = typeof allowance?.limit === 'number' ? allowance.limit : null;
  const remaining = typeof allowance?.remaining === 'number' ? allowance.remaining : null;
  const badge = paid
    ? { label: status === 'active' ? 'Unlimited' : 'Comped', bg: '#D1FAE5', fg: '#065F46' }
    : { label: 'Free', bg: '#DEF5B0', fg: colors.navy };
  const planName = paid ? 'Wingman Unlimited' : 'Free plan';
  const renewsDate = fmtDate(sub?.subscription_end_at as string);
  // past_due / canceled are still Free-with-access now — a gentle note, never a lockout.
  const softNote = !paid && status === 'past_due'
    ? 'We could not charge your card, so you are on the Free plan for now. Update your payment details to go back to Unlimited.'
    : !paid && status === 'canceled'
    ? 'Your subscription has ended and you are on the Free plan. Resubscribe any time to lift the daily AI cap.'
    : null;

  async function applyPromo() {
    const code = promo.trim();
    if (!code) return;
    setPromoStatus('Checking…');
    try {
      const v = await httpClient.validatePromo(code);
      if (!v.valid) {
        setPromoStatus(v.error || 'That code is not valid.');
        return;
      }
      if (v.kind === 'grant') {
        const r = await httpClient.redeemPromo(code);
        setPromoStatus((r as { message?: string }).message || '✓ Code applied to your account!');
        httpClient.subscriptionStatus().then((s) => setSub(s as SubState)).catch(() => {});
      } else {
        setPromoStatus(`✓ ${v.description || 'Valid code'} — applied at checkout.`);
      }
    } catch (e) {
      setPromoStatus((e as Error).message || 'Could not validate the code.');
    }
  }

  async function cancelSubscription() {
    setCanceling(true);
    setCancelStatus('');
    try {
      await httpClient.subscriptionCancel();
      setShowCancel(false);
      httpClient.subscriptionStatus().then((s) => setSub(s as SubState)).catch(() => {});
    } catch (e) {
      // Stripe may be unconfigured (no subscription to cancel) — surface the backend's answer
      // inside the tile rather than closing it on a silent failure.
      setCancelStatus((e as Error).message || 'Could not cancel right now. Please try again.');
    } finally {
      setCanceling(false);
    }
  }

  async function upgrade() {
    setUpgradeStatus('Starting checkout…');
    try {
      const url = await httpClient.subscriptionCheckout(promo.trim());
      if (url) {
        setUpgradeStatus('Redirecting to checkout…');
        (globalThis as { location?: { href: string } }).location && ((globalThis as { location: { href: string } }).location.href = url);
      } else {
        setUpgradeStatus('Checkout is not available right now.');
      }
    } catch (e) {
      // Stripe isn't configured in this environment — surface the backend's answer.
      setUpgradeStatus((e as Error).message || 'Checkout is not available right now.');
    }
  }

  return (
    <>
    <Screen>
      <SoftCard style={styles.card}>
        <View style={styles.headWrap}>
          <Text style={styles.title}>Your Subscription</Text>
          <Text style={styles.subTitle}>Manage your plan and billing</Text>
        </View>

        {!!softNote && (
          <View style={styles.softNote}>
            <Text style={styles.softNoteText}>{softNote}</Text>
          </View>
        )}

        {/* Status card */}
        <View style={styles.statusCard}>
          <View style={styles.statusRow}>
            <View style={styles.statusRowLeft}>
              <Text style={styles.tinyLabel}>CURRENT PLAN</Text>
              <Text style={styles.planName}>{planName}</Text>
            </View>
            <View style={[styles.badge, { backgroundColor: badge.bg }]}>
              <Text style={[styles.badgeText, { color: badge.fg }]}>{badge.label}</Text>
            </View>
          </View>
          {paid ? (
            <>
              <View style={[styles.countdown, styles.activeInfo]}>
                <Text style={[styles.countdownTitle, { color: '#065F46' }]}>No daily AI cap</Text>
                <Text style={[styles.countdownSub, { color: '#047857' }]}>
                  {status === 'canceled'
                    ? `Access ends ${renewsDate}`
                    : status === 'active'
                    ? `Renews ${renewsDate}`
                    : 'Unlimited AI actions while your access lasts'}
                </Text>
              </View>
              {/* Cancel is only offered on a live paying subscription. A comped/beta grant has
                  nothing to cancel (it lapses on its own), and a canceled-but-still-in-period
                  account has already cancelled. */}
              {status === 'active' && (
                <Pressable style={styles.cancelLinkWrap} onPress={() => { setCancelStatus(''); setShowCancel(true); }}>
                  <Text style={styles.cancelLink}>Cancel subscription</Text>
                </Pressable>
              )}
            </>
          ) : (
            <View style={styles.meterBox}>
              <Text style={styles.meterTitle}>
                {used !== null && limit !== null
                  ? `${Math.max(0, (remaining ?? limit - used))} of ${limit} AI actions left today`
                  : 'Your daily AI actions'}
              </Text>
              {used !== null && limit !== null && (
                <View style={styles.meterTrack}>
                  <View style={[styles.meterFill, { width: `${Math.min(100, Math.round((used / Math.max(1, limit)) * 100))}%` }]} />
                </View>
              )}
              <Text style={styles.meterSub}>
                {used !== null && limit !== null ? `Used ${used} of ${limit} · resets ${resetsInLabel(allowance)}` : 'Resets every day at midnight UTC'}
              </Text>
            </View>
          )}
        </View>

        {/* Free vs Unlimited */}
        <View style={styles.plansBox}>
          <View style={styles.plansHead}>
            <Text style={styles.plansHeadText}>Compare plans</Text>
          </View>
          <View style={styles.plansBody}>
            <View style={styles.planRow}>
              <View style={styles.flex1}>
                <Text style={styles.planRowName}>Free</Text>
                <Text style={styles.planRowPrice}>$0</Text>
                <Text style={styles.planRowNote}>Available at no charge, for as long as you use Wingman</Text>
              </View>
              {!paid && (
                <View style={[styles.badge, { backgroundColor: '#DEF5B0' }]}>
                  <Text style={[styles.badgeText, { color: colors.navy }]}>Current</Text>
                </View>
              )}
            </View>
            <View style={{ gap: 4, marginTop: 8 }}>
              <Text style={styles.featureLine}>✓ Full opportunity catalog &amp; search</Text>
              <Text style={styles.featureLine}>✓ Quest Log tracking &amp; calendar sync</Text>
              <Text style={styles.featureLine}>✓ {limit ?? 10} AI actions a day, profile chats, match-finding, deadline checks</Text>
            </View>
            <View style={styles.planDivider} />
            <View style={styles.planRow}>
              <View style={styles.flex1}>
                <Text style={styles.planRowName}>Wingman Unlimited</Text>
                <Text style={styles.planRowPrice}>
                  $9.99<Text style={styles.planRowPer}>/month</Text>
                </Text>
                <Text style={styles.planRowNote}>Billed monthly in advance. Cancel any time — you'll keep access through the end of the current period.</Text>
              </View>
              {paid ? (
                <View style={[styles.badge, { backgroundColor: '#D1FAE5' }]}>
                  <Text style={[styles.badgeText, { color: '#065F46' }]}>Current</Text>
                </View>
              ) : (
                <PopButton label="Go Unlimited" small square onPress={upgrade} />
              )}
            </View>
            <View style={{ gap: 4, marginTop: 8 }}>
              <Text style={styles.featureLine}>✓ Everything in Free</Text>
              <Text style={styles.featureLine}>✓ Unlimited profile chats &amp; match-finding</Text>
              <Text style={styles.featureLine}>✓ Unlimited deadline re-checks &amp; resume imports</Text>
            </View>
            {!!upgradeStatus && <Text style={styles.promoStatus}>{upgradeStatus}</Text>}
          </View>
        </View>

        {/* Promo code */}
        <View style={styles.promoBox}>
          <Text style={styles.promoTitle}>Have a promo code?</Text>
          <View style={styles.promoRow}>
            <TextInput
              style={styles.promoInput}
              value={promo}
              onChangeText={setPromo}
              placeholder="Enter promo code"
              placeholderTextColor={colors.slate400}
              autoCapitalize="characters"
            />
            <Pressable {...promoBtnPop.handlers} style={[styles.promoBtn, promoBtnPop.shadowStyle]} onPress={applyPromo}>
              <Text style={styles.promoBtnText}>Apply</Text>
            </Pressable>
          </View>
          {!!promoStatus && <Text style={styles.promoStatus}>{promoStatus}</Text>}
        </View>

        {/* Billing */}
        <View style={styles.plansBox}>
          <View style={styles.plansHead}>
            <Text style={styles.plansHeadText}>Billing</Text>
          </View>
          <View style={styles.plansBody}>
            <Text style={styles.billingLine}>Wingman Unlimited is $9.99/month, billed when you upgrade. Cancel anytime.</Text>
            <Text style={styles.billingLine}>💳 Payment method: Add during checkout</Text>
            <Text style={styles.billingLine}>📧 Receipts will be sent to your email</Text>
          </View>
        </View>
      </SoftCard>
    </Screen>

    {/* Cancel confirmation — an overlay tile (mockup: "What clicking Cancel subscription shows"). */}
    <Modal visible={showCancel} transparent animationType="fade" onRequestClose={() => !canceling && setShowCancel(false)}>
      <Pressable style={styles.modalScrim} onPress={() => !canceling && setShowCancel(false)}>
        <Pressable style={styles.modalCard} onPress={(e) => e.stopPropagation()}>
          <Text style={styles.modalTitle}>Cancel Wingman Unlimited?</Text>
          <Text style={styles.modalBody}>
            You'll keep unlimited AI actions through the end of your current period
            {sub?.subscription_end_at ? `, ${renewsDate}` : ''}. After that, your account returns to the Free plan and its daily AI allowance.
          </Text>
          {!!cancelStatus && <Text style={styles.cancelStatus}>{cancelStatus}</Text>}
          <View style={styles.modalActions}>
            <Pressable style={styles.modalDanger} onPress={cancelSubscription} disabled={canceling}>
              <Text style={styles.modalDangerText}>{canceling ? 'Canceling…' : 'Yes, cancel subscription'}</Text>
            </Pressable>
            <Pressable style={styles.modalKeep} onPress={() => !canceling && setShowCancel(false)} disabled={canceling}>
              <Text style={styles.modalKeepText}>Never mind, keep my plan</Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  flex1: { flex: 1 },
  card: { padding: 32, gap: 24 },
  headWrap: { alignItems: 'center', gap: 4 },
  title: { fontFamily: fonts.display, fontSize: 24, color: colors.navy },
  subTitle: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.slate500 },

  softNote: { backgroundColor: colors.amber50, borderWidth: 2, borderColor: '#FBBF24', borderRadius: radius.lg, padding: 16 },
  softNoteText: { fontFamily: fonts.bodyMed, fontSize: 13.5, lineHeight: 21, color: '#92400E' },
  meterBox: { backgroundColor: colors.slate50, borderRadius: radius.md, padding: 16, gap: 8 },
  meterTitle: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.slate900 },
  meterTrack: { height: 8, borderRadius: 999, backgroundColor: colors.slate200, overflow: 'hidden' },
  meterFill: { height: 8, borderRadius: 999, backgroundColor: colors.orange },
  meterSub: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500 },
  lapsedCard: { backgroundColor: '#FEF2F2', borderWidth: 2, borderColor: '#FCA5A5', borderRadius: radius.lg, padding: 20, gap: 8 },
  lapsedTitle: { fontFamily: fonts.display, fontSize: 20, color: '#991B1B' },
  lapsedBody: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: '#B91C1C' },
  lapsedActions: { flexDirection: 'row', marginTop: 8 },

  statusCard: { borderWidth: 2, borderColor: colors.slate200, borderRadius: radius.lg, padding: 24, gap: 16 },
  // Wrap the badge below the plan name on a narrow screen instead of clipping it; the left
  // column shrinks so a long plan name ("Wingman Unlimited") wraps rather than pushing the
  // badge off the right edge (mobile audit).
  statusRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' },
  statusRowLeft: { flexShrink: 1, minWidth: 0 },
  tinyLabel: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500, letterSpacing: 0.6, textTransform: 'uppercase' },
  planName: { fontFamily: fonts.display, fontSize: 24, color: colors.slate900, marginTop: 4 },
  badge: { borderRadius: radius.pill, paddingHorizontal: 16, paddingVertical: 8 },
  badgeText: { fontFamily: fonts.bodyBold, fontSize: 14 },
  countdown: { backgroundColor: colors.amber50, borderLeftWidth: 4, borderLeftColor: '#FBBF24', borderRadius: 4, padding: 16, gap: 4 },
  activeInfo: { backgroundColor: '#ECFDF5', borderLeftColor: '#34D399' },
  countdownTitle: { fontFamily: fonts.bodyBold, fontSize: 14, color: '#78350F' },
  countdownSub: { fontFamily: fonts.bodyMed, fontSize: 13, color: '#92400E' },

  plansBox: { borderWidth: 2, borderColor: colors.slate200, borderRadius: radius.lg, overflow: 'hidden' },
  plansHead: { backgroundColor: colors.slate50, paddingHorizontal: 24, paddingVertical: 16, borderBottomWidth: 2, borderBottomColor: colors.slate200 },
  plansHeadText: { fontFamily: fonts.display, fontSize: 18, color: colors.slate900 },
  plansBody: { padding: 24, gap: 8 },
  planRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 },
  planRowName: { fontFamily: fonts.bodyBold, fontSize: 18, color: colors.slate900 },
  planRowPrice: { fontFamily: fonts.bodyXBold, fontSize: 24, color: colors.navy, marginTop: 4 },
  planRowPer: { fontFamily: fonts.body, fontSize: 14, color: colors.slate500 },
  planRowNote: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500, marginTop: 4 },
  planDivider: { height: 2, backgroundColor: colors.slate200, marginVertical: 16 },
  featureLine: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.slate500 },

  promoBox: { backgroundColor: '#EEF2FF', borderWidth: 2, borderColor: colors.indigo200, borderRadius: radius.lg, padding: 16, gap: 8 },
  promoTitle: { fontFamily: fonts.bodyBold, fontSize: 14, color: '#312E81' },
  promoRow: { flexDirection: 'row', gap: 8 },
  promoInput: { flex: 1, borderWidth: 2, borderColor: '#A5B4FC', borderRadius: radius.sm, paddingHorizontal: 12, paddingVertical: 8, fontFamily: fonts.bodyMed, fontSize: 14, color: colors.slate900, backgroundColor: colors.white },
  promoBtn: { backgroundColor: colors.indigo, borderRadius: radius.sm, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center' },
  promoBtnText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white },
  promoStatus: { fontFamily: fonts.bodyMed, fontSize: 12, color: '#4338CA' },
  billingLine: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.slate500 },

  cancelLinkWrap: { alignSelf: 'flex-start' },
  cancelLink: { fontFamily: fonts.bodyBold, fontSize: 13, color: '#DC2626', textDecorationLine: 'underline' },

  // Cancel overlay tile.
  modalScrim: { flex: 1, backgroundColor: 'rgba(15,23,42,0.5)', alignItems: 'center', justifyContent: 'center', padding: 16 },
  modalCard: {
    backgroundColor: colors.white,
    borderRadius: radius.lg,
    width: '100%',
    maxWidth: 420,
    padding: 28,
    gap: 16,
    shadowColor: colors.slate900,
    shadowOffset: { width: 0, height: 20 },
    shadowOpacity: 0.25,
    shadowRadius: 50,
  },
  modalTitle: { fontFamily: fonts.display, fontSize: 20, color: colors.slate900 },
  modalBody: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.slate500 },
  cancelStatus: { fontFamily: fonts.bodyBold, fontSize: 13, color: '#DC2626' },
  modalActions: { gap: 10, marginTop: 4 },
  modalDanger: { borderRadius: radius.pill, paddingVertical: 12, paddingHorizontal: 20, backgroundColor: '#DC2626', alignItems: 'center' },
  modalDangerText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white },
  modalKeep: { borderRadius: radius.pill, paddingVertical: 12, paddingHorizontal: 20, backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, alignItems: 'center' },
  modalKeepText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.slate900 },
});
