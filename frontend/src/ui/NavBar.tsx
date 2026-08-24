import { Ionicons } from '@expo/vector-icons';
import { usePathname, useRouter } from 'expo-router';
import { useState } from 'react';
import { Linking, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { backendUrl, httpClient } from '@/api/httpClient';
import { useAuth } from '@/auth/AuthContext';
import { Logo, RightDrawer } from './components';
import { CalendarIcon, HomeIcon, PersonIcon, SearchIcon } from './icons';
import { APP_MAX_WIDTH, colors, fonts, navShadow, popShadow, radius, space } from './theme';

// The live app's floating pill navigation: sticky, centered in the max-w-4xl column with
// 16px top inset, navy pill with a soft blue glow. Wordmark + BETA, four tabs (orange when
// active, #B7D3E8 otherwise) with the app's own inline SVG icons, and the teal 👤 badge
// opening the account drawer — a full port of #profilePanel (account + location,
// subscription, beta notice, legal, contact, about), sliding in from the right.
// Logging out lands on the LANDING page, same as the old app's showLandingPage().
type TabIcon = (props: { size?: number; color: string }) => React.JSX.Element;
const TABS: { label: string; path: string; Icon: TabIcon }[] = [
  { label: 'Home Base', path: '/(app)', Icon: HomeIcon },
  { label: 'My Vibe', path: '/(app)/profile', Icon: PersonIcon },
  { label: 'Fresh Finds', path: '/(app)/finder', Icon: SearchIcon },
  { label: 'Quest Log', path: '/(app)/tracker', Icon: CalendarIcon },
];

function isActive(pathname: string, tabPath: string): boolean {
  if (tabPath === '/(app)') return pathname === '/' || pathname === '/(app)' || pathname === '';
  const leaf = tabPath.replace('/(app)', '');
  return pathname === leaf || pathname.endsWith(leaf);
}

// updateSubscriptionUI()'s status line, ported.
function subscriptionLabel(sub: { status?: string; days_left?: number } | undefined): string | null {
  if (!sub?.status) return null;
  const days = sub.days_left ?? 0;
  if (sub.status === 'trial') return `Trial: ${days} days left`;
  if (sub.status === 'beta') return `Beta access: ${days} day${days === 1 ? '' : 's'} left`;
  if (sub.status === 'active') return 'Active: $9.99/month';
  if (sub.status === 'canceled') return 'Canceled';
  return null;
}

export function NavBar() {
  const router = useRouter();
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [location, setLocation] = useState(user?.location ?? '');
  const [locationStatus, setLocationStatus] = useState('');

  async function handleLogout() {
    setDrawerOpen(false);
    // Navigate BEFORE clearing the session: the (app) layout redirects to /login the moment
    // `user` goes null, and that guard would win the race. Old app behavior: logout lands
    // on the landing page (showLandingPage()).
    router.replace('/landing');
    await logout();
  }

  async function saveLocation() {
    const value = location.trim();
    if (!value) return;
    setLocationStatus('');
    try {
      await httpClient.saveLocation(value);
      setLocationStatus('Saved ✓');
    } catch (e) {
      setLocationStatus((e as Error).message || 'Could not save.');
    }
  }

  const subLabel = subscriptionLabel(user?.subscription);

  return (
    <SafeAreaView edges={['top']} style={styles.safe}>
      <View style={styles.column}>
        <View style={[styles.bar, navShadow()]}>
          <Pressable style={styles.brand} onPress={() => router.push('/(app)' as never)}>
            <Logo size={32} />
            <Text style={styles.word}>Wingman</Text>
            <View style={styles.beta}>
              <Text style={styles.betaText}>BETA</Text>
            </View>
          </Pressable>

          <View style={styles.tabs}>
            {TABS.map((t) => {
              const active = isActive(pathname, t.path);
              return (
                <Pressable
                  key={t.path}
                  onPress={() => router.push(t.path as never)}
                  style={[styles.tab, active && styles.tabActive]}
                >
                  <t.Icon size={18} color={active ? colors.white : '#B7D3E8'} />
                  <Text style={[styles.tabText, active && styles.tabTextActive]}>{t.label}</Text>
                </Pressable>
              );
            })}
          </View>

          <Pressable style={styles.avatar} onPress={() => { setLocation(user?.location ?? ''); setDrawerOpen(true); }}>
            <Text style={styles.avatarEmoji}>👤</Text>
          </Pressable>
        </View>
      </View>

      {/* Account drawer — #profilePanel ported section-for-section, slid in from the right. */}
      <RightDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} width={320} duration={300} panelStyle={styles.drawer}>
            <ScrollView contentContainerStyle={styles.drawerScroll} showsVerticalScrollIndicator={false}>
              <View style={styles.drawerHead}>
                <View style={styles.drawerHeadLeft}>
                  <Ionicons name="settings-outline" size={22} color={colors.slate900} />
                  <Text style={styles.drawerTitle}>Your Account</Text>
                </View>
                <Pressable onPress={() => setDrawerOpen(false)} hitSlop={10}>
                  <Text style={styles.close}>✕</Text>
                </Pressable>
              </View>

              {/* Account: name, email, log out, location */}
              <View style={styles.accountBox}>
                <View style={styles.accountRow}>
                  <View style={styles.flex1}>
                    <Text style={styles.accountName} numberOfLines={1}>
                      {[user?.firstName, user?.lastName].filter(Boolean).join(' ') || user?.userid}
                    </Text>
                    {!!user?.email && <Text style={styles.accountEmail} numberOfLines={1}>{user.email}</Text>}
                  </View>
                  <Pressable onPress={handleLogout}>
                    <Text style={styles.logout}>Log out</Text>
                  </Pressable>
                </View>
                <View style={styles.locationBlock}>
                  <Text style={styles.tinyLabel}>LOCATION</Text>
                  <View style={styles.locationRow}>
                    <TextInput
                      style={styles.locationInput}
                      value={location}
                      onChangeText={setLocation}
                      placeholder="e.g. Seattle, WA"
                      placeholderTextColor={colors.slate400}
                    />
                    <SmallBtn label="Save" onPress={saveLocation} />
                  </View>
                  {!!locationStatus && <Text style={styles.locationStatus}>{locationStatus}</Text>}
                </View>
              </View>

              {/* Subscription */}
              <View style={styles.subBox}>
                <Text style={styles.subTitle}>📋 Subscription</Text>
                {!!subLabel && <Text style={styles.subStatus}>{subLabel}</Text>}
                <SmallBtn
                  label="Manage Plan"
                  full
                  onPress={() => {
                    setDrawerOpen(false);
                    router.push('/(app)/subscription' as never);
                  }}
                />
              </View>

              {/* Beta notice */}
              <View style={styles.betaBox}>
                <Text style={styles.betaBoxTitle}>🚧 Beta product</Text>
                <Text style={styles.betaBoxText}>
                  You're using an early, actively-evolving version of Wingman. Thanks for testing it out!
                </Text>
              </View>

              {/* Legal */}
              <View style={styles.plainBox}>
                <Text style={styles.boxTitle}>Legal</Text>
                <Text style={styles.boxDesc}>The documents you agreed to when you signed up.</Text>
                <View style={styles.btnRow}>
                  <SmallBtn label="Terms of Use" onPress={() => Linking.openURL(backendUrl('/terms.html'))} />
                  <SmallBtn label="Privacy Policy" onPress={() => Linking.openURL(backendUrl('/privacy.html'))} />
                </View>
              </View>

              {/* Contact us */}
              <View style={styles.plainBox}>
                <Text style={styles.boxTitle}>Contact us</Text>
                <Text style={styles.boxDesc}>Found a bug, or have feedback? We'd love to hear it.</Text>
                <View style={styles.btnRow}>
                  <SmallBtn
                    label="✉️ Email us"
                    onPress={() => Linking.openURL('mailto:shamabildikar78@gmail.com?subject=Highschool%20Wingman%20Feedback')}
                  />
                </View>
              </View>

              {/* About us */}
              <View style={styles.plainBox}>
                <Text style={styles.boxTitle}>About us</Text>
                <Text style={styles.boxDesc}>Why we built Wingman.</Text>
                <View style={styles.btnRow}>
                  <SmallBtn label="Read our story" onPress={() => Linking.openURL(backendUrl('/about.html'))} />
                </View>
              </View>
            </ScrollView>
      </RightDrawer>
    </SafeAreaView>
  );
}

