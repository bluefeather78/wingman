import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { countProfileWords, profileHasTruncatedTail, repairProfileText, synthesizeProfile, transcriptStudentLines } from '@/lib/profile';
import { diffNewProfileSentences, PROFILE_HIGHLIGHT_MS, profileSentenceKey, splitProfileSentences } from '@/lib/profileHighlight';
import { beginProfileWrite, endProfileWrite } from '@/lib/profileWrites';
import {
  getProfileDerived,
  refreshProfileDerived,
  type BasicsSlot,
  type ModelCalls,
  type ProfileRecord,
  type ProfileStore,
  type StarterPoolSlot,
} from '@/lib/profileDerived';
import {
  drawStarterWindow,
  FALLBACK_STARTER_QUESTIONS,
  profileChatNextQuestion,
  profileChatStarterQuestionsFromAI,
  profileChatTranscript,
  type ChatMessage,
} from '@/lib/profileChat';
import { PopButton, RightDrawer, Screen, SoftCard, Txt, usePopInteraction, VibeField } from '@/ui/components';
import { colors, fonts, popShadow, radius, space } from '@/ui/theme';

const callClaude = httpClient.callClaude.bind(httpClient);
const callClaudeDetailed = httpClient.callClaudeDetailed.bind(httpClient);
const callGemini = httpClient.callGemini.bind(httpClient);
const PROFILE_KEY = 'student-profile';

// Shared with Fresh Finds: the profile-derived slots (subjects+grade, filter tags, basics,
// chat openers) are computed once per profile "version" and stored on this same record.
const modelCalls: ModelCalls = {
  gemini: callGemini,
  claude: callClaude,
};
const profileStore: ProfileStore = {
  load: () => httpClient.loadData<ProfileRecord>(PROFILE_KEY),
  save: (record) => httpClient.saveData(PROFILE_KEY, record),
};

