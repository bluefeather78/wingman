import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { httpClient } from '@/api/httpClient';
import { useAuth } from '@/auth/AuthContext';
import {
  profileHasTruncatedTail,
  repairProfileText,
  synthesizeProfile,
  transcriptStudentLines,
} from '@/lib/profile';
import {
  profileChatNextQuestion,
  profileChatStarterQuestionsFromAI,
  profileChatTranscript,
  type ChatMessage,
} from '@/lib/profileChat';

// Profile — the profile card plus an inline profile-chat flow. Behavior preserved from
// script.js / CLAUDE.md: openers are the cached-style batch, follow-ups are one live call
// per turn (never pooled), and the transcript sent to the follow-up includes the bot lines.
// Synthesis (the expensive call) runs once, on finishing the chat.
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
  const { user, logout } = useAuth();

  const [profile, setProfile] = useState<StoredProfile>({ synthesized: '', updatedAt: null, chatRounds: 0 });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  // Chat state
  const [starters, setStarters] = useState<string[] | null>(null);
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [chatOpen, setChatOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    httpClient
      .loadData<StoredProfile>(PROFILE_KEY)
      .then((p) => {
        if (alive && p && typeof p.synthesized === 'string') setProfile(p);
      })
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
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
      const qs = await profileChatStarterQuestionsFromAI(callClaude, profile.synthesized, profile.chatRounds, false);
      setStarters(qs);
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
    const nextHistory: ChatMessage[] = [...history, { role: 'user', text }];
    setHistory(nextHistory);
    setBusy('thinking');
    try {
      const q = await profileChatNextQuestion(callClaude, profile.synthesized, nextHistory, profile.chatRounds);
      setHistory([...nextHistory, { role: 'bot', text: q || 'Tell me something else about yourself.' }]);
    } catch {
      setHistory([...nextHistory, { role: 'bot', text: "Couldn't think of a question — tell me something about yourself." }]);
    } finally {
      setBusy(null);
    }
  }

  async function finishChat() {
    // Only pay for synthesis if the student actually answered something.
    const answered = history.some((m) => m.role === 'user');
    if (!answered) {
      setChatOpen(false);
      setHistory([]);
      setStarters(null);
      return;
    }
    setBusy('saving');
    const transcript = profileChatTranscript(history);
    try {
      const merged = await synthesizeProfile(callClaudeDetailed, profile.synthesized, transcript, true);
      await persist({ synthesized: merged, updatedAt: new Date().toISOString(), chatRounds: profile.chatRounds + 1 });
    } catch {
      // Fallback: append the student's own lines so nothing they wrote is lost.
      const fallback = transcriptStudentLines(transcript);
      const merged = fallback
        ? profile.synthesized
          ? `${profile.synthesized} ${fallback}`
          : fallback
        : profile.synthesized;
      await persist({ synthesized: merged, updatedAt: new Date().toISOString(), chatRounds: profile.chatRounds + 1 });
    } finally {
      setBusy(null);
      setChatOpen(false);
      setHistory([]);
      setStarters(null);
    }
  }

  async function tidyUp() {
    setBusy('tidying');
    try {
      const repaired = await repairProfileText(callClaudeDetailed, profile.synthesized);
      await persist({ ...profile, synthesized: repaired, updatedAt: new Date().toISOString() });
    } catch {
      /* leave profile as-is on failure */
    } finally {
      setBusy(null);
    }
  }

  async function handleLogout() {
    await logout();
    router.replace('/login');
  }

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator />
      </View>
    );
  }

  const truncated = profileHasTruncatedTail(profile.synthesized);

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.h1}>Your profile</Text>
      {user && (
        <Text style={styles.dim}>
          {user.firstName ? `${user.firstName} ` : ''}({user.userid})
        </Text>
      )}

      <View style={styles.card}>
        {profile.synthesized ? (
          <Text style={styles.profileText}>{profile.synthesized}</Text>
        ) : (
          <Text style={styles.dim}>No profile yet — chat below to build one.</Text>
        )}
      </View>

      {truncated && (
        <Pressable style={styles.secondaryBtn} onPress={tidyUp} disabled={!!busy}>
          <Text style={styles.secondaryText}>{busy === 'tidying' ? 'Tidying…' : 'Tidy it up'}</Text>
        </Pressable>
      )}

      {!chatOpen && (
        <Pressable style={styles.button} onPress={openChat}>
          <Text style={styles.buttonText}>
            {profile.synthesized ? 'Chat to add more' : 'Chat to build your profile'}
          </Text>
        </Pressable>
      )}

      {chatOpen && (
        <View style={styles.chat}>
          {!starters && history.length === 0 && <ActivityIndicator />}

          {starters && (
            <View style={styles.starters}>
              <Text style={styles.dim}>Pick a question to start:</Text>
              {starters.map((q, i) => (
                <Pressable key={i} style={styles.starterBtn} onPress={() => pickStarter(q)}>
                  <Text style={styles.starterText}>{q}</Text>
                </Pressable>
              ))}
            </View>
          )}

          {history.map((m, i) => (
            <View
              key={i}
              style={[styles.bubble, m.role === 'bot' ? styles.bubbleBot : styles.bubbleUser]}
            >
              <Text style={m.role === 'bot' ? styles.bubbleBotText : styles.bubbleUserText}>
                {m.text}
              </Text>
            </View>
          ))}

          {busy === 'thinking' && <ActivityIndicator />}

          {history.length > 0 && (
            <View style={styles.composer}>
              <TextInput
                style={styles.chatInput}
                placeholder="Type your answer…"
                value={draft}
                onChangeText={setDraft}
                multiline
                onSubmitEditing={send}
              />
              <Pressable style={styles.sendBtn} onPress={send} disabled={!!busy || !draft.trim()}>
                <Text style={styles.sendText}>Send</Text>
              </Pressable>
            </View>
          )}

          <Pressable style={styles.finishBtn} onPress={finishChat} disabled={busy === 'saving'}>
            <Text style={styles.finishText}>
              {busy === 'saving' ? 'Saving…' : 'Finish & save to profile'}
            </Text>
          </Pressable>
        </View>
      )}

      <Pressable style={styles.logout} onPress={handleLogout}>
        <Text style={styles.logoutText}>Log out</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 20, gap: 12, maxWidth: 720, width: '100%', alignSelf: 'center' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  h1: { fontSize: 22, fontWeight: '700' },
  dim: { color: '#889', fontSize: 13 },
  card: { borderWidth: 1, borderColor: '#e2e6ef', borderRadius: 12, padding: 14, backgroundColor: '#fafbff' },
  profileText: { fontSize: 15, lineHeight: 22, color: '#1a2540' },
  button: { backgroundColor: '#2563eb', borderRadius: 10, padding: 14, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  secondaryBtn: { borderWidth: 1, borderColor: '#b45309', borderRadius: 10, padding: 10, alignItems: 'center' },
  secondaryText: { color: '#b45309', fontWeight: '600' },
  chat: { gap: 10, borderWidth: 1, borderColor: '#e2e6ef', borderRadius: 12, padding: 14 },
  starters: { gap: 8 },
  starterBtn: { borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 10, padding: 10 },
  starterText: { color: '#1a2540', fontSize: 14 },
  bubble: { borderRadius: 12, padding: 10, maxWidth: '90%' },
  bubbleBot: { backgroundColor: '#eef0fb', alignSelf: 'flex-start' },
  bubbleUser: { backgroundColor: '#2563eb', alignSelf: 'flex-end' },
  bubbleBotText: { color: '#1a2540', fontSize: 14 },
  bubbleUserText: { color: '#fff', fontSize: 14 },
  composer: { flexDirection: 'row', gap: 8, alignItems: 'flex-end' },
  chatInput: { flex: 1, borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 10, padding: 10, fontSize: 14, maxHeight: 120 },
  sendBtn: { backgroundColor: '#2563eb', borderRadius: 10, paddingVertical: 10, paddingHorizontal: 14 },
  sendText: { color: '#fff', fontWeight: '600' },
  finishBtn: { borderWidth: 1, borderColor: '#16a34a', borderRadius: 10, padding: 10, alignItems: 'center' },
  finishText: { color: '#166534', fontWeight: '700' },
  logout: { marginTop: 8, alignSelf: 'flex-start', borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 10, paddingVertical: 10, paddingHorizontal: 16 },
  logoutText: { color: '#b91c1c', fontWeight: '600' },
});
