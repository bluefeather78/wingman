import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { addTrackerItem, flattenItems, loadTrackerData } from '@/api/trackerStore';
import type { Opportunity } from '@/api/types';
import { PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { ACTIVE_KINDS, KIND_CONFIG } from '@/lib/kinds';
import { countProfileWords } from '@/lib/profile';
import { parseGradeFromText } from '@/lib/grade';
import { inferSubjects, preFilter, rankCandidates, type RankedPick } from '@/lib/ranking';
import { findBucketForKind } from '@/lib/tracker';
import { MiniBadge, PopButton, Screen, SoftCard, Txt } from '@/ui/components';
import { colors, fonts, popShadow, radius, space } from '@/ui/theme';

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

const FILTER_FIELDS = [
  { key: 'type', label: 'Type' },
  { key: 'price', label: 'Cost' },
  { key: 'season', label: 'Season' },
  { key: 'location', label: 'Format' },
] as const;
type FilterKey = (typeof FILTER_FIELDS)[number]['key'];

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
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [trackedIds, setTrackedIds] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [visibleCount, setVisibleCount] = useState(10);
  const [untrackedOnly, setUntrackedOnly] = useState(false);
  const [filters, setFilters] = useState<Record<FilterKey, Set<string>>>({ type: new Set(), price: new Set(), season: new Set(), location: new Set() });
  const [openFacet, setOpenFacet] = useState<FilterKey | null>(null);

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

  // Auto-run the profile-based suggestion once when entering with a ready profile.
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
      setSelected(new Set());
      setVisibleCount(10);
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

  async function addOneToTracker(opp: Opportunity, reason: string) {
    const bucket = findBucketForKind(suggestMode ? kindForOpp(opp) : kind);
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
    await addTrackerItem(bucket, item);
  }

  async function addSelectedToTracker() {
    if (!selected.size || adding) return;
    setAdding(true);
    try {
      for (const id of selected) {
        const r = results.find((x) => x.opp.id === id);
        if (r) await addOneToTracker(r.opp, r.reason);
      }
      setTrackedIds((p) => new Set([...p, ...selected]));
      setSelected(new Set());
    } catch (e) {
      setNote(`Couldn't add: ${(e as Error).message}`);
    } finally {
      setAdding(false);
    }
  }

  function toggleSelect(id: string) {
    setSelected((p) => {
      const n = new Set(p);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }
  function toggleFilter(key: FilterKey, value: string) {
    setFilters((p) => {
      const n = { ...p, [key]: new Set(p[key]) };
      if (n[key].has(value)) n[key].delete(value);
      else n[key].add(value);
      return n;
    });
    setVisibleCount(10);
  }

  // Tracked first, then saved (selected), then tier (script.js renderResults).
  const sortedResults = useMemo(() => {
    const rank = (r: Result) => (trackedIds.has(r.opp.id) ? 0 : selected.has(r.opp.id) ? 1 : 2);
    const tierOrder = { strong: 0, look: 1 };
    return [...results].sort((a, b) => {
      const d = rank(a) - rank(b);
      if (d !== 0) return d;
      return tierOrder[a.tier] - tierOrder[b.tier];
    });
  }, [results, trackedIds, selected]);

  const filteredResults = useMemo(() => {
    return sortedResults.filter((r) => {
      if (untrackedOnly && trackedIds.has(r.opp.id)) return false;
      for (const f of FILTER_FIELDS) {
        const set = filters[f.key];
        if (set.size && !set.has((r.opp[f.key] as string) ?? '')) return false;
      }
      return true;
    });
  }, [sortedResults, untrackedOnly, filters, trackedIds]);
  const visibleResults = filteredResults.slice(0, visibleCount);

  // ---------- Home stage ----------
  if (stage === 'home') {
    return (
      <Screen>
        <SoftCard style={styles.heroCard}>
          {searching ? (
            <View style={styles.loadingRow}>
              <ActivityIndicator color={colors.orangeDeep} size="small" />
              <View style={styles.flex1}>
                <Text style={styles.heroTitleSm}>Finding your matches…</Text>
                <Text style={styles.heroSub}>Searching based on everything in your profile.</Text>
              </View>
            </View>
          ) : !profileText ? (
            <>
              <Text style={styles.heroTitle}>Your profile is empty</Text>
              <Text style={[styles.heroSub, styles.heroSubItalic]}>
                Every match here gets better once we know you. Takes 2 minutes — add a few things and your matches will show up right here.
              </Text>
              <PopButton label="Build my profile" onPress={() => router.push('/(app)/profile')} style={styles.selfStart} />
            </>
          ) : !profileReady ? (
            <>
              <Text style={styles.heroTitle}>I don't have enough yet to match opportunities</Text>
              <Text style={[styles.heroSub, styles.heroSubItalic]}>Help me help you by building your profile</Text>
              <PopButton label="Deepen your story" onPress={() => router.push('/(app)/profile')} style={styles.selfStart} />
            </>
          ) : results.length ? (
            <>
              <Text style={styles.heroTitle}>Your matches are ready</Text>
              <Text style={[styles.heroSub, styles.heroSubItalic]}>Based on everything in your profile.</Text>
              <PopButton label="View my matches →" onPress={() => setStage('results')} style={styles.selfStart} />
            </>
          ) : (
            <>
              <Text style={styles.heroTitle}>Fresh Finds</Text>
              <Text style={[styles.heroSub, styles.heroSubItalic]}>We'll use your profile to surface the best fits.</Text>
              <PopButton label="Suggest opportunities for me" onPress={suggestForMe} style={styles.selfStart} />
            </>
          )}
        </SoftCard>

        <Pressable style={styles.centerLink} onPress={() => setBrowseOpen((b) => !b)}>
          <Text style={styles.link}>{browseOpen ? 'Hide opportunity types' : 'Click here to browse opportunities'}</Text>
        </Pressable>

        {browseOpen && (
          <SoftCard style={{ gap: 24, padding: 32 }}>
            <Txt variant="h2">What kind of opportunity are you looking for?</Txt>
            <View style={styles.grid}>
              {ACTIVE_KINDS.map((k) => (
                <Pressable key={k} style={styles.kindCard} onPress={() => openForm(k)}>
                  <Text style={styles.kindName}>{KIND_CONFIG[k].name}</Text>
                  <Text style={styles.kindDesc}>{KIND_CONFIG[k].desc}</Text>
                </Pressable>
              ))}
            </View>
            <Pressable style={styles.quizCta} onPress={() => { setQuizBranch(null); setStage('quiz'); }}>
              <Text style={styles.quizCtaText}>Not sure? Take a quick quiz →</Text>
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
        <SoftCard style={{ gap: 24, padding: 32 }}>
          <Txt variant="h2">Let's figure out what fits</Txt>
          <Text style={styles.quizQuestion}>{quizBranch ? 'And with that, what do you want to do?' : 'Which of these sounds most like you right now?'}</Text>
          <View style={{ gap: 12 }}>
            {(options ?? QUIZ_ROOT).map((o, i) => (
              <Pressable
                key={i}
                style={[styles.quizOption, i === 0 && { backgroundColor: '#EDF7FC' }, popShadow(3)]}
                onPress={() => {
                  const opt = o as { kind?: string; branch?: string };
                  if (opt.kind) openForm(opt.kind);
                  else if (opt.branch) setQuizBranch(opt.branch);
                }}
              >
                <Text style={styles.quizOptTitle}>{o.label}</Text>
                <Text style={styles.quizOptDesc}>{o.desc}</Text>
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
        <SoftCard style={{ gap: 16, padding: 32 }}>
          <Txt variant="h2" style={{ marginBottom: 8 }}>{cfg.heading}</Txt>

          <View style={{ gap: 8 }}>
            <Text style={styles.fieldLabel}>{cfg.label.toUpperCase()}</Text>
            <Pressable>
              <TextArea value={description} onChangeText={setDescription} placeholder={cfg.placeholder} />
            </Pressable>
            <Text style={styles.charCount}>{description.length} characters - aim for at least 200</Text>
          </View>

          <View style={styles.formRow}>
            <View style={styles.flex1}>
              <Text style={styles.fieldLabelMuted}>GRADE LEVEL (OPTIONAL)</Text>
              <SoftSelect value={grade || 'Prefer not to say'} options={['Prefer not to say', 'Middle School', '9th grade', '10th grade', '11th grade', '12th grade']} onChange={(v) => setGrade(v === 'Prefer not to say' ? '' : v)} />
            </View>
            <View style={styles.flex1}>
              <Text style={styles.fieldLabelMuted}>HOME STATE (OPTIONAL)</Text>
              <SoftInput value={homeState} onChangeText={setHomeState} placeholder="e.g. Washington" />
            </View>
          </View>

          <View style={styles.formRow}>
            <View style={styles.flex1}>
              <Text style={styles.fieldLabelMuted}>COST PREFERENCE</Text>
              <SoftSelect value={freeOnly ? 'Free only' : 'No preference'} options={['No preference', 'Free only']} onChange={(v) => setFreeOnly(v === 'Free only')} />
            </View>
            <View style={styles.flex1}>
              <Text style={styles.fieldLabelMuted}>FORMAT PREFERENCE</Text>
              <SoftSelect value={remote ? 'Remote-friendly' : 'No preference'} options={['No preference', 'Remote-friendly']} onChange={(v) => setRemote(v === 'Remote-friendly')} />
            </View>
          </View>

          {!!note && <Text style={styles.note}>{note}</Text>}
          <View style={styles.formActions}>
            <PopButton
              label="Find matching opportunities"
              variant="secondary"
              square
              loading={searching}
              disabled={!opps || !description.trim()}
              onPress={() => search(description, kind, buildPrefs())}
              style={styles.findBtn}
              textStyle={styles.findBtnText}
            />
          </View>
        </SoftCard>
      </Screen>
    );
  }

  // ---------- Results stage ----------
  return (
    <Screen contentStyle={{ paddingBottom: 90 }}>
      {/* Deepen story banner */}
      <LinearGradient colors={[colors.bannerFrom, colors.bannerTo]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={styles.deepenBanner}>
        <View style={styles.flex1}>
          <Text style={styles.deepenTitle}>Want more matches like these?</Text>
          <Text style={styles.deepenSub}>Deepen your story by adding more details.</Text>
        </View>
        <View style={styles.deepenRight}>
          <Pressable style={styles.deepenBtn} onPress={() => router.push('/(app)/profile')}>
            <Text style={styles.deepenBtnText}>Deepen your story</Text>
          </Pressable>
          <Pressable onPress={() => { setStage('home'); setBrowseOpen(true); }}>
            <Text style={styles.deepenAlt}>or browse opportunities</Text>
          </Pressable>
        </View>
      </LinearGradient>

      {/* Filter row */}
      <View style={styles.filterBar}>
        <Text style={styles.filterLabel}>FILTER:</Text>
        <Pressable style={[styles.filterToggle, untrackedOnly && styles.filterToggleOn]} onPress={() => setUntrackedOnly(!untrackedOnly)}>
          <Text style={styles.filterToggleText}>{untrackedOnly ? '☑' : '☐'} Only untracked</Text>
        </Pressable>
        {FILTER_FIELDS.map((f) => {
          const values = [...new Set(sortedResults.map((r) => (r.opp[f.key] as string) ?? '').filter(Boolean))].sort();
          if (values.length < 2) return null;
          const active = filters[f.key].size;
          return (
            <View key={f.key}>
              <Pressable style={[styles.filterToggle, popShadow(2, colors.slate900)]} onPress={() => setOpenFacet(openFacet === f.key ? null : f.key)}>
                <Text style={styles.filterToggleText}>▾ {f.label}{active ? ` (${active})` : ''}</Text>
              </Pressable>
              {openFacet === f.key && (
                <View style={styles.facetPanel}>
                  {values.map((v) => (
                    <Pressable key={v} style={styles.facetRow} onPress={() => toggleFilter(f.key, v)}>
                      <Text style={styles.facetRowText}>{filters[f.key].has(v) ? '☑' : '☐'} {v}</Text>
                    </Pressable>
                  ))}
                </View>
              )}
            </View>
          );
        })}
      </View>
      {!!note && <Text style={styles.note}>{note}</Text>}

      {/* Result cards */}
      {visibleResults.map(({ opp, reason, tier }) => {
        const isSelected = selected.has(opp.id);
        const isTracked = trackedIds.has(opp.id);
        const cat = suggestMode ? (KIND_CONFIG[kindForOpp(opp)]?.name ?? 'Opportunity') : KIND_CONFIG[kind].name;
        const reviewed = opp.review_status === 'positive';
        const mixed = opp.review_status === 'mixed';
        const metaPills = [opp.org, opp.type, opp.price, opp.location, opp.state && opp.state !== 'All States' ? opp.state : null, opp.season]
          .filter((x): x is string => typeof x === 'string' && x.trim().length > 0);
        return (
          <View key={opp.id} style={[styles.resultCard, popShadow(4), isSelected && styles.resultCardSelected]}>
            <View style={styles.cardTopRow}>
              <View style={styles.badgeRow}>
                <MiniBadge label={cat} bg={colors.violet200} fg={colors.violet900} />
                {tier === 'strong' ? (
                  <MiniBadge label="⭐ Strong Fit" bg={colors.yellow300} fg={colors.slate900} />
                ) : (
                  <MiniBadge label="Worth a look" bg={colors.slate100} fg={colors.slate900} />
                )}
                {reviewed && <MiniBadge label="Well reviewed" bg={colors.emerald100} fg={colors.emerald900} />}
                {mixed && <MiniBadge label="Mixed reviews" bg="#FFEDD5" fg="#7C2D12" />}
              </View>
              {isTracked ? (
                <Pressable style={styles.trackedTag} onPress={() => router.push('/(app)/tracker')}>
                  <Text style={styles.trackedTagText}>📌 In Quest Log. Make edits there.</Text>
                </Pressable>
              ) : (
                <Pressable
                  style={[styles.saveBtn, popShadow(3), isSelected && styles.saveBtnSelected]}
                  onPress={() => toggleSelect(opp.id)}
                >
                  <Text style={styles.saveBtnText}>{isSelected ? '⭐ Saved Match' : '⭐ Save Match'}</Text>
                </Pressable>
              )}
            </View>

            <Pressable onPress={() => opp.url && Linking.openURL(opp.url as string)}>
              <Text style={styles.resultName}>{opp.name}</Text>
            </Pressable>

            {!!reason && (
              <View style={styles.whyRow}>
                <View style={styles.whyBar} />
                <View style={styles.flex1}>
                  <Text style={styles.whyLabel}>WHY IT FITS</Text>
                  <Text style={styles.whyText}>{reason}</Text>
                </View>
              </View>
            )}

            {metaPills.length > 0 && (
              <View style={styles.metaRow}>
                {metaPills.map((p, i) => (
                  <View key={i} style={styles.metaPill}>
                    <Text style={styles.metaPillText}>{p}</Text>
                  </View>
                ))}
              </View>
            )}
            {!!opp.summary && (
              <Text style={styles.summary} numberOfLines={3}>{opp.summary as string}</Text>
            )}
          </View>
        );
      })}

      {filteredResults.length > visibleCount && (
        <View style={styles.centerLink}>
          <PopButton label={`Show more (${filteredResults.length - visibleCount} left)`} variant="ink" small square shadowColor={colors.slate900} onPress={() => setVisibleCount((c) => c + 10)} />
        </View>
      )}

      {/* Selection bar */}
      {results.length > 0 && (
        <View style={styles.selectionBar}>
          <Text style={styles.selectionCount}>{selected.size} selected</Text>
          <PopButton
            label={adding ? 'Adding…' : 'Add to my tracker →'}
            loading={adding}
            disabled={!selected.size}
            onPress={addSelectedToTracker}
          />
        </View>
      )}
    </Screen>
  );
}

// ---------- Small soft form controls (lavender, Poppins-ish) ----------
function TextArea({ value, onChangeText, placeholder }: { value: string; onChangeText: (t: string) => void; placeholder?: string }) {
  return (
    <TextInput
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colors.muted}
      multiline
      style={styles.textArea}
    />
  );
}
function SoftInput({ value, onChangeText, placeholder }: { value: string; onChangeText: (t: string) => void; placeholder?: string }) {
  return (
    <TextInput
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colors.muted}
      style={styles.softInput}
    />
  );
}
function SoftSelect({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <View>
      <Pressable style={styles.softInput} onPress={() => setOpen(!open)}>
        <Text style={styles.softSelectText}>{value}  ▾</Text>
      </Pressable>
      {open && (
        <View style={styles.facetPanel}>
          {options.map((o) => (
            <Pressable key={o} style={styles.facetRow} onPress={() => { onChange(o); setOpen(false); }}>
              <Text style={styles.facetRowText}>{o}</Text>
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}

function BackLink({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.back} onPress={onPress}>
      <Text style={styles.backText}>← {label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  flex1: { flex: 1, minWidth: 0 },
  selfStart: { alignSelf: 'flex-start', marginTop: 8 },
  centerLink: { alignItems: 'center' },
  link: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted, textDecorationLine: 'underline' },

  heroCard: { padding: 40, gap: 8 },
  loadingRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  heroTitle: { fontFamily: fonts.display, fontSize: 30, lineHeight: 38, color: colors.navy, maxWidth: 576 },
  heroTitleSm: { fontFamily: fonts.display, fontSize: 24, lineHeight: 30, color: colors.navy },
  heroSub: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.inkSoft, marginTop: 4 },
  heroSubItalic: { fontStyle: 'italic', fontSize: 16, lineHeight: 26, maxWidth: 576 },

  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 16 },
  kindCard: { flexGrow: 1, flexBasis: '46%', minWidth: 150, backgroundColor: colors.lavender, borderRadius: radius.lg, padding: 16, gap: 4 },
  kindName: { fontFamily: fonts.display, fontSize: 16, color: colors.ink },
  kindDesc: { fontFamily: fonts.bodyMed, fontSize: 13, color: colors.inkSoft, marginTop: 4 },
  quizCta: { borderWidth: 2, borderColor: colors.slate400, borderStyle: 'dashed', borderRadius: radius.lg, paddingVertical: 16, alignItems: 'center' },
  quizCtaText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.slate500 },

  quizQuestion: { fontFamily: fonts.display, fontSize: 18, color: colors.ink },
  quizOption: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.navy, borderRadius: radius.md, padding: 16, gap: 2 },
  quizOptTitle: { fontFamily: fonts.display, fontSize: 18, color: colors.navy },
  quizOptDesc: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.inkSoft },

  fieldLabel: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500, letterSpacing: 0.6, textTransform: 'uppercase' },
  fieldLabelMuted: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted, letterSpacing: 0.6, textTransform: 'uppercase', marginBottom: 8 },
  textArea: { backgroundColor: colors.lavender, borderRadius: radius.lg, padding: 16, minHeight: 160, fontFamily: fonts.bodyMed, fontSize: 15, color: colors.ink, textAlignVertical: 'top' },
  softInput: { backgroundColor: colors.lavender, borderRadius: radius.lg, paddingVertical: 12, paddingHorizontal: 12, fontFamily: fonts.bodyMed, fontSize: 15, color: colors.ink },
  softSelectText: { fontFamily: fonts.bodyMed, fontSize: 15, color: colors.ink },
  charCount: { fontFamily: fonts.bodyBold, fontSize: 10, color: colors.muted, textAlign: 'right' },
  formRow: { flexDirection: 'row', gap: 16, flexWrap: 'wrap' },
  formActions: { flexDirection: 'row', alignItems: 'center', gap: 16, marginTop: 16 },
  findBtn: { backgroundColor: '#F97316', borderWidth: 2, borderColor: colors.slate900 },
  findBtnText: { color: colors.slate900 },
  note: { color: colors.orangeDeep, fontFamily: fonts.bodyBold, fontSize: 13 },

  back: { flexDirection: 'row', alignItems: 'center' },
  backText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.teal },

  deepenBanner: { borderRadius: radius.lg, paddingHorizontal: 28, paddingVertical: 20, flexDirection: 'row', alignItems: 'center', gap: 20, flexWrap: 'wrap' },
  deepenTitle: { fontFamily: fonts.bodyBold, fontSize: 16, color: colors.white },
  deepenSub: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.grayLighter, marginTop: 2 },
  deepenRight: { alignItems: 'center', gap: 8 },
  deepenBtn: { backgroundColor: colors.orangeDeep, borderRadius: radius.pill, paddingHorizontal: 20, paddingVertical: 10 },
  deepenBtnText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white },
  deepenAlt: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.grayLighter, textDecorationLine: 'underline' },

  filterBar: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap', zIndex: 20 },
  filterLabel: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted, letterSpacing: 0.6 },
  filterToggle: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 8 },
  filterToggleOn: { backgroundColor: colors.lavender },
  filterToggleText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate900 },
  facetPanel: { position: 'absolute', top: '100%', left: 0, marginTop: 8, width: 224, backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.lg, padding: 12, zIndex: 50, gap: 2 },
  facetRow: { paddingVertical: 4 },
  facetRowText: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate900 },

  resultCard: { backgroundColor: colors.white, borderWidth: 4, borderColor: colors.slate900, borderRadius: radius.xxl, padding: 24, gap: 16 },
  resultCardSelected: { borderColor: '#A3E635', backgroundColor: '#F7FEE7' },
  cardTopRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' },
  badgeRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap', flexShrink: 1 },
  trackedTag: { backgroundColor: '#1E293B', borderRadius: radius.pill, paddingHorizontal: 16, paddingVertical: 8 },
  trackedTagText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.white },
  saveBtn: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 20, paddingVertical: 10 },
  saveBtnSelected: { backgroundColor: '#A3E635' },
  saveBtnText: { fontFamily: fonts.bodyXBold, fontSize: 12, color: colors.slate900 },
  resultName: { fontFamily: fonts.display, fontSize: 30, lineHeight: 36, color: colors.slate900 },
  whyRow: { flexDirection: 'row', gap: 12 },
  whyBar: { width: 4, borderRadius: 2, backgroundColor: '#818CF8' },
  whyLabel: { fontFamily: fonts.bodyBold, fontSize: 10, color: colors.slate400, letterSpacing: 0.8, marginBottom: 4 },
  whyText: { fontFamily: fonts.display, fontSize: 20, lineHeight: 26, color: colors.slate900 },
  metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  metaPill: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.indigo200, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  metaPillText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate900 },
  summary: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.slate500 },

  selectionBar: {
    position: Platform.OS === 'web' ? ('fixed' as 'absolute') : 'absolute',
    bottom: 16,
    left: 16,
    right: 16,
    maxWidth: 832,
    marginHorizontal: 'auto',
    backgroundColor: colors.slate900,
    borderRadius: radius.lg,
    padding: 16,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
  },
  selectionCount: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white },
});