interface StoredProfile {
  synthesized: string;
  updatedAt: string | null;
  chatRounds: number;
  // Other slots stored on the same record and owned by other screens (filterTags backs
  // Fresh Finds' "Your Profile" filter; filterValues/starterPool/basics are the retired
  // SPA's derived caches). This screen must carry them through untouched — the save
  // REPLACES the whole record, so anything not spread forward is destroyed.
  [slot: string]: unknown;
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

// Web Speech API feature detection (web only; each control hides independently, like the
// live app's initProfileChatVoiceUI).
type SpeechRecognitionLike = {
  lang: string; interimResults: boolean; maxAlternatives: number;
  onresult: ((e: { results: { 0: { transcript: string } }[] }) => void) | null;
  onend: (() => void) | null; onerror: ((e: unknown) => void) | null;
  start: () => void; stop: () => void;
};
const SpeechRecognitionCtor: (new () => SpeechRecognitionLike) | null =
  Platform.OS === 'web'
    ? (((globalThis as Record<string, unknown>).SpeechRecognition ??
        (globalThis as Record<string, unknown>).webkitSpeechRecognition) as (new () => SpeechRecognitionLike) | null) ?? null
    : null;
const ttsAvailable = Platform.OS === 'web' && typeof globalThis !== 'undefined' && 'speechSynthesis' in globalThis;

// My Vibe — ported from the live app's #page-profile: gradient CTA banner, the "Your Story
// So Far" card (updated pill, quick-add + deepen buttons, basics grid, vibe-field sections
// with 5s new-text highlights + auto-scroll), the resume/LinkedIn quick-add modal, and the
// right-hand "Deepen your story" chat drawer (starters + regenerate, mic, spoken questions).
export default function Profile() {
  const router = useRouter();
  const [profile, setProfile] = useState<StoredProfile>({ synthesized: '', updatedAt: null, chatRounds: 0 });
  const [basics, setBasics] = useState<Record<string, string | null>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [starters, setStarters] = useState<string[] | null>(null);
  const [startersLoading, setStartersLoading] = useState(false);
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [clearArmed, setClearArmed] = useState(false);
  const clearArmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const voiceBtnPop = usePopInteraction(3, colors.slate900, 1);
  const micBtnPop = usePopInteraction(3, colors.slate900, 1);
  const sendBtnPop = usePopInteraction(3, colors.ink, 1);
  const resumeSubmitPop = usePopInteraction(3, colors.slate900, 1);
  const linkedinSubmitPop = usePopInteraction(3, colors.slate900, 1);

  // Import modal
  const [importOpen, setImportOpen] = useState(false);
  const [importTab, setImportTab] = useState<'resume' | 'linkedin'>('resume');
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [resumeStatus, setResumeStatus] = useState('');
  const [linkedinText, setLinkedinText] = useState('');
  const [linkedinStatus, setLinkedinStatus] = useState('');

  // New-text highlight + auto-scroll
  const [highlightSet, setHighlightSet] = useState<Set<string> | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const cardY = useRef(0);
  const sectionsY = useRef(0);
  const fieldY = useRef<Record<string, number>>({});

  // Voice
  const [voiceOn, setVoiceOn] = useState(false);
  const [listening, setListening] = useState(false);
  const recognition = useRef<SpeechRecognitionLike | null>(null);
  const voiceOnRef = useRef(false);

  useEffect(() => {
    let alive = true;
    httpClient.loadData<StoredProfile>(PROFILE_KEY)
      .then((p) => {
        if (!alive || !p || typeof p.synthesized !== 'string') return;
        setProfile(p);
        if (countProfileWords(p.synthesized) >= PROFILE_SUFFICIENT_LENGTH) {
          // Through the slot cache: this used to fire a fresh extractProfileBasics call on
          // EVERY visit to My Vibe, for a profile that had not changed.
          getProfileDerived(profileStore, modelCalls, 'basics', p)
            .then((slot) => alive && setBasics((slot as BasicsSlot).fields || {}))
            .catch(() => {});
        }
      })
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);

  useEffect(() => () => {
    if (clearArmTimer.current) clearTimeout(clearArmTimer.current);
  }, []);

  async function persist(next: StoredProfile) {
    setProfile(next);
    await httpClient.saveData(PROFILE_KEY, next);
  }

  // Highlight what a merge added, fade it after PROFILE_HIGHLIGHT_MS, and scroll the page
  // to the first section that contains a new sentence (flagNewProfileText behavior).
  function showMergeHighlights(before: string, after: string) {
    const added = diffNewProfileSentences(before, after);
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    if (!added.size) {
      setHighlightSet(null);
      return;
    }
    setHighlightSet(added);
    highlightTimer.current = setTimeout(() => setHighlightSet(null), PROFILE_HIGHLIGHT_MS);
    // Scroll after the re-render has laid the sections out.
    setTimeout(() => {
      const { general, passion, research } = splitProfile(after);
      const contains = (paras: string[]) =>
        paras.some((p) => splitProfileSentences(p).some((s) => added.has(profileSentenceKey(s))));
      let key: string = 'interests';
      if (!contains(general)) {
        if (contains(passion)) key = 'passion';
        else if (contains(research)) key = 'research';
      }
      const y = cardY.current + sectionsY.current + (fieldY.current[key] ?? 0);
      scrollRef.current?.scrollTo({ y: Math.max(0, y - 90), animated: true });
    }, 350);
  }

  // The one merge path every entry point uses (chat close, resume, LinkedIn) — old
  // mergeIntoProfile. Returns true when the profile actually changed.
  async function mergeIntoProfile(newText: string, isTranscript: boolean): Promise<boolean> {
    const before = profile.synthesized;
    setBusy('saving');
    // Tell the other screens the profile is mid-rewrite. Fresh Finds waits on this rather
    // than reading the pre-merge text and showing a list it will have to swap out seconds
    // later. Paired in the finally so a failed synthesis can't leave readers blocked.
    beginProfileWrite();
    try {
      let merged: string;
      try {
        merged = await synthesizeProfile(callClaudeDetailed, before, newText, isTranscript);
      } catch {
        const fb = isTranscript ? transcriptStudentLines(newText) : newText;
        merged = fb ? (before ? `${before} ${fb}` : fb) : before;
      }
      // Spread the existing record. /api/data/save REPLACES the whole value at a key, so
      // writing a bare literal here destroyed every other slot stored alongside the profile
      // — including `filterTags`, which backs Fresh Finds' "Your Profile" filter. The facet
      // survived until a student edited their profile once, then vanished for good.
      await persist({
        ...profile,
        synthesized: merged,
        updatedAt: new Date().toISOString(),
        chatRounds: profile.chatRounds + (isTranscript ? 1 : 0),
      });
      showMergeHighlights(before, merged);
      // The profile changed, so every derived slot is now stale. Refresh them all in the
      // background (fire-and-forget, failures are not user-facing) so the next search, the
      // tag facet and the chat drawer are served warm instead of paying on the critical
      // path. Basics is read back here because this screen displays it.
      const nextRecord: ProfileRecord = { ...profile, synthesized: merged };
      refreshProfileDerived(profileStore, modelCalls, nextRecord);
      getProfileDerived(profileStore, modelCalls, 'basics', nextRecord)
        .then((slot) => setBasics((slot as BasicsSlot).fields || {}))
        .catch(() => {});
      return merged !== before;
    } finally {
      setBusy(null);
      endProfileWrite();
    }
  }

  // Double-click-armed since it wipes the whole profile at once — ported from script.js's
  // clearProfile(). First tap arms a 3s confirm window; a second tap inside it clears.
  function clearProfile() {
    if (!clearArmed) {
      setClearArmed(true);
      if (clearArmTimer.current) clearTimeout(clearArmTimer.current);
      clearArmTimer.current = setTimeout(() => setClearArmed(false), 3000);
      return;
    }
    if (clearArmTimer.current) clearTimeout(clearArmTimer.current);
    setClearArmed(false);
    void (async () => {
      await persist({ synthesized: '', updatedAt: null, chatRounds: 0 });
      setBasics({});
      setHistory([]);
      setStarters(null);
      // Only refresh the chat (and re-spend an API call on fresh starters) if the drawer is
      // already open — clearing the profile shouldn't be what opens it.
      if (drawerOpen) void loadStarters(false);
    })();
  }

  // Openers come from the cached 10-question pool (PROFILE_DERIVED_SLOTS.starterPool),
  // serving a rotating window of 3 per open — three clean trios, then the fourth wraps and
  // reuses two (10 is not a multiple of 3), and an unchanged profile never re-pays for any
  // of them. They depend on the profile text and nothing else,
  // which is what makes them safe to cache; follow-ups are the opposite and stay live.
  // refreshProfileDerived warms the pool after every merge, and opening the drawer before
  // that lands shares the SAME in-flight call rather than starting a second one.
  //
  // The slot is read through getProfileDerived with no record argument on purpose: the local
  // `profile` state is written by persist() and never carries the slots the background
  // refresh writes to the server, so passing it would read as a cache miss and re-pay for a
  // pool that already exists.
  //
  // Regenerate stays a LIVE 3-question call — that button is the student explicitly saying
  // "these don't suit me", which is the one place paying is clearly warranted, and its prompt
  // carries a breadth directive the pool's does not. It deliberately does not touch the pool.
  async function loadStarters(regenerate: boolean) {
    setStartersLoading(true);
    try {
      if (regenerate) {
        setStarters(await profileChatStarterQuestionsFromAI(callClaude, profile.synthesized, profile.chatRounds, true));
      } else {
        const slot = (await getProfileDerived(profileStore, modelCalls, 'starterPool')) as StarterPoolSlot;
        setStarters(drawStarterWindow(slot.questions));
      }
    } catch {
      // Only on the first open: a failed regenerate leaves the trio already on screen, which
      // is better than replacing questions built from the profile with generic ones.
      if (!regenerate) setStarters(FALLBACK_STARTER_QUESTIONS.slice());
    } finally {
      setStartersLoading(false);
    }
  }
  async function openDrawer() {
    setDrawerOpen(true); setHistory([]); setStarters(null);
    void loadStarters(false);
  }
  function pickStarter(q: string) {
    setHistory([{ role: 'bot', text: q }]);
    setStarters(null);
    speak(q);
  }
  function speak(text: string) {
    if (!voiceOnRef.current || !ttsAvailable || !text) return;
    const synth = (globalThis as { speechSynthesis?: { cancel: () => void; speak: (u: unknown) => void } }).speechSynthesis;
    synth?.cancel();
    const Utter = (globalThis as Record<string, unknown>).SpeechSynthesisUtterance as new (t: string) => unknown;
    if (Utter) synth?.speak(new Utter(text));
  }
  function toggleVoiceOutput() {
    const next = !voiceOn;
    setVoiceOn(next);
    voiceOnRef.current = next;
    if (next) {
      const lastBot = [...history].reverse().find((m) => m.role === 'bot');
      if (lastBot) speak(lastBot.text);
    } else if (ttsAvailable) {
      (globalThis as { speechSynthesis?: { cancel: () => void } }).speechSynthesis?.cancel();
    }
  }
  function toggleVoiceInput() {
    if (!SpeechRecognitionCtor) return;
    if (listening) {
      recognition.current?.stop();
      return;
    }
    if (!recognition.current) {
      const rec = new SpeechRecognitionCtor();
      rec.lang = 'en-US';
      rec.interimResults = true;
      rec.maxAlternatives = 1;
      rec.onresult = (e) => {
        let transcript = '';
        for (let i = 0; i < (e.results as unknown as { length: number }).length; i++) transcript += e.results[i][0].transcript;
        setDraft(transcript);
      };
      rec.onend = () => {
        setListening(false);
        setDraft((current) => {
          if (current.trim()) void sendText(current.trim());
          return current;
        });
      };
      rec.onerror = () => setListening(false);
      recognition.current = rec;
    }
    try {
      recognition.current.start();
      setListening(true);
    } catch { /* already started */ }
  }

  async function sendText(text: string) {
    if (!text || busy) return;
    setDraft('');
    setHistory((prev) => {
      const next: ChatMessage[] = [...prev, { role: 'user', text }];
      setBusy('thinking');
      profileChatNextQuestion(callClaude, profile.synthesized, next, profile.chatRounds)
        .then((q) => {
          const bot = q || 'Tell me something else about yourself.';
          setHistory([...next, { role: 'bot', text: bot }]);
          speak(bot);
        })
        .catch(() => setHistory([...next, { role: 'bot', text: "Couldn't think of a question — tell me something about yourself." }]))
        .finally(() => setBusy(null));
      return next;
    });
  }
  async function send() {
    await sendText(draft.trim());
  }

  // Closing IS the save (matches the live app's closeStoryDrawer).
  async function closeDrawer() {
    setDrawerOpen(false);
    if (recognition.current && listening) recognition.current.stop();
    if (!history.some((m) => m.role === 'user')) { setHistory([]); setStarters(null); setDraft(''); return; }
    const transcript = profileChatTranscript(history);
    setHistory([]); setStarters(null); setDraft('');
    await mergeIntoProfile(transcript, true);
  }

  async function tidyUp() {
    setBusy('tidying');
    beginProfileWrite();
    try {
      const repaired = await repairProfileText(callClaudeDetailed, profile.synthesized);
      await persist({ ...profile, synthesized: repaired, updatedAt: new Date().toISOString() });
      // A repair is a synthesis pass like any other — it rewrites the profile text, so every
      // derived slot is now computed from text that no longer exists. Warm them in the
      // background exactly as a merge does, or the tags/subjects/basics would go on
      // describing the damaged version until some later reader happened to recompute them.
      const nextRecord: ProfileRecord = { ...profile, synthesized: repaired };
      refreshProfileDerived(profileStore, modelCalls, nextRecord);
      getProfileDerived(profileStore, modelCalls, 'basics', nextRecord)
        .then((slot) => setBasics((slot as BasicsSlot).fields || {}))
        .catch(() => {});
    } catch { /* keep */ } finally { setBusy(null); endProfileWrite(); }
  }

  // ---------- Resume / LinkedIn import ----------
  function pickResumeFile() {
    if (Platform.OS !== 'web') {
      setResumeStatus('Resume upload is available on the web app.');
      return;
    }
    const doc = (globalThis as { document?: Document }).document;
    if (!doc) return;
    const input = doc.createElement('input');
    input.type = 'file';
    input.accept = '.pdf,.docx';
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) return;
      const validTypes = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
      if (!validTypes.includes(file.type)) {
        setResumeStatus('❌ Please upload a PDF or Word document');
        setResumeFile(null);
        return;
      }
      if (file.size > 5 * 1024 * 1024) {
        setResumeStatus('❌ File is too large (max 5MB)');
        setResumeFile(null);
        return;
      }
      setResumeFile(file);
      setResumeStatus(`✓ ${file.name} ready to extract`);
    };
    input.click();
  }
  async function submitResume() {
    if (!resumeFile) return;
    setResumeStatus('Extracting from your resume…');
    try {
      const extracted = await httpClient.extractFromResume(resumeFile, resumeFile.name);
      if (!extracted.trim()) {
        setResumeStatus('⚠️ No relevant information found in resume');
        return;
      }
      setImportOpen(false);
      setResumeFile(null);
      setResumeStatus('');
      await mergeIntoProfile(extracted, false);
    } catch (e) {
      setResumeStatus(`❌ ${(e as Error).message || 'Extraction failed'}`);
    }
  }
  async function submitLinkedIn() {
    const text = linkedinText.trim();
    if (!text) {
      setLinkedinStatus('⚠️ Please paste your LinkedIn profile text');
      return;
    }
    setLinkedinStatus('Extracting from LinkedIn…');
    try {
      const extracted = await httpClient.extractFromLinkedIn(text);
      if (!extracted.trim()) {
        setLinkedinStatus('⚠️ No relevant information found in LinkedIn profile');
        return;
      }
      setImportOpen(false);
      setLinkedinText('');
      setLinkedinStatus('');
      await mergeIntoProfile(extracted, false);
    } catch (e) {
      setLinkedinStatus(`❌ ${(e as Error).message || 'Extraction failed'}`);
    }
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

  // A paragraph with any newly-merged sentences rendered highlighted (profileTextHTML).
  const renderProse = (p: string, style: object, extra?: object) => {
    if (!highlightSet || !highlightSet.size) return <Text style={[style, extra]}>{p}</Text>;
    const parts = splitProfileSentences(p);
    return (
      <Text style={[style, extra]}>
        {parts.map((s, i) => (
          <Text key={i} style={highlightSet.has(profileSentenceKey(s)) ? styles.newText : undefined}>
            {s}
            {i < parts.length - 1 ? ' ' : ''}
          </Text>
        ))}
      </Text>
    );
  };

  return (
    <Screen scrollRef={scrollRef}>
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
          <View style={styles.ctaBtnCol}>
            <PopButton label="Deepen your story" variant="primaryDeep" textStyle={styles.ctaBtnText} style={styles.ctaBtn} shadowColor={colors.ink} onPress={openDrawer} />
            <Pressable onPress={() => router.push('/(app)/finder')}>
              <Text style={styles.ctaSecondaryLink}>or browse opportunities</Text>
            </Pressable>
          </View>
        </LinearGradient>
      )}

