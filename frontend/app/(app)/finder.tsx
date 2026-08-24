import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, StyleSheet, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { addTrackerItem, flattenItems, loadTrackerData } from '@/api/trackerStore';
import type { Opportunity } from '@/api/types';
import { PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { ACTIVE_KINDS, KIND_CONFIG } from '@/lib/kinds';
import { countProfileWords } from '@/lib/profile';
import { parseGradeFromText } from '@/lib/grade';
import { inferSubjects, preFilter, rankCandidates, type RankedPick } from '@/lib/ranking';
import { findBucketForKind } from '@/lib/tracker';
import { Badge, Chip, Field, PopButton, PopCard, Screen, SoftCard, Txt } from '@/ui/components';
import { colors, radius, space } from '@/ui/theme';

interface Result {
  opp: Opportunity;
  reason: string;
  tier: 'strong' | 'look';
}
const callGemini = httpClient.callGemini.bind(httpClient);
type Stage = 'home' | 'quiz' | 'form' | 'results';

// Map a catalog opportunity's `type` to a kind key (used when adding from a mixed suggest list).
function kindForOpp(opp: Opportunity): string {
  const map: Record<string, string> = {
    Program: 'summer', Internship: 'internship', Conference: 'conference',
    Journal: 'journal', Research: 'research-competition', Competition: 'pure-competition',
  };
  return map[(opp.type as string) ?? ''] ?? 'summer';
}

const GRADES = [
  { label: 'Any', v: '' },
  { label: '9th', v: '9th grade' },
  { label: '10th', v: '10th grade' },
  { label: '11th', v: '11th grade' },
  { label: '12th', v: '12th grade' },
  { label: 'Middle', v: 'middle school' },
];

// Quiz: root → sub-branch → kind (from script.js QUIZ_BRANCHES + the live quiz screen).
const QUIZ_ROOT = [
  { label: 'I already have a research paper or project', desc: 'In progress or already completed', branch: 'project' },
  { label: "I'm looking for something to do when school is out", desc: 'Camps, programs, or work experience', branch: 'timeoff' },
  { label: 'I enjoy competing directly with my peers', desc: 'Tests, exams, head-to-head challenges', kind: 'pure-competition' },
] as const;
const QUIZ_SUB: Record<string, { label: string; desc: string; kind: string }[]> = {
  project: [
    { label: 'Enter it in a competition', desc: 'Science fairs, app challenges, project contests', kind: 'research-competition' },
    { label: 'Present it at a conference', desc: 'Submit a paper to a workshop or conference', kind: 'conference' },
    { label: 'Get it published', desc: 'Submit to an academic or student journal', kind: 'journal' },
  ],
  timeoff: [
    { label: 'Hands-on work experience', desc: 'Work with a lab, company, or organization', kind: 'internship' },
    { label: 'A summer program', desc: 'Camps, pre-college programs, academies', kind: 'summer' },
  ],
};

export default function Finder() {
  const router = useRouter();
  const [opps, setOpps] = useState<Opportunity[] | null>(null);
  const [oppsError, setOppsError] = useState<string | null>(null);
  const [profileText, setProfileText] = useState('');
  const [stage, setStage] = useState<Stage>('home');
  const [browseOpen, setBrowseOpen] = useState(false);
  const [quizBranch, setQuizBranch] = useState<string | null>(null);
  const [kind, setKind] = useState<string>(ACTIVE_KINDS[0]);
  const [suggestMode, setSuggestMode] = useState(false);

  const [description, setDescription] = useState('');
  const [grade, setGrade] = useState('');
  const [homeState, setHomeState] = useState('');
  const [freeOnly, setFreeOnly] = useState(false);
  const [remote, setRemote] = useState(false);

  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<Result[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [trackedIds, setTrackedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    httpClient.getOpportunities().then((r) => alive && setOpps(r)).catch((e) => alive && setOppsError((e as Error).message));
    httpClient.loadData<{ synthesized?: string }>('student-profile').then((p) => alive && setProfileText(p?.synthesized ?? '')).catch(() => {});
    loadTrackerData().then((d) => alive && setTrackedIds(new Set(flattenItems(d).map((i) => i.id)))).catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const profileReady = countProfileWords(profileText) >= PROFILE_SUFFICIENT_LENGTH;

  // Auto-run the profile-based suggestion once when entering with a ready profile (matches
  // the web app, which starts "Finding your matches…" on load rather than behind a button).
  const autoRan = useRef(false);
  useEffect(() => {
    if (autoRan.current) return;
    if (opps && profileReady && stage === 'home' && !results.length) {
      autoRan.current = true;
      void suggestForMe();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opps, profileReady]);

  function openForm(k: string) {
    setKind(k);
    setSuggestMode(false);
    setDescription('');
    setResults([]);
    setNote(null);
    setStage('form');
  }

  function buildPrefs(): string {
    const parts: string[] = [];
    if (freeOnly) parts.push('free or low-cost only');
    if (remote) parts.push('remote-friendly');
    if (homeState.trim()) parts.push(`based in or near ${homeState.trim()}`);
    return parts.join(', ');
  }

  async function search(desc: string, k: string | null, prefs: string) {
    if (!opps || !desc.trim() || searching) return;
    setSearching(true);
    setNote(null);
    const cfg = k ? KIND_CONFIG[k] : null;
    const strict = !!cfg?.strictType;
    const gradeNum = parseGradeFromText(grade);
    try {
      let subjectHints: string[] = [];
      try {
        subjectHints = await inferSubjects(callGemini, desc);
      } catch {
        /* best effort */
      }
      const pool = preFilter(opps, desc, subjectHints, cfg?.dbTypes ?? null, strict, gradeNum);
      const byId = new Map(pool.map((o) => [o.id, o]));
      try {
        const ranked: RankedPick[] = await rankCandidates(callGemini, desc, pool, prefs || null, strict);
        const mapped = ranked
          .map((r) => (byId.get(r.id) ? { opp: byId.get(r.id) as Opportunity, reason: r.reason, tier: r.tier } : null))
          .filter((x): x is Result => x !== null);
        if (!mapped.length) throw new Error('empty');
        setResults(mapped);
      } catch {
        setNote('Showing keyword matches — AI ranking is unavailable right now.');
        setResults(pool.slice(0, 12).map((opp) => ({ opp, reason: '', tier: 'look' as const })));
      }
      setStage('results');
    } catch (e) {
      setNote(`Search failed: ${(e as Error).message}`);
    } finally {
      setSearching(false);
    }
  }

  async function suggestForMe() {
    setSuggestMode(true);
    await search(profileText, null, buildPrefs());
  }

  async function addToTracker(opp: Opportunity, reason: string) {
    const bucket = findBucketForKind(suggestMode ? kindForOpp(opp) : kind);
    // Build the shared item shape; enrich dates from the cross-user deadline cache.
    const meta = [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · ');
    const item = {
      id: opp.id,
      name: opp.name,
      url: (opp.url as string) ?? null,
      type: (opp.type as string) ?? null,
      bucket,
      progressStatus: 'not_started',
      status: 'unknown' as const,
      reviewStatus: (opp.review_status as string) ?? null,
      reviewSummary: (opp.review_summary as string) ?? null,
      meta: meta || (opp.summary as string) || '',
      fit: reason || (opp.summary as string) || '',
      importantDates: [] as { label: string; dateISO: string; type: string }[],
      applyUrl: (opp.url as string) ?? null,
      applyLabel: 'Apply / learn more',
      actionItems: [],
    };
    try {
      const info = await httpClient.getDeadlineCheck(opp.id);
      if (info?.important_dates?.length) {
        item.importantDates = info.important_dates.map((d) => ({ label: d.label, dateISO: d.date_iso, type: d.type }));
        if (info.status) item.status = info.status as typeof item.status;
      }
    } catch {
      /* dates are best-effort */
    }
    try {
      await addTrackerItem(bucket, item);
      setAdded((p) => new Set(p).add(opp.id));
    } catch (e) {
      setNote(`Couldn't add: ${(e as Error).message}`);
    }
  }

  // ---------- Home stage ----------
  if (stage === 'home') {
    // Auto-suggest loading state (matches :8000's "Finding your matches…").
    if (suggestMode && searching) {
      return (
        <Screen>
          <SoftCard style={styles.loadingCard}>
            <ActivityIndicator color={colors.orange} />
            <View style={styles.flex1}>
              <Txt variant="h2">Finding your matches…</Txt>
              <Txt variant="body">Searching based on everything in your profile.</Txt>
            </View>
          </SoftCard>
          <Pressable style={styles.centerLink} onPress={() => setBrowseOpen((b) => !b)}>
            <Txt variant="small" style={styles.link}>Click here to browse opportunities</Txt>
          </Pressable>
        </Screen>
      );
    }
    return (
      <Screen>
        {/* Suggest hero */}
        <SoftCard style={{ gap: space.md }}>
          {profileReady ? (
            <>
              <Txt variant="label">FRESH PICKS</Txt>
              <Txt variant="h1">Opportunities matched to your vibe</Txt>
              <Txt variant="body">We'll use your profile to surface the best fits across every category.</Txt>
              <PopButton label={searching ? 'Finding…' : 'Suggest opportunities for me'} loading={searching} onPress={suggestForMe} />
            </>
          ) : (
            <>
              <Txt variant="h1">Your profile is empty</Txt>
              <Txt variant="body">Every match here gets better once we know you. Takes 2 minutes — add a few things and your matches show up right here.</Txt>
              <PopButton label="Build my profile" onPress={() => router.push('/(app)/profile')} />
            </>
          )}
        </SoftCard>

        <Pressable style={styles.centerLink} onPress={() => setBrowseOpen((b) => !b)}>
          <Txt variant="small" style={styles.link}>
            {browseOpen ? 'Hide opportunity types' : 'Click here to browse opportunities'}
          </Txt>
        </Pressable>

        {browseOpen && (
          <SoftCard style={{ gap: space.md }}>
            <Txt variant="h2">What kind of opportunity are you looking for?</Txt>
            <Txt variant="small">
              {opps ? `Searching ${opps.length.toLocaleString()} opportunities.` : oppsError ? `Couldn't load: ${oppsError}` : 'Loading…'}
            </Txt>
            <View style={styles.grid}>
              {ACTIVE_KINDS.map((k) => (
                <Pressable key={k} style={styles.kindCard} onPress={() => openForm(k)}>
                  <Txt variant="h3">{KIND_CONFIG[k].name}</Txt>
                  <Txt variant="small" style={{ color: colors.inkSoft }}>
                    {KIND_CONFIG[k].desc}
                  </Txt>
                </Pressable>
              ))}
            </View>
            <Pressable style={styles.quizCta} onPress={() => { setQuizBranch(null); setStage('quiz'); }}>
              <Txt variant="bodyStrong" style={{ color: colors.muted }}>
                Not sure? Take a quick quiz →
              </Txt>
            </Pressable>
          </SoftCard>
        )}
      </Screen>
    );
  }

  // ---------- Quiz stage ----------
  if (stage === 'quiz') {
    const options = quizBranch ? QUIZ_SUB[quizBranch] : null;
    return (
      <Screen>
        <BackLink label="Back to opportunity type" onPress={() => (quizBranch ? setQuizBranch(null) : setStage('home'))} />
        <SoftCard style={{ gap: space.md }}>
          <Txt variant="h1">Let's figure out what fits</Txt>
          <Txt variant="body">{quizBranch ? 'And with that, what do you want to do?' : 'Which of these sounds most like you right now?'}</Txt>
          <View style={{ gap: space.md }}>
            {(options ?? QUIZ_ROOT).map((o, i) => (
              <Pressable
                key={i}
                style={styles.quizOption}
                onPress={() => {
                  const opt = o as { kind?: string; branch?: string };
                  if (opt.kind) openForm(opt.kind);
                  else if (opt.branch) setQuizBranch(opt.branch);
                }}
              >
                <Txt variant="bodyStrong">{o.label}</Txt>
                <Txt variant="small">{o.desc}</Txt>
              </Pressable>
            ))}
          </View>
        </SoftCard>
      </Screen>
    );
  }

  // ---------- Form stage ----------
  if (stage === 'form') {
    const cfg = KIND_CONFIG[kind];
    return (
      <Screen>
        <BackLink label="Back to opportunity type" onPress={() => setStage('home')} />
        <SoftCard style={{ gap: space.lg }}>
          <Txt variant="h1">{cfg.heading}</Txt>
          <Field label={cfg.label.toUpperCase()} value={description} onChangeText={setDescription} placeholder={cfg.placeholder} multiline hint={`${description.length} characters — aim for at least 200`} />

          <View>
            <Txt variant="label">GRADE LEVEL (OPTIONAL)</Txt>
            <View style={styles.chipRow}>
              {GRADES.map((g) => (
                <Chip key={g.label} label={g.label} active={grade === g.v} onPress={() => setGrade(g.v)} />
              ))}
            </View>
          </View>

          <Field label="HOME STATE (OPTIONAL)" value={homeState} onChangeText={setHomeState} placeholder="e.g. Washington" />

          <View style={styles.filterRow}>
            <View style={styles.flex1}>
              <Txt variant="label">COST</Txt>
              <View style={styles.chipRow}>
                <Chip label="Any" active={!freeOnly} onPress={() => setFreeOnly(false)} />
                <Chip label="Free only" active={freeOnly} onPress={() => setFreeOnly(true)} />
              </View>
            </View>
            <View style={styles.flex1}>
              <Txt variant="label">FORMAT</Txt>
              <View style={styles.chipRow}>
                <Chip label="Any" active={!remote} onPress={() => setRemote(false)} />
                <Chip label="Remote" active={remote} onPress={() => setRemote(true)} />
              </View>
            </View>
          </View>

          {!!note && <Txt style={styles.note}>{note}</Txt>}
          <PopButton label="Find matching opportunities" loading={searching} disabled={!opps || !description.trim()} onPress={() => search(description, kind, buildPrefs())} />
        </SoftCard>
      </Screen>
    );
  }

  // ---------- Results stage ----------
  return (
    <Screen>
      <BackLink label="New search" onPress={() => setStage(suggestMode ? 'home' : 'form')} />
      <SoftCard color={colors.navy} style={styles.deepenBanner}>
        <View style={styles.flex1}>
          <Txt variant="h3" style={{ color: colors.white }}>Want more matches like these?</Txt>
          <Txt variant="small" style={{ color: '#D6E4F5' }}>Deepen your story by adding more details.</Txt>
        </View>
        <PopButton label="Deepen your story" small onPress={() => router.push('/(app)/profile')} />
      </SoftCard>
      <View style={{ gap: space.xs }}>
        <Txt variant="label">{suggestMode ? 'SUGGESTED FOR YOU' : KIND_CONFIG[kind].name.toUpperCase()}</Txt>
        <Txt variant="h1">
          {results.length} match{results.length === 1 ? '' : 'es'}
        </Txt>
        {!!note && <Txt style={styles.note}>{note}</Txt>}
      </View>
      {results.map(({ opp, reason, tier }) => {
        const cat = KIND_CONFIG[kindForOpp(opp)]?.name?.toUpperCase() ?? 'OPPORTUNITY';
        const reviewed = !!opp.review_status && opp.review_status !== 'insufficient_data' && opp.review_status !== 'concerns_found';
        const metaPills = [opp.org, opp.type, opp.price, opp.location, opp.state, opp.season]
          .filter((x): x is string => typeof x === 'string' && x.trim().length > 0);
        return (
          <PopCard key={opp.id} style={{ gap: space.sm }}>
            <View style={styles.badgeRow}>
              <Badge label={cat} bg={colors.lavender} fg={colors.purple} />
              {tier === 'strong' ? <Badge label="⚡ STRONG FIT" bg={colors.yellow} fg={colors.navyDeep} /> : <Badge label="WORTH A LOOK" bg={colors.lavender} fg={colors.navy} />}
              {reviewed && <Badge label="✓ WELL REVIEWED" bg={colors.greenSoft} fg={colors.green} />}
            </View>
            <Txt variant="h2">{opp.name}</Txt>
            {!!reason && (
              <View style={{ gap: 2 }}>
                <Txt variant="label">WHY IT FITS</Txt>
                <Txt variant="bodyStrong" style={{ color: colors.ink }}>{reason}</Txt>
              </View>
            )}
            {metaPills.length > 0 && (
              <View style={styles.metaRow}>
                {metaPills.slice(0, 6).map((p, i) => (
                  <View key={i} style={styles.metaPill}><Txt variant="small" style={styles.metaPillText}>{p}</Txt></View>
                ))}
              </View>
            )}
            {!!opp.summary && <Txt variant="body" numberOfLines={3}>{opp.summary as string}</Txt>}
            <View style={styles.actions}>
              {!!opp.url && <PopButton label="Open" variant="secondary" small onPress={() => Linking.openURL(opp.url as string)} />}
              {added.has(opp.id) || trackedIds.has(opp.id) ? (
                <Badge label="📌 IN QUEST LOG" bg={colors.navy} fg={colors.white} />
              ) : (
                <PopButton label="Add to tracker" small onPress={() => addToTracker(opp, reason)} />
              )}
            </View>
          </PopCard>
        );
      })}
    </Screen>
  );
}

function BackLink({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.back} onPress={onPress}>
      <Ionicons name="chevron-back" size={16} color={colors.teal} />
      <Txt variant="bodyStrong" style={{ color: colors.teal }}>
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  centerLink: { alignItems: 'center' },
  link: { color: colors.muted, textDecorationLine: 'underline' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: space.md },
  kindCard: { flexGrow: 1, flexBasis: '46%', minWidth: 150, backgroundColor: colors.lavender, borderRadius: radius.md, padding: space.lg, gap: 4 },
  quizCta: { borderWidth: 2, borderColor: colors.hairline, borderStyle: 'dashed', borderRadius: radius.lg, padding: space.md, alignItems: 'center' },
  quizOption: { backgroundColor: colors.lavender, borderRadius: radius.md, padding: space.lg, gap: 2 },
  back: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: 6 },
  filterRow: { flexDirection: 'row', gap: space.lg },
  flex1: { flex: 1 },
  note: { color: colors.orangeDeep, fontFamily: 'PlusJakartaSans_700Bold', fontSize: 13 },
  cardHead: { flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' },
  actions: { flexDirection: 'row', gap: space.sm, marginTop: space.xs, flexWrap: 'wrap' },
  badgeRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, flexWrap: 'wrap' },
  metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  metaPill: { borderWidth: 1, borderColor: colors.borderSoft, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 3 },
  metaPillText: { color: colors.inkSoft },
  loadingCard: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  deepenBanner: { flexDirection: 'row', alignItems: 'center', gap: space.md },
});
