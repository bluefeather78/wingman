import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { countProfileWords, profileHasTruncatedTail, repairProfileText, synthesizeProfile, transcriptStudentLines } from '@/lib/profile';
import { extractProfileBasics } from '@/lib/ranking';
import { profileChatNextQuestion, profileChatStarterQuestionsFromAI, profileChatTranscript, type ChatMessage } from '@/lib/profileChat';
import { PopButton, RightDrawer, Screen, SoftCard, Txt, VibeField } from '@/ui/components';
import { colors, fonts, popShadow, radius, space } from '@/ui/theme';

const callClaude = httpClient.callClaude.bind(httpClient);
const callClaudeDetailed = httpClient.callClaudeDetailed.bind(httpClient);
const callGemini = httpClient.callGemini.bind(httpClient);
const PROFILE_KEY = 'student-profile';

interface StoredProfile {
  synthesized: string;
  updatedAt: string | null;
  chatRounds: number;
}

function daysSince(iso: string): number | null {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.floor((Date.now() - t) / (24 * 3600 * 1000));
}

// Splits the synthesized profile into general paragraphs + numbered passion/research
// project lists — ported from script.js renderProfileFit.
function splitProfile(text: string) {
  const all = (text || '').split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  const passion: string[] = [];
  const research: string[] = [];
  const general: string[] = [];
  all.forEach((p) => {
    if (/^passion projects?:/i.test(p)) passion.push(p.replace(/^passion projects?:\s*/i, ''));
    else if (/^research projects?:/i.test(p)) research.push(p.replace(/^research projects?:\s*/i, ''));
    else general.push(p);
  });
  return { general, passion, research };
}

