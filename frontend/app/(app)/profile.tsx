import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { useAuth } from '@/auth/AuthContext';
import { PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { countProfileWords, profileHasTruncatedTail, repairProfileText, synthesizeProfile, transcriptStudentLines } from '@/lib/profile';
import { extractProfileBasics } from '@/lib/ranking';
import { profileChatNextQuestion, profileChatStarterQuestionsFromAI, profileChatTranscript, type ChatMessage } from '@/lib/profileChat';
import { Badge, PopButton, Screen, SoftCard, Txt } from '@/ui/components';
import { colors, radius, space } from '@/ui/theme';

const callClaude = httpClient.callClaude.bind(httpClient);
const callClaudeDetailed = httpClient.callClaudeDetailed.bind(httpClient);
const callGemini = httpClient.callGemini.bind(httpClient);
const PROFILE_KEY = 'student-profile';

interface StoredProfile {
  synthesized: string;
  updatedAt: string | null;
  chatRounds: number;
}

function relativeUpdated(iso: string | null): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const days = Math.floor((Date.now() - t) / (24 * 3600 * 1000));
  if (days <= 0) return 'Updated today';
  if (days === 1) return 'Updated yesterday';
  if (days < 30) return `Updated ${days} days ago`;
  return 'Updated a while ago';
}

