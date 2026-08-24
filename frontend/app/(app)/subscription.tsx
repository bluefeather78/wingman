import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { useAuth } from '@/auth/AuthContext';
import { PopButton, Screen, SoftCard, usePopInteraction } from '@/ui/components';
import { colors, fonts, popShadow, radius } from '@/ui/theme';

interface SubState {
  status?: string;
  days_left?: number;
  trial_ends_at?: string | null;
  subscription_end_at?: string | null;
  [key: string]: unknown;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '…';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '…';
  return new Date(t).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
}

// The subscription page (#page-subscription), reached from the account drawer's Manage
// Plan. Payments stay deferred: status, plans, and the promo-code flow work; Upgrade
// surfaces the backend's answer (Stripe is not configured in this environment).
export default function Subscription() {
  const { user } = useAuth();
  const [sub, setSub] = useState<SubState | null>((user?.subscription as SubState) ?? null);
  const [promo, setPromo] = useState('');
  const [promoStatus, setPromoStatus] = useState('');
  const [upgradeStatus, setUpgradeStatus] = useState('');
  const promoBtnPop = usePopInteraction(3, colors.navy, 1);

  useEffect(() => {
    let alive = true;
    httpClient.subscriptionStatus().then((s) => alive && setSub(s as SubState)).catch(() => {});
    return () => { alive = false; };
  }, []);

  const status = sub?.status ?? 'trial';
  const badge =
    status === 'active' ? { label: 'Active', bg: '#D1FAE5', fg: '#065F46' }
    : status === 'beta' ? { label: 'Beta', bg: '#DEF5B0', fg: colors.navy }
    : status === 'canceled' ? { label: 'Canceled', bg: '#FEE2E2', fg: '#991B1B' }
    : { label: 'Trial', bg: '#DEF5B0', fg: colors.navy };
  const planName = status === 'active' ? 'Pro Plan' : status === 'beta' ? 'Beta access' : 'Free Trial';
  const daysLeft = sub?.days_left ?? 0;
  const endDate = fmtDate((sub?.trial_ends_at as string) ?? (sub?.subscription_end_at as string));

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
    <Screen>
      <SoftCard style={styles.card}>
        <View style={styles.headWrap}>
          <Text style={styles.title}>Your Subscription</Text>
          <Text style={styles.subTitle}>Manage your plan and billing</Text>
        </View>

        {/* Status card */}
        <View style={styles.statusCard}>
          <View style={styles.statusRow}>
            <View>
              <Text style={styles.tinyLabel}>CURRENT PLAN</Text>
              <Text style={styles.planName}>{planName}</Text>
            </View>
            <View style={[styles.badge, { backgroundColor: badge.bg }]}>
              <Text style={[styles.badgeText, { color: badge.fg }]}>{badge.label}</Text>
            </View>
          </View>
          {(status === 'trial' || status === 'beta') && (
            <View style={styles.countdown}>
              <Text style={styles.countdownTitle}>
                {daysLeft} day{daysLeft === 1 ? '' : 's'} left {status === 'beta' ? 'of beta access' : 'in trial'}
              </Text>
              <Text style={styles.countdownSub}>Your access ends {endDate}</Text>
            </View>
          )}
          {status === 'active' && (
            <View style={[styles.countdown, styles.activeInfo]}>
              <Text style={[styles.countdownTitle, { color: '#065F46' }]}>✓ Subscription Active</Text>
              <Text style={[styles.countdownSub, { color: '#047857' }]}>Renews {endDate}</Text>
            </View>
          )}
        </View>

        {/* Plans */}
        <View style={styles.plansBox}>
          <View style={styles.plansHead}>
            <Text style={styles.plansHeadText}>Plans</Text>
          </View>
          <View style={styles.plansBody}>
            <View style={styles.planRow}>
              <View style={styles.flex1}>
                <Text style={styles.planRowName}>Free Trial</Text>
                <Text style={styles.planRowPrice}>3 days</Text>
                <Text style={styles.planRowNote}>Includes all features</Text>
              </View>
              {(status === 'trial' || status === 'beta') && (
                <View style={[styles.badge, { backgroundColor: '#DEF5B0' }]}>
                  <Text style={[styles.badgeText, { color: colors.navy }]}>Current</Text>
                </View>
              )}
            </View>
            <View style={styles.planDivider} />
            <View style={styles.planRow}>
              <View style={styles.flex1}>
                <Text style={styles.planRowName}>Pro Plan</Text>
                <Text style={styles.planRowPrice}>
                  $9.99<Text style={styles.planRowPer}>/month</Text>
                </Text>
                <Text style={styles.planRowNote}>After trial ends</Text>
              </View>
              <PopButton label="Upgrade Now" small square onPress={upgrade} />
            </View>
            <View style={{ gap: 4, marginTop: 8 }}>
              <Text style={styles.featureLine}>✓ Access to all opportunities</Text>
              <Text style={styles.featureLine}>✓ AI-powered recommendations</Text>
              <Text style={styles.featureLine}>✓ Priority support</Text>
              <Text style={styles.featureLine}>✓ Deadline reminders</Text>
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
            <Text style={styles.billingLine}>You'll be billed $9.99/month after your trial ends.</Text>
            <Text style={styles.billingLine}>💳 Payment method: Add during checkout</Text>
            <Text style={styles.billingLine}>📧 Receipts will be sent to your email</Text>
          </View>
        </View>
      </SoftCard>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex1: { flex: 1 },
  card: { padding: 32, gap: 24 },
  headWrap: { alignItems: 'center', gap: 4 },
  title: { fontFamily: fonts.display, fontSize: 24, color: colors.navy },
  subTitle: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.slate500 },

  statusCard: { borderWidth: 2, borderColor: colors.slate200, borderRadius: radius.lg, padding: 24, gap: 16 },
  statusRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
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
});