// The drawer's white pop-btn (bg-white text-xs bold px-3 py-2 rounded-lg/xl + ink shadow).
function SmallBtn({ label, onPress, full }: { label: string; onPress: () => void; full?: boolean }) {
  return (
    <Pressable onPress={onPress} style={[styles.smallBtn, popShadow(3), full && styles.smallBtnFull]}>
      <Text style={styles.smallBtnText}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  safe: {
    backgroundColor: colors.cream,
    zIndex: 50,
    ...(Platform.OS === 'web' ? ({ position: 'sticky', top: 0 } as object) : null),
  },
  column: { width: '100%', maxWidth: APP_MAX_WIDTH, alignSelf: 'center', paddingHorizontal: space.lg, paddingTop: space.lg },
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.navy,
    borderRadius: radius.pill,
    paddingVertical: 8,
    paddingLeft: 12,
    paddingRight: 8,
    gap: 8,
  },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  word: { fontFamily: fonts.display, fontSize: 18, lineHeight: 28, color: colors.white, letterSpacing: -0.3 },
  beta: { backgroundColor: colors.orange, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2 },
  betaText: { fontFamily: fonts.bodyXBold, fontSize: 9, lineHeight: 13, color: colors.white, letterSpacing: 0.5, textTransform: 'uppercase' },
  tabs: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  tab: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8, paddingHorizontal: 16, borderRadius: radius.pill },
  tabActive: { backgroundColor: colors.orange },
  tabText: { fontFamily: fonts.bodyBold, fontSize: 14, lineHeight: 20, color: '#B7D3E8' },
  tabTextActive: { color: colors.white },
  avatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.teal, alignItems: 'center', justifyContent: 'center' },
  avatarEmoji: { fontSize: 16, color: colors.white },

  drawer: { borderLeftWidth: 4, borderLeftColor: colors.slate900 },
  drawerScroll: { padding: 24, gap: 16 },
  drawerHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: 2, borderBottomColor: colors.slate900, paddingBottom: 16 },
  drawerHeadLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  drawerTitle: { fontFamily: fonts.display, fontSize: 18, lineHeight: 28, color: colors.slate900 },
  close: { fontFamily: fonts.bodyBold, fontSize: 18, color: colors.slate500 },
  flex1: { flex: 1, minWidth: 0 },

  accountBox: { backgroundColor: colors.slate50, borderWidth: 2, borderColor: colors.slate200, borderRadius: radius.lg, padding: 12, gap: 8 },
  accountRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  accountName: { fontFamily: fonts.bodyBold, fontSize: 14, lineHeight: 20, color: colors.slate900 },
  accountEmail: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: colors.slate500 },
  logout: { fontFamily: fonts.bodyBold, fontSize: 12, lineHeight: 16, color: colors.rose600 },
  locationBlock: { borderTopWidth: 1, borderTopColor: colors.slate200, paddingTop: 8, gap: 4 },
  tinyLabel: { fontFamily: fonts.bodyBold, fontSize: 10, lineHeight: 14, color: colors.slate500, letterSpacing: 0.5, textTransform: 'uppercase' },
  locationRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  locationInput: {
    flex: 1,
    borderWidth: 2,
    borderColor: '#CBD5E1',
    borderRadius: radius.sm,
    paddingHorizontal: 8,
    paddingVertical: 6,
    fontFamily: fonts.bodyMed,
    fontSize: 12,
    color: colors.slate900,
    backgroundColor: colors.white,
  },
  locationStatus: { fontFamily: fonts.bodyBold, fontSize: 10, lineHeight: 14, color: '#059669', minHeight: 14 },

  subBox: { backgroundColor: '#EFF6FF', borderWidth: 2, borderColor: '#BFDBFE', borderRadius: radius.lg, padding: 12, gap: 8 },
  subTitle: { fontFamily: fonts.bodyBold, fontSize: 14, lineHeight: 20, color: '#1E3A8A' },
  subStatus: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: '#1E40AF' },

  betaBox: { backgroundColor: colors.amber50, borderWidth: 2, borderColor: colors.amber200, borderRadius: radius.lg, padding: 12, gap: 4 },
  betaBoxTitle: { fontFamily: fonts.bodyBold, fontSize: 12, lineHeight: 16, color: '#92400E' },
  betaBoxText: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: colors.amber700 },

  plainBox: { borderWidth: 2, borderColor: colors.slate200, borderRadius: radius.lg, padding: 12, gap: 4 },
  boxTitle: { fontFamily: fonts.bodyBold, fontSize: 14, lineHeight: 20, color: colors.slate900 },
  boxDesc: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: colors.slate500, marginBottom: 4 },
  btnRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },

  smallBtn: {
    backgroundColor: colors.white,
    borderWidth: 2,
    borderColor: colors.navy,
    borderRadius: radius.md,
    paddingHorizontal: 12,
    paddingVertical: 8,
    alignItems: 'center',
  },
  smallBtnFull: { alignSelf: 'stretch' },
  smallBtnText: { fontFamily: fonts.bodyBold, fontSize: 12, lineHeight: 16, color: colors.slate900 },
});