export default function Profile() {
  const router = useRouter();
  const { logout } = useAuth();
  const [profile, setProfile] = useState<StoredProfile>({ synthesized: '', updatedAt: null, chatRounds: 0 });
  const [basics, setBasics] = useState<Record<string, string | null>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [starters, setStarters] = useState<string[] | null>(null);
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [chatOpen, setChatOpen] = useState(false);

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
  async function openChat() {
    setChatOpen(true); setHistory([]); setStarters(null);
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
  async function finishChat() {
    if (!history.some((m) => m.role === 'user')) { setChatOpen(false); setHistory([]); setStarters(null); return; }
    setBusy('saving');
    const transcript = profileChatTranscript(history);
    try {
      const merged = await synthesizeProfile(callClaudeDetailed, profile.synthesized, transcript, true);
      await persist({ synthesized: merged, updatedAt: new Date().toISOString(), chatRounds: profile.chatRounds + 1 });
    } catch {
      const fb = transcriptStudentLines(transcript);
      const merged = fb ? (profile.synthesized ? `${profile.synthesized} ${fb}` : fb) : profile.synthesized;
      await persist({ synthesized: merged, updatedAt: new Date().toISOString(), chatRounds: profile.chatRounds + 1 });
    } finally { setBusy(null); setChatOpen(false); setHistory([]); setStarters(null); }
  }
  async function tidyUp() {
    setBusy('tidying');
    try {
      const repaired = await repairProfileText(callClaudeDetailed, profile.synthesized);
      await persist({ ...profile, synthesized: repaired, updatedAt: new Date().toISOString() });
    } catch { /* keep */ } finally { setBusy(null); }
  }
  async function handleLogout() { await logout(); router.replace('/login'); }

  if (loading) {
    return <Screen scroll={false}><View style={styles.center}><ActivityIndicator color={colors.navy} /></View></Screen>;
  }

  const hasProfile = countProfileWords(profile.synthesized) >= PROFILE_SUFFICIENT_LENGTH;
  const truncated = profileHasTruncatedTail(profile.synthesized);

  return (
    <Screen>
      {hasProfile && (
        <SoftCard color={colors.navy} style={styles.banner}>
          <View style={styles.flex1}>
            <Txt variant="h2" style={styles.onDark}>Your story is ready to work for you.</Txt>
            <Txt variant="body" style={styles.onDarkSoft}>Head to Fresh Finds to see opportunities matched to what you just told us.</Txt>
          </View>
          <PopButton label="Find your matches" onPress={() => router.push('/(app)/finder')} />
        </SoftCard>
      )}

      <SoftCard style={{ gap: space.md }}>
        <View style={styles.headRow}>
          <View style={styles.titleWrap}>
            <Txt variant="h2">Your Story So Far</Txt>
            {hasProfile && !!profile.updatedAt && <Badge label={relativeUpdated(profile.updatedAt).toUpperCase()} bg={colors.greenSoft} fg={colors.green} />}
          </View>
          <View style={styles.headBtns}>
            <PopButton label="Quick add from resume / LinkedIn" variant="secondary" small onPress={() => router.push('/(app)/profile')} />
            {!chatOpen && <PopButton label="Deepen your story" small onPress={openChat} />}
          </View>
        </View>

        {hasProfile && (
          <View style={styles.basicsRow}>
            <BasicTile label="GRADE LEVEL" value={basics.grade} />
            <BasicTile label="HOME STATE" value={basics.state} />
            <BasicTile label="GENDER" value={basics.gender} />
          </View>
        )}

        {profile.synthesized ? (
          <View style={{ gap: 6 }}>
            <Txt variant="label">INTERESTS & EXPERIENCE</Txt>
            <Txt variant="body" style={styles.story}>{profile.synthesized}</Txt>
          </View>
        ) : (
          <Txt variant="body" style={styles.italic}>Nothing here yet — chat with the bot to build your profile.</Txt>
        )}

        {truncated && <PopButton label={busy === 'tidying' ? 'Tidying…' : 'Tidy it up'} variant="secondary" small loading={busy === 'tidying'} onPress={tidyUp} />}
        {!chatOpen && !hasProfile && <PopButton label="Start chatting" onPress={openChat} style={styles.selfStart} />}
      </SoftCard>

      {chatOpen && (
        <SoftCard style={{ gap: space.md }}>
          {!starters && history.length === 0 && <ActivityIndicator color={colors.navy} />}
          {starters && (
            <View style={{ gap: space.sm }}>
              <Txt variant="label">PICK A QUESTION TO START</Txt>
              {starters.map((q, i) => (
                <Pressable key={i} style={styles.starter} onPress={() => pickStarter(q)}>
                  <Txt variant="bodyStrong">{q}</Txt>
                </Pressable>
              ))}
            </View>
          )}
          {history.map((m, i) => (
            <View key={i} style={[styles.bubble, m.role === 'bot' ? styles.bot : styles.userB]}>
              <Txt variant="body" style={{ color: m.role === 'bot' ? colors.ink : colors.white }}>{m.text}</Txt>
            </View>
          ))}
          {busy === 'thinking' && <ActivityIndicator color={colors.navy} />}
          {history.length > 0 && (
            <View style={{ gap: space.sm }}>
              <TextInput style={styles.chatInput} placeholder="Type your answer…" placeholderTextColor={colors.muted} value={draft} onChangeText={setDraft} multiline />
              <PopButton label="Send" onPress={send} disabled={!!busy || !draft.trim()} />
            </View>
          )}
          <PopButton label={busy === 'saving' ? 'Saving…' : 'Finish & save'} variant="secondary" loading={busy === 'saving'} onPress={finishChat} full />
        </SoftCard>
      )}

      <PopButton label="Log out" variant="ghost" onPress={handleLogout} />
    </Screen>
  );
}

function BasicTile({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <View style={styles.tile}>
      <Txt variant="label">{label}</Txt>
      <Txt variant="bodyStrong" style={value ? undefined : styles.noInfo}>{value || 'No info'}</Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  banner: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  onDark: { color: colors.white },
  onDarkSoft: { color: '#D6E4F5' },
  flex1: { flex: 1 },
  headRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: space.md, flexWrap: 'wrap' },
  titleWrap: { flexDirection: 'row', alignItems: 'center', gap: space.sm, flexWrap: 'wrap' },
  headBtns: { flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' },
  basicsRow: { flexDirection: 'row', gap: space.md, flexWrap: 'wrap' },
  tile: { flexGrow: 1, flexBasis: 140, borderWidth: 1, borderColor: colors.borderSoft, borderRadius: radius.md, padding: space.md, gap: 4, backgroundColor: colors.card },
  noInfo: { color: colors.muted, fontStyle: 'italic' },
  story: { color: colors.ink, fontSize: 15, lineHeight: 24 },
  italic: { fontStyle: 'italic' },
  selfStart: { alignSelf: 'flex-start' },
  starter: { backgroundColor: colors.lavender, borderRadius: radius.md, padding: space.md },
  bubble: { borderRadius: radius.md, padding: space.md, maxWidth: '92%' },
  bot: { backgroundColor: colors.lavender, alignSelf: 'flex-start' },
  userB: { backgroundColor: colors.orange, alignSelf: 'flex-end' },
  chatInput: { borderWidth: 1, borderColor: colors.borderSoft, borderRadius: radius.md, padding: space.md, fontFamily: 'PlusJakartaSans_400Regular', fontSize: 15, color: colors.ink, backgroundColor: colors.lavender, minHeight: 56, textAlignVertical: 'top' },
});