      {/* Main profile card */}
      <SoftCard style={styles.mainCard} hoverTint onLayout={(e) => { cardY.current = e.nativeEvent.layout.y; }}>
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
            <PopButton label="📄 Quick add from resume / LinkedIn" variant="ink" small textStyle={styles.hBtnText} shadowColor={colors.ink} onPress={() => setImportOpen(true)} />
            {/* With no profile yet there is nothing to deepen — the card's own "Start
                chatting" button is the CTA, so both "deepen" affordances stay hidden. */}
            {hasProfile && (
              <PopButton label="Deepen your story" small textStyle={styles.hBtnText} shadowColor={colors.ink} style={styles.deepenBtn} onPress={openDrawer} />
            )}
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
          <View style={{ gap: 12 }} onLayout={(e) => { sectionsY.current = e.nativeEvent.layout.y; }}>
            {general.length > 0 && (
              <View onLayout={(e) => { fieldY.current.interests = e.nativeEvent.layout.y; }}>
                <VibeField label="Interests & experience">
                  {general.map((p, i) => (
                    <View key={i} style={i < general.length - 1 ? { marginBottom: 20 } : undefined}>
                      {renderProse(p, styles.prose)}
                    </View>
                  ))}
                </VibeField>
              </View>
            )}
            {passion.length > 0 && (
              <View onLayout={(e) => { fieldY.current.passion = e.nativeEvent.layout.y; }}>
                <VibeField label="Passion projects">
                  <View style={styles.list}>
                    {passion.map((p, i) => (
                      <View key={i} style={styles.listRow}>
                        <Text style={styles.listNum}>{i + 1}.</Text>
                        <View style={styles.flex1}>{renderProse(p, styles.listText)}</View>
                      </View>
                    ))}
                  </View>
                </VibeField>
              </View>
            )}
            {research.length > 0 && (
              <View onLayout={(e) => { fieldY.current.research = e.nativeEvent.layout.y; }}>
                <VibeField label="Research projects">
                  <View style={styles.list}>
                    {research.map((p, i) => (
                      <View key={i} style={styles.listRow}>
                        <Text style={styles.listNum}>{i + 1}.</Text>
                        <View style={styles.flex1}>{renderProse(p, styles.listText)}</View>
                      </View>
                    ))}
                  </View>
                </VibeField>
              </View>
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
          {hasProfile && (
            <Pressable onPress={openDrawer}>
              <Text style={styles.footLink}>or deepen your story</Text>
            </Pressable>
          )}
          {hasProfile && (
            <Pressable onPress={clearProfile}>
              <Text style={[styles.clearLink, clearArmed && styles.clearLinkArmed]}>
                {clearArmed ? 'Click again to confirm' : '🗑️ Clear profile'}
              </Text>
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
            <View style={styles.drawerHeadBtns}>
              {ttsAvailable && (
                <Pressable {...voiceBtnPop.handlers} style={[styles.voiceBtn, voiceBtnPop.shadowStyle, voiceOn && styles.voiceBtnOn]} onPress={toggleVoiceOutput}>
                  <Text style={styles.voiceBtnText}>{voiceOn ? '🔊' : '🔇'}</Text>
                </Pressable>
              )}
              <Pressable onPress={closeDrawer} hitSlop={10}>
                <Text style={styles.drawerClose}>✕</Text>
              </Pressable>
            </View>
          </View>
          <ScrollView style={styles.drawerBody} contentContainerStyle={styles.drawerBodyContent}>
            {!starters && history.length === 0 && !startersLoading && <Text style={styles.emptyState}>Cooking up a few conversation starters…</Text>}
            {startersLoading && !starters && <ActivityIndicator color={colors.navy} />}
            {starters && history.length === 0 && (
              <>
                <View style={styles.starterHead}>
                  <Text style={styles.starterHeadText}>Pick a place to start:</Text>
                  <Pressable onPress={() => !startersLoading && loadStarters(true)} disabled={startersLoading}>
                    <Text style={[styles.regenLink, startersLoading && styles.regenDisabled]}>
                      {startersLoading ? 'Regenerating…' : '🔄 Regenerate'}
                    </Text>
                  </Pressable>
                </View>
                {starters.map((q, i) => (
                  <Pressable key={i} style={[styles.starterBtn, startersLoading && styles.regenDisabled]} onPress={() => !startersLoading && pickStarter(q)}>
                    <Text style={styles.bubbleText}>{q}</Text>
                  </Pressable>
                ))}
              </>
            )}
            {history.map((m, i) => (
              <View key={i} style={[styles.bubble, m.role === 'bot' ? styles.bubbleBot : styles.bubbleUser]}>
                <Text style={styles.bubbleText}>{m.text}</Text>
              </View>
            ))}
            {busy === 'thinking' && (
              <View style={[styles.bubble, styles.bubbleBot]}>
                <Text style={[styles.bubbleText, { color: colors.slate400 }]}>…</Text>
              </View>
            )}
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
            {!!SpeechRecognitionCtor && (
              <Pressable {...micBtnPop.handlers} style={[styles.micBtn, micBtnPop.shadowStyle, listening && styles.micListening]} onPress={toggleVoiceInput}>
                <Text style={styles.voiceBtnText}>{listening ? '⏺' : '🎤'}</Text>
              </Pressable>
            )}
            <Pressable onPress={send} {...sendBtnPop.handlers} style={[styles.sendBtn, sendBtnPop.shadowStyle]}>
              <Text style={styles.sendText}>Send</Text>
            </Pressable>
          </View>
        </>
      </RightDrawer>

      {/* Resume / LinkedIn quick-add modal (#importModal) */}
      <Modal visible={importOpen} transparent animationType="fade" onRequestClose={() => setImportOpen(false)}>
        <Pressable style={styles.importScrim} onPress={() => setImportOpen(false)}>
          <Pressable style={styles.importCard} onPress={(e) => e.stopPropagation()}>
            <View style={styles.importHead}>
              <Text style={styles.importTitle}>Quick add</Text>
              <Pressable onPress={() => setImportOpen(false)} hitSlop={10}>
                <Text style={styles.drawerClose}>✕</Text>
              </Pressable>
            </View>
            <Text style={styles.importSub}>
              Upload your resume or share your LinkedIn profile to automatically extract relevant skills, experience, and education.
            </Text>
            <View style={styles.importTabs}>
              <Pressable onPress={() => setImportTab('resume')}>
                <Text style={[styles.importTab, importTab === 'resume' && styles.importTabActive]}>📄 Upload Resume</Text>
              </Pressable>
              <Pressable onPress={() => setImportTab('linkedin')}>
                <Text style={[styles.importTab, importTab === 'linkedin' && styles.importTabActive]}>🔗 LinkedIn Profile</Text>
              </Pressable>
            </View>
            {importTab === 'resume' ? (
              <View style={{ gap: 12 }}>
                <Pressable style={styles.dropZone} onPress={pickResumeFile}>
                  <Text style={styles.dropZoneTitle}>Drop your resume here, or click to browse</Text>
                  <Text style={styles.dropZoneSub}>PDF or Word document (max 5MB)</Text>
                </Pressable>
                {!!resumeStatus && <Text style={styles.importStatus}>{resumeStatus}</Text>}
                {!!resumeFile && (
                  <Pressable {...resumeSubmitPop.handlers} style={[styles.importSubmit, resumeSubmitPop.shadowStyle]} onPress={submitResume}>
                    <Text style={styles.importSubmitText}>Extract from Resume</Text>
                  </Pressable>
                )}
              </View>
            ) : (
              <View style={{ gap: 12 }}>
                <Text style={styles.importStatus}>
                  Copy and paste your LinkedIn profile text here (LinkedIn blocks direct URL access, so text paste is the only method)
                </Text>
                <TextInput
                  style={styles.linkedinInput}
                  multiline
                  value={linkedinText}
                  onChangeText={setLinkedinText}
                  placeholder="Paste your LinkedIn profile content here..."
                  placeholderTextColor={colors.slate400}
                />
                <Pressable {...linkedinSubmitPop.handlers} style={[styles.importSubmit, linkedinSubmitPop.shadowStyle]} onPress={submitLinkedIn}>
                  <Text style={styles.importSubmitText}>Extract from LinkedIn Text</Text>
                </Pressable>
                {!!linkedinStatus && <Text style={styles.importStatus}>{linkedinStatus}</Text>}
              </View>
            )}
          </Pressable>
        </Pressable>
      </Modal>
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
  ctaBtnCol: { alignItems: 'center', gap: 8 },
  ctaSecondaryLink: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.grayLighter, textDecorationLine: 'underline' },

  mainCard: { padding: 28, gap: 20 },
  headRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: space.lg, flexWrap: 'wrap' },
  titleWrap: { flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  updatedPill: { backgroundColor: colors.lime100, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  updatedText: { fontFamily: fonts.bodyBold, fontSize: 12, lineHeight: 16, color: colors.lime700 },
  updatedStale: { backgroundColor: '#FFE4E6' },
  updatedStaleText: { color: '#BE123C' },
  headBtns: { flexDirection: 'row', gap: 10, flexWrap: 'wrap' },
  hBtnText: { fontSize: 13, lineHeight: 18, fontFamily: fonts.bodyBold },
  deepenBtn: { borderWidth: 2, borderColor: colors.ink, paddingHorizontal: 20 },

  synthStatus: { flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: 2, borderColor: '#F7D9BD', borderRadius: radius.md, backgroundColor: '#FFFAF5', paddingVertical: 10, paddingHorizontal: 14 },
  synthText: { fontFamily: fonts.bodyBold, fontSize: 13, color: '#C2743A' },

  basicsGrid: { flexDirection: 'row', gap: 12, flexWrap: 'wrap' },
  vibeValue: { fontFamily: fonts.bodyBold, fontSize: 14, lineHeight: 22, color: colors.ink },
  vibeEmpty: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: '#B8BFCD', fontStyle: 'italic' },

  // profileContent renders as .vibe-value.vibe-body: 16px / 1.8 line-height, weight 600.
  prose: { fontFamily: fonts.bodySemi, fontSize: 16, lineHeight: 28.8, color: colors.ink },
  // The freshly-merged-sentence mark (.profile-new's warm gradient, flattened).
  newText: { backgroundColor: '#FFD17D', borderRadius: 4 },
  list: { gap: 6, paddingLeft: 2 },
  listRow: { flexDirection: 'row', gap: 6 },
  listNum: { fontFamily: fonts.bodySemi, fontSize: 14, lineHeight: 21, color: colors.ink },
  listText: { fontFamily: fonts.bodySemi, fontSize: 14, lineHeight: 21, color: colors.ink },

  truncatedBox: { borderWidth: 2, borderColor: '#E6D5F5', backgroundColor: '#FBF7FF', borderRadius: radius.lg, padding: 16, gap: 8 },
  truncatedLabel: { fontFamily: fonts.bodyXBold, fontSize: 10, color: '#7C5CAD', letterSpacing: 0.3 },
  truncatedText: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: '#57407D' },

  emptyState: { color: '#9AA9B8', fontStyle: 'italic', fontSize: 14, fontFamily: fonts.bodyMed },
  footRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.lg, marginTop: 8, flexWrap: 'wrap' },
  footLink: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted, textDecorationLine: 'underline' },
  clearLink: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.red },
  clearLinkArmed: { color: colors.statusPastFg },

  drawer: { borderLeftWidth: 4, borderLeftColor: colors.ink },
  drawerHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, paddingHorizontal: 20, paddingTop: 20, paddingBottom: 16, borderBottomWidth: 2, borderBottomColor: colors.lavender },
  drawerHeadBtns: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  drawerTitle: { fontFamily: fonts.display, fontSize: 18, color: colors.ink },
  drawerSub: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.muted, marginTop: 4 },
  drawerClose: { fontFamily: fonts.bodyXBold, fontSize: 20, color: colors.muted },
  drawerBody: { flex: 1, backgroundColor: colors.cream },
  drawerBodyContent: { padding: 20, gap: 10 },
  starterHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 2 },
  starterHeadText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500 },
  regenLink: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.indigo600 },
  regenDisabled: { opacity: 0.5 },
  starterBtn: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: 14, paddingVertical: 10, paddingHorizontal: 14 },
  bubble: { borderWidth: 2, borderColor: colors.slate900, paddingVertical: 10, paddingHorizontal: 14, maxWidth: '85%' },
  bubbleBot: { backgroundColor: colors.white, alignSelf: 'flex-start', borderTopLeftRadius: 14, borderTopRightRadius: 14, borderBottomRightRadius: 14, borderBottomLeftRadius: 2 },
  bubbleUser: { backgroundColor: '#E0E7FF', alignSelf: 'flex-end', borderTopLeftRadius: 14, borderTopRightRadius: 14, borderBottomRightRadius: 2, borderBottomLeftRadius: 14 },
  bubbleText: { fontFamily: fonts.bodySemi, fontSize: 13, lineHeight: 18, color: colors.slate900 },
  drawerFoot: { padding: 20, paddingTop: 14, borderTopWidth: 2, borderTopColor: colors.lavender, flexDirection: 'row', gap: 8 },
  chatInput: { flex: 1, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, paddingVertical: 12, paddingHorizontal: 12, fontFamily: fonts.bodyMed, fontSize: 13, color: colors.slate900, backgroundColor: colors.white },
  voiceBtn: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  voiceBtnOn: { backgroundColor: '#E0E7FF' },
  voiceBtnText: { fontSize: 15 },
  micBtn: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, width: 48, alignItems: 'center', justifyContent: 'center' },
  micListening: { backgroundColor: colors.rose600 },
  sendBtn: { backgroundColor: colors.orange, borderWidth: 2, borderColor: colors.ink, borderRadius: radius.md, paddingHorizontal: 20, alignItems: 'center', justifyContent: 'center' },
  sendText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white },

  importScrim: { flex: 1, backgroundColor: 'rgba(15,23,42,0.55)', alignItems: 'center', justifyContent: 'center', padding: 16 },
  importCard: { backgroundColor: colors.white, borderRadius: radius.xl, width: '100%', maxWidth: 440, padding: 26, gap: 6 },
  importHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  importTitle: { fontFamily: fonts.display, fontSize: 18, color: colors.ink },
  importSub: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 18, color: colors.muted, marginBottom: 10 },
  importTabs: { flexDirection: 'row', gap: 16, borderBottomWidth: 2, borderBottomColor: colors.slate200, marginBottom: 12 },
  importTab: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.muted, paddingBottom: 8 },
  importTabActive: { color: colors.teal, borderBottomWidth: 2, borderBottomColor: colors.teal, marginBottom: -2 },
  dropZone: { borderWidth: 2, borderColor: '#CBD5E1', borderStyle: 'dashed', borderRadius: radius.lg, padding: 24, alignItems: 'center', gap: 4 },
  dropZoneTitle: { fontFamily: fonts.bodyBold, fontSize: 14, color: '#5B6785' },
  dropZoneSub: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate400 },
  importStatus: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 17, color: colors.muted },
  importSubmit: { backgroundColor: colors.slate900, borderRadius: radius.md, paddingVertical: 12, alignItems: 'center' },
  importSubmitText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white },
  linkedinInput: { borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.lg, padding: 12, minHeight: 110, fontFamily: fonts.bodyMed, fontSize: 13, color: colors.slate900, textAlignVertical: 'top' },
});
