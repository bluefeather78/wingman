import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { useAuth } from '@/auth/AuthContext';
import { profileHasTruncatedTail, repairProfileText, synthesizeProfile, transcriptStudentLines } from '@/lib/profile';
import { profileChatNextQuestion, profileChatStarterQuestionsFromAI, profileChatTranscript, type ChatMessage } from '@/lib/profileChat';
import { PopButton, Screen, SoftCard, Txt } from '@/ui/components';
import { colors, radius, space } from '@/ui/theme';

const callClaude = httpClient.callClaude.bind(httpClient);
const callClaudeDetailed = httpClient.callClaudeDetailed.bind(httpClient);
const PROFILE_KEY = 'student-profile';

interface StoredProfile {
  synthesized: string;
  updatedAt: string | null;
  chatRounds: number;
}

export default function Profile() {
  const router = useRouter();
  const { logout } = useAuth();
  const [profile, setProfile] = useState<StoredProfile>({ synthesized: '', updatedAt: null, chatRounds: 0 });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [starters, setStarters] = useState<string[] | null>(null);
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [chatOpen, setChatOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    httpClient.loadData<StoredProfile>(PROFILE_KEY)
      .then((p) => { if (alive && p && typeof p.synthesized === 'string') setProfile(p); })
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);

  async function persist(next: StoredProfile) {
    setProfile(next);
    await httpClient.saveData(PROFILE_KEY, next);
  }

  async function openChat() {
    setChatOpen(true);
    setHistory([]);
    setStarters(null);
    try {
      setStarters(await profileChatStarterQuestionsFromAI(callClaude, profile.synthesized, profile.chatRounds, false));
    } catch {
      setStarters(["What's something you're weirdly good at that has nothing to do with school?"]);
    }
  }
  function pickStarter(q: string) {
    setHistory([{ role: 'bot', text: q }]);
    setStarters(null);
  }
  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft('');
    const next: ChatMessage[] = [...history, { role: 'user', text }];
    setHistory(next);
    setBusy('thinking');
    try {
      const q = await profileChatNextQuestion(callClaude, profile.synthesized, next, profile.chatRounds);
      setHistory([...next, { role: 'bot', text: q || 'Tell me something else about yourself.' }]);
    } catch {
      setHistory([...next, { role: 'bot', text: "Couldn't think of a question — tell me something about yourself." }]);
    } finally {
      setBusy(null);
    }
  }
  async function finishChat() {
    if (!history.some((m) => m.role === 'user')) {
      setChatOpen(false); setHistory([]); setStarters(null); return;
    }
    setBusy('saving');
    const transcript = profileChatTranscript(history);
    try {
      const merged = await synthesizeProfile(callClaudeDetailed, profile.synthesized, transcript, true);
      await persist({ synthesized: merged, updatedAt: new Date().toISOString(), chatRounds: profile.chatRounds + 1 });
    } catch {
      const fb = transcriptStudentLines(transcript);
      const merged = fb ? (profile.synthesized ? `${profile.synthesized} ${fb}` : fb) : profile.synthesized;
      await persist({ synthesized: merged, updatedAt: new Date().toISOString(), chatRounds: profile.chatRounds + 1 });
    } finally {
      setBusy(null); setChatOpen(false); setHistory([]); setStarters(null);
    }
  }
  async function tidyUp() {
    setBusy('tidying');
    try {
      const repaired = await repairProfileText(callClaudeDetailed, profile.synthesized);
      await persist({ ...profile, synthesized: repaired, updatedAt: new Date().toISOString() });
    } catch { /* keep */ } finally { setBusy(null); }
  }
  async function handleLogout() {
    await logout();
    router.replace('/login');
  }

  if (loading) {
    return <Screen scroll={false}><View style={styles.center}><ActivityIndicator color={colors.navy} /></View></Screen>;
  }

  const truncated = profileHasTruncatedTail(profile.synthesized);

  return (
    <Screen>
      <SoftCard style={{ gap: space.md }}>
        <View style={styles.headRow}>
          <Txt variant="h2">Your Story So Far</Txt>
          <View style={styles.headBtns}>
            <PopButton label="Quick add from resume / LinkedIn" variant="secondary" small onPress={() => router.push('/(app)/profile')} />
            {!chatOpen && <PopButton label="Deepen your story" small onPress={openChat} />}
          </View>
        </View>

        {profile.synthesized ? (
          <Txt variant="body" style={styles.story}>{profile.synthesized}</Txt>
        ) : (
          <Txt variant="body" style={styles.italic}>Nothing here yet — chat with the bot to build your profile.</Txt>
        )}

        {truncated && <PopButton label={busy === 'tidying' ? 'Tidying…' : 'Tidy it up'} variant="secondary" small loading={busy === 'tidying'} onPress={tidyUp} />}
        {!chatOpen && <PopButton label="Start chatting" onPress={openChat} style={styles.startBtn} />}
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

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.md, flexWrap: 'wrap' },
  headBtns: { flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' },
  story: { color: colors.ink, fontSize: 15, lineHeight: 24 },
  italic: { fontStyle: 'italic' },
  startBtn: { alignSelf: 'flex-start' },
  starter: { backgroundColor: colors.lavender, borderRadius: radius.md, padding: space.md },
  bubble: { borderRadius: radius.md, padding: space.md, maxWidth: '92%' },
  bot: { backgroundColor: colors.lavender, alignSelf: 'flex-start' },
  userB: { backgroundColor: colors.orange, alignSelf: 'flex-end' },
  chatInput: { borderWidth: 1, borderColor: colors.borderSoft, borderRadius: radius.md, padding: space.md, fontFamily: 'PlusJakartaSans_400Regular', fontSize: 15, color: colors.ink, backgroundColor: colors.lavender, minHeight: 56, textAlignVertical: 'top' },
});
