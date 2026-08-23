import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, TextInput, View } from 'react-native';
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
import { PopButton, PopCard, Screen, Txt } from '@/ui/components';
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
  const { user, logout } = useAuth();
  const [profile, setProfile] = useState<StoredProfile>({ synthesized: '', updatedAt: null, chatRounds: 0 });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

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
      const fallback = transcriptStudentLines(transcript);
      const merged = fallback ? (profile.synthesized ? `${profile.synthesized} ${fallback}` : fallback) : profile.synthesized;
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
      /* keep as-is */
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
      <Screen scroll={false}>
        <View style={styles.center}>
          <ActivityIndicator color={colors.navy} />
        </View>
      </Screen>
    );
  }

  const truncated = profileHasTruncatedTail(profile.synthesized);

  return (
    <Screen>
      <View style={styles.head}>
        <Txt variant="label">YOUR PROFILE</Txt>
        <Txt variant="hero">My Vibe</Txt>
        {!!user && (
          <Txt variant="small">
            {user.firstName ? `${user.firstName} ${user.lastName ?? ''} · ` : ''}
            {user.userid}
          </Txt>
        )}
      </View>

      <PopCard color={profile.synthesized ? colors.white : colors.page} style={{ gap: space.sm }}>
        {profile.synthesized ? (
          <Txt variant="body" style={styles.profileText}>
            {profile.synthesized}
          </Txt>
        ) : (
          <Txt variant="body">
            Nothing here yet. Have a quick chat below and Wingman will write your profile for you.
          </Txt>
        )}
      </PopCard>

      {truncated && (
        <PopButton
          label={busy === 'tidying' ? 'Tidying…' : 'Tidy it up'}
          variant="secondary"
          onPress={tidyUp}
          loading={busy === 'tidying'}
        />
      )}

      {!chatOpen && (
        <PopButton
          label={profile.synthesized ? 'Chat to add more' : 'Chat to build your profile'}
          variant="purple"
          onPress={openChat}
        />
      )}

      {chatOpen && (
        <PopCard style={{ gap: space.md }}>
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
              <Txt variant="body" style={{ color: m.role === 'bot' ? colors.ink : colors.white }}>
                {m.text}
              </Txt>
            </View>
          ))}

          {busy === 'thinking' && <ActivityIndicator color={colors.navy} />}

          {history.length > 0 && (
            <View style={styles.composer}>
              <TextInput
                style={styles.chatInput}
                placeholder="Type your answer…"
                placeholderTextColor={colors.muted}
                value={draft}
                onChangeText={setDraft}
                multiline
              />
              <PopButton label="Send" onPress={send} disabled={!!busy || !draft.trim()} />
            </View>
          )}

          <PopButton
            label={busy === 'saving' ? 'Saving…' : 'Finish & save'}
            variant="primary"
            onPress={finishChat}
            loading={busy === 'saving'}
            full
          />
        </PopCard>
      )}

      <PopButton label="Log out" variant="ghost" onPress={handleLogout} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  head: { gap: space.xs, marginBottom: space.xs },
  profileText: { color: colors.ink, fontSize: 15, lineHeight: 24 },
  starter: { borderWidth: 2, borderColor: colors.navy, borderRadius: radius.md, padding: space.md, backgroundColor: colors.white },
  bubble: { borderRadius: radius.md, padding: space.md, maxWidth: '92%' },
  bot: { backgroundColor: colors.page, alignSelf: 'flex-start', borderWidth: 2, borderColor: colors.hairline },
  userB: { backgroundColor: colors.purple, alignSelf: 'flex-end' },
  composer: { gap: space.sm },
  chatInput: {
    borderWidth: 2,
    borderColor: colors.navy,
    borderRadius: radius.md,
    padding: space.md,
    fontFamily: 'PlusJakartaSans_400Regular',
    fontSize: 15,
    color: colors.ink,
    backgroundColor: colors.white,
    minHeight: 56,
    textAlignVertical: 'top',
  },
});