// My Vibe — ported from the live app's #page-profile: gradient CTA banner, the "Your Story
// So Far" card (updated pill, quick-add + deepen buttons, basics grid, vibe-field sections),
// and the right-hand "Deepen your story" chat drawer.
export default function Profile() {
  const router = useRouter();
  const [profile, setProfile] = useState<StoredProfile>({ synthesized: '', updatedAt: null, chatRounds: 0 });
  const [basics, setBasics] = useState<Record<string, string | null>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [starters, setStarters] = useState<string[] | null>(null);
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    httpClient.loadData<StoredProfile>(PROFILE_KEY)
      .then((p) => {
        if (!alive || !p || typeof p.synthesized !== 'string') return;
        setProfile(p);
        if (countProfileWords(p.synthesized) >= PROFILE_SUFFICIENT_LENGTH) {
          extractProfileBasics(callGemini, p.synthesized).then((b) => alive && setBasics(b)).catch(() => {});
        }
      })
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);

  async function persist(next: StoredProfile) {
    setProfile(next);
    await httpClient.saveData(PROFILE_KEY, next);
  }
  async function openDrawer() {
    setDrawerOpen(true); setHistory([]); setStarters(null);
    try { setStarters(await profileChatStarterQuestionsFromAI(callClaude, profile.synthesized, profile.chatRounds, false)); }
    catch { setStarters(["What's something you're weirdly good at that has nothing to do with school?"]); }
  }
  function pickStarter(q: string) { setHistory([{ role: 'bot', text: q }]); setStarters(null); }
  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft('');
    const next: ChatMessage[] = [...history, { role: 'user', text }];
    setHistory(next); setBusy('thinking');
    try {
      const q = await profileChatNextQuestion(callClaude, profile.synthesized, next, profile.chatRounds);
      setHistory([...next, { role: 'bot', text: q || 'Tell me something else about yourself.' }]);
    } catch {
      setHistory([...next, { role: 'bot', text: "Couldn't think of a question — tell me something about yourself." }]);
    } finally { setBusy(null); }
  }
  // Closing IS the save (matches the live app's closeStoryDrawer).
  async function closeDrawer() {
    setDrawerOpen(false);
    if (!history.some((m) => m.role === 'user')) { setHistory([]); setStarters(null); setDraft(''); return; }
    setBusy('saving');
    const transcript = profileChatTranscript(history);
    try {
      const merged = await synthesizeProfile(callClaudeDetailed, profile.synthesized, transcript, true);
      await persist({ synthesized: merged, updatedAt: new Date().toISOString(), chatRounds: profile.chatRounds + 1 });
    } catch {
      const fb = transcriptStudentLines(transcript);
      const merged = fb ? (profile.synthesized ? `${profile.synthesized} ${fb}` : fb) : profile.synthesized;
      await persist({ synthesized: merged, updatedAt: new Date().toISOString(), chatRounds: profile.chatRounds + 1 });
    } finally { setBusy(null); setHistory([]); setStarters(null); setDraft(''); }
  }
  async function tidyUp() {
    setBusy('tidying');
    try {
      const repaired = await repairProfileText(callClaudeDetailed, profile.synthesized);
      await persist({ ...profile, synthesized: repaired, updatedAt: new Date().toISOString() });
    } catch { /* keep */ } finally { setBusy(null); }
  }

  if (loading) {
    return <Screen scroll={false}><View style={styles.center}><ActivityIndicator color={colors.navy} /></View></Screen>;
  }

  const hasProfile = !!profile.synthesized;
  const isSufficient = countProfileWords(profile.synthesized) >= PROFILE_SUFFICIENT_LENGTH;
  const truncated = profileHasTruncatedTail(profile.synthesized);
  const days = profile.updatedAt ? daysSince(profile.updatedAt) : null;
  const updatedLabel = days === null ? '' : days === 0 ? 'Updated today' : days === 1 ? 'Updated yesterday' : `Updated ${days} days ago`;
  const isStale = hasProfile && days !== null && days >= 30;
  const { general, passion, research } = splitProfile(profile.synthesized);

  return (
    <Screen>
      {/* CTA banner — the product's one gradient treatment */}
      {isSufficient ? (
        <LinearGradient colors={[colors.bannerFrom, colors.bannerTo]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={styles.ctaBanner}>
          <View style={styles.flex1}>
            <Text style={styles.ctaTitle}>Your story is ready to work for you.</Text>
            <Text style={styles.ctaSub}>Head to Fresh Finds to see opportunities matched to what you just told us.</Text>
          </View>
          <PopButton label="Find your matches" variant="primaryDeep" textStyle={styles.ctaBtnText} style={styles.ctaBtn} shadowColor={colors.ink} onPress={() => router.push('/(app)/finder')} />
        </LinearGradient>
      ) : (
        <LinearGradient colors={[colors.bannerFrom, colors.bannerTo]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={styles.ctaBanner}>
          <View style={styles.flex1}>
            <Text style={styles.ctaTitle}>I don't have enough yet to match opportunities</Text>
            <Text style={styles.ctaSub}>Help me help you by building your profile.</Text>
          </View>
          <PopButton label="Deepen your story" variant="primaryDeep" textStyle={styles.ctaBtnText} style={styles.ctaBtn} shadowColor={colors.ink} onPress={openDrawer} />
        </LinearGradient>
      )}

      {/* Main profile card */}
      <SoftCard style={styles.mainCard}>
        <View style={styles.headRow}>
          <View style={styles.titleWrap}>
            <Txt variant="h2" style={{ color: colors.ink }}>Your Story So Far</Txt>
            {hasProfile && !!updatedLabel && (
              <View style={[styles.updatedPill, isStale && styles.updatedStale]}>
                <Text style={[styles.updatedText, isStale && styles.updatedStaleText]}>{updatedLabel}</Text>
              </View>
            )}
          </View>
          <View style={styles.headBtns}>
            <PopButton label="📄 Quick add from resume / LinkedIn" variant="ink" small textStyle={styles.hBtnText} shadowColor={colors.ink} onPress={() => {}} />
            <PopButton label="Deepen your story" small textStyle={styles.hBtnText} shadowColor={colors.ink} style={styles.deepenBtn} onPress={openDrawer} />
          </View>
        </View>

        {busy === 'saving' && (
          <View style={styles.synthStatus}>
            <ActivityIndicator size="small" color="#C2743A" />
            <Text style={styles.synthText}>Synthesis into profile in progress…</Text>
          </View>
        )}

        {hasProfile && (
          <View style={styles.basicsGrid}>
            <VibeField label="Grade level" style={styles.flex1}>
              <Text style={basics.grade ? styles.vibeValue : styles.vibeEmpty}>{basics.grade || 'No info'}</Text>
            </VibeField>
            <VibeField label="Home state" style={styles.flex1}>
              <Text style={basics.state ? styles.vibeValue : styles.vibeEmpty}>{basics.state || 'No info'}</Text>
            </VibeField>
            <VibeField label="Gender" style={styles.flex1}>
              <Text style={basics.gender ? styles.vibeValue : styles.vibeEmpty}>{basics.gender || 'No info'}</Text>
            </VibeField>
          </View>
        )}

        {hasProfile ? (
          <View style={{ gap: 12 }}>
            {general.length > 0 && (
              <VibeField label="Interests & experience">
                {general.map((p, i) => (
                  <Text key={i} style={[styles.prose, i < general.length - 1 && { marginBottom: 20 }]}>{p}</Text>
                ))}
              </VibeField>
            )}
            {passion.length > 0 && (
              <VibeField label="Passion projects">
                <View style={styles.list}>
                  {passion.map((p, i) => (
                    <View key={i} style={styles.listRow}>
                      <Text style={styles.listNum}>{i + 1}.</Text>
                      <Text style={styles.listText}>{p}</Text>
                    </View>
                  ))}
                </View>
              </VibeField>
            )}
            {research.length > 0 && (
              <VibeField label="Research projects">
                <View style={styles.list}>
                  {research.map((p, i) => (
                    <View key={i} style={styles.listRow}>
                      <Text style={styles.listNum}>{i + 1}.</Text>
                      <Text style={styles.listText}>{p}</Text>
                    </View>
                  ))}
                </View>
              </VibeField>
            )}
            {truncated && (
              <View style={styles.truncatedBox}>
                <Text style={styles.truncatedLabel}>THIS LOOKS CUT OFF</Text>
                <Text style={styles.truncatedText}>
                  The end of your profile was trimmed by an earlier save. Tidying up finishes or removes the incomplete bit — it won't change anything else, and it won't make anything up.
                </Text>
                <PopButton label={busy === 'tidying' ? 'Tidying…' : 'Tidy it up'} variant="ink" small loading={busy === 'tidying'} shadowColor={colors.ink} onPress={tidyUp} style={styles.selfStart} />
              </View>
            )}
          </View>
        ) : (
          <View style={{ gap: space.lg }}>
            <Text style={styles.emptyState}>Nothing here yet — chat with the bot to build your profile.</Text>
            <PopButton label="Start chatting" style={styles.selfStart} shadowColor={colors.ink} onPress={openDrawer} />
          </View>
        )}

        <View style={styles.footRow}>
          <Pressable onPress={openDrawer}>
            <Text style={styles.footLink}>or deepen your story</Text>
          </Pressable>
          {hasProfile && (
            <Pressable onPress={() => {}}>
              <Text style={styles.clearLink}>🗑️ Clear profile</Text>
            </Pressable>
          )}
        </View>
      </SoftCard>

      {/* "Deepen your story" drawer — slides in from the right like .story-drawer */}
      <RightDrawer open={drawerOpen} onClose={closeDrawer} width={440} duration={250} panelStyle={styles.drawer}>
        <>
            <View style={styles.drawerHead}>
              <View style={styles.flex1}>
                <Text style={styles.drawerTitle}>Deepen your story</Text>
                <Text style={styles.drawerSub}>Chat with the bot to add more detail. Close this and I'll fold it into your profile.</Text>
              </View>
              <Pressable onPress={closeDrawer} hitSlop={10}>
                <Text style={styles.drawerClose}>✕</Text>
              </Pressable>
            </View>
            <ScrollView style={styles.drawerBody} contentContainerStyle={styles.drawerBodyContent}>
              {!starters && history.length === 0 && <ActivityIndicator color={colors.navy} />}
              {starters?.map((q, i) => (
                <Pressable key={i} style={styles.starterBtn} onPress={() => pickStarter(q)}>
                  <Text style={styles.bubbleText}>{q}</Text>
                </Pressable>
              ))}
              {history.map((m, i) => (
                <View key={i} style={[styles.bubble, m.role === 'bot' ? styles.bubbleBot : styles.bubbleUser]}>
                  <Text style={styles.bubbleText}>{m.text}</Text>
                </View>
              ))}
              {busy === 'thinking' && <ActivityIndicator color={colors.navy} />}
            </ScrollView>
            <View style={styles.drawerFoot}>
              <TextInput
                style={styles.chatInput}
                placeholder="Type your answer..."
                placeholderTextColor={colors.slate400}
                value={draft}
                onChangeText={setDraft}
                onSubmitEditing={send}
              />
              <Pressable onPress={send} style={[styles.sendBtn, popShadow(3, colors.ink)]}>
                <Text style={styles.sendText}>Send</Text>
              </Pressable>
            </View>
        </>
      </RightDrawer>
    </Screen>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  flex1: { flex: 1, minWidth: 180 },
  selfStart: { alignSelf: 'flex-start' },

  ctaBanner: { borderRadius: radius.lg, paddingHorizontal: 28, paddingVertical: 24, flexDirection: 'row', alignItems: 'center', gap: 20, flexWrap: 'wrap' },
  ctaTitle: { fontFamily: fonts.display, fontSize: 18, lineHeight: 24, color: colors.white },
  ctaSub: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 20, color: colors.grayLighter, marginTop: 4 },
  ctaBtn: { borderWidth: 2, borderColor: colors.ink, paddingHorizontal: 26, paddingVertical: 12 },
  ctaBtnText: { fontSize: 14 },

  mainCard: { padding: 28, gap: 20 },
  headRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: space.lg, flexWrap: 'wrap' },
  titleWrap: { flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  updatedPill: { backgroundColor: colors.lime100, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  updatedText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.lime700 },
  updatedStale: { backgroundColor: '#FFE4E6' },
  updatedStaleText: { color: '#BE123C' },
  headBtns: { flexDirection: 'row', gap: 10, flexWrap: 'wrap' },
  hBtnText: { fontSize: 13, fontFamily: fonts.bodyBold },
  deepenBtn: { borderWidth: 2, borderColor: colors.ink, paddingHorizontal: 20 },

  synthStatus: { flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: 2, borderColor: '#F7D9BD', borderRadius: radius.md, backgroundColor: '#FFFAF5', paddingVertical: 10, paddingHorizontal: 14 },
  synthText: { fontFamily: fonts.bodyBold, fontSize: 13, color: '#C2743A' },

  basicsGrid: { flexDirection: 'row', gap: 12, flexWrap: 'wrap' },
  vibeValue: { fontFamily: fonts.bodyBold, fontSize: 14, lineHeight: 22, color: colors.ink },
  vibeEmpty: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: '#B8BFCD', fontStyle: 'italic' },

  // profileContent renders as .vibe-value.vibe-body: 16px / 1.8 line-height, weight 600.
  prose: { fontFamily: fonts.bodySemi, fontSize: 16, lineHeight: 28.8, color: colors.ink },
  list: { gap: 6, paddingLeft: 2 },
  listRow: { flexDirection: 'row', gap: 6 },
  listNum: { fontFamily: fonts.bodySemi, fontSize: 14, lineHeight: 21, color: colors.ink },
  listText: { fontFamily: fonts.bodySemi, fontSize: 14, lineHeight: 21, color: colors.ink, flex: 1 },

  truncatedBox: { borderWidth: 2, borderColor: '#E6D5F5', backgroundColor: '#FBF7FF', borderRadius: radius.lg, padding: 16, gap: 8 },
  truncatedLabel: { fontFamily: fonts.bodyXBold, fontSize: 10, color: '#7C5CAD', letterSpacing: 0.3 },
  truncatedText: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: '#57407D' },

  emptyState: { color: '#9AA9B8', fontStyle: 'italic', fontSize: 14, fontFamily: fonts.bodyMed },
  footRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.lg, marginTop: 8, flexWrap: 'wrap' },
  footLink: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted, textDecorationLine: 'underline' },
  clearLink: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.red },

  drawer: { borderLeftWidth: 4, borderLeftColor: colors.ink },
  drawerHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, paddingHorizontal: 20, paddingTop: 20, paddingBottom: 16, borderBottomWidth: 2, borderBottomColor: colors.lavender },
  drawerTitle: { fontFamily: fonts.display, fontSize: 18, color: colors.ink },
  drawerSub: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.muted, marginTop: 4 },
  drawerClose: { fontFamily: fonts.bodyXBold, fontSize: 20, color: colors.muted },
  drawerBody: { flex: 1, backgroundColor: colors.cream },
  drawerBodyContent: { padding: 20, gap: 10 },
  starterBtn: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: 14, paddingVertical: 10, paddingHorizontal: 14 },
  bubble: { borderWidth: 2, borderColor: colors.slate900, paddingVertical: 10, paddingHorizontal: 14, maxWidth: '85%' },
  bubbleBot: { backgroundColor: colors.white, alignSelf: 'flex-start', borderTopLeftRadius: 14, borderTopRightRadius: 14, borderBottomRightRadius: 14, borderBottomLeftRadius: 2 },
  bubbleUser: { backgroundColor: '#E0E7FF', alignSelf: 'flex-end', borderTopLeftRadius: 14, borderTopRightRadius: 14, borderBottomRightRadius: 2, borderBottomLeftRadius: 14 },
  bubbleText: { fontFamily: fonts.bodySemi, fontSize: 13, lineHeight: 18, color: colors.slate900 },
  drawerFoot: { padding: 20, paddingTop: 14, borderTopWidth: 2, borderTopColor: colors.lavender, flexDirection: 'row', gap: 8 },
  chatInput: { flex: 1, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, paddingVertical: 12, paddingHorizontal: 12, fontFamily: fonts.bodyMed, fontSize: 13, color: colors.slate900, backgroundColor: colors.white },
  sendBtn: { backgroundColor: colors.orange, borderWidth: 2, borderColor: colors.ink, borderRadius: radius.md, paddingHorizontal: 20, alignItems: 'center', justifyContent: 'center' },
  sendText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white },
});
