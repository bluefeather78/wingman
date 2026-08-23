import { Ionicons } from '@expo/vector-icons';
import { useEffect, useState } from 'react';
import { Linking, Pressable, StyleSheet, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { addTrackerItem } from '@/api/trackerStore';
import type { Opportunity } from '@/api/types';
import { ACTIVE_KINDS, KIND_CONFIG } from '@/lib/kinds';
import { inferSubjects, preFilter, rankCandidates, type RankedPick } from '@/lib/ranking';
import { findBucketForKind } from '@/lib/tracker';
import { Badge, Field, PopButton, PopCard, Screen, Txt } from '@/ui/components';
import { colors, space } from '@/ui/theme';

// Finder / Wizard: pick a kind → describe → the ranking chain (inferSubjects → preFilter →
// rankCandidates) → ranked results → add to tracker. Falls back to keyword ranking if the
// AI rank is unavailable so the screen still works offline / in mock mode.
interface Result {
  opp: Opportunity;
  reason: string;
  tier: 'strong' | 'look';
}
const callGemini = httpClient.callGemini.bind(httpClient);
type Stage = 'kind' | 'form' | 'results';

// A splash of colour per kind card, cycled.
const KIND_COLORS = [colors.lime, colors.purple, colors.orange, colors.yellow, colors.greenSoft, colors.borderSoft];

export default function Finder() {
  const [opps, setOpps] = useState<Opportunity[] | null>(null);
  const [oppsError, setOppsError] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage>('kind');
  const [kind, setKind] = useState<string>(ACTIVE_KINDS[0]);
  const [description, setDescription] = useState('');
  const [prefs, setPrefs] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<Result[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    httpClient
      .getOpportunities()
      .then((rows) => alive && setOpps(rows))
      .catch((e) => alive && setOppsError((e as Error).message));
    return () => {
      alive = false;
    };
  }, []);

  const cfg = KIND_CONFIG[kind];

  function chooseKind(k: string) {
    setKind(k);
    setDescription('');
    setPrefs('');
    setResults([]);
    setNote(null);
    setStage('form');
  }

  async function runSearch() {
    if (!opps || !description.trim() || searching) return;
    setSearching(true);
    setNote(null);
    const strict = !!cfg.strictType;
    try {
      let subjectHints: string[] = [];
      try {
        subjectHints = await inferSubjects(callGemini, description);
      } catch {
        /* best effort */
      }
      const pool = preFilter(opps, description, subjectHints, cfg.dbTypes ?? null, strict, null);
      const byId = new Map(pool.map((o) => [o.id, o]));
      try {
        const ranked: RankedPick[] = await rankCandidates(callGemini, description, pool, prefs.trim() || null, strict);
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

  async function addToTracker(opp: Opportunity, reason: string) {
    try {
      await addTrackerItem({
        oppId: opp.id,
        bucket: findBucketForKind(kind),
        name: opp.name,
        org: opp.org ?? null,
        url: opp.url ?? null,
        summary: opp.summary ?? null,
        reason,
      });
      setAdded((prev) => new Set(prev).add(opp.id));
    } catch (e) {
      setNote(`Couldn't add to tracker: ${(e as Error).message}`);
    }
  }

  // ---------- Stage: pick a kind ----------
  if (stage === 'kind') {
    return (
      <Screen>
        <View style={styles.head}>
          <Txt variant="label">FINDER</Txt>
          <Txt variant="hero">What are you after?</Txt>
          <Txt variant="body">
            {opps ? `Searching ${opps.length.toLocaleString()} opportunities.` : oppsError ? `Couldn't load the catalog: ${oppsError}` : 'Loading the catalog…'}
          </Txt>
        </View>
        <View style={styles.grid}>
          {ACTIVE_KINDS.map((k, i) => (
            <Pressable key={k} style={styles.gridItem} onPress={() => chooseKind(k)}>
              <PopCard color={KIND_COLORS[i % KIND_COLORS.length]} style={styles.kindCard}>
                <Txt variant="h3">{KIND_CONFIG[k].name}</Txt>
                <Txt variant="small" style={styles.kindDesc}>
                  {KIND_CONFIG[k].desc}
                </Txt>
              </PopCard>
            </Pressable>
          ))}
        </View>
      </Screen>
    );
  }

  // ---------- Stage: describe ----------
  if (stage === 'form') {
    return (
      <Screen>
        <BackLink label="All categories" onPress={() => setStage('kind')} />
        <View style={styles.head}>
          <Txt variant="label">{cfg.name.toUpperCase()}</Txt>
          <Txt variant="h1">{cfg.heading}</Txt>
          <Txt variant="body">{cfg.sub}</Txt>
        </View>
        <PopCard style={{ gap: space.md }}>
          <Field
            label={cfg.label}
            value={description}
            onChangeText={setDescription}
            placeholder={cfg.placeholder}
            multiline
          />
          <Field
            label="Preferences (optional)"
            value={prefs}
            onChangeText={setPrefs}
            placeholder="e.g. free or low-cost, remote, near Seattle"
          />
          {!!note && <Txt style={styles.note}>{note}</Txt>}
          <PopButton
            label="Find my matches"
            onPress={runSearch}
            loading={searching}
            disabled={!opps || !description.trim()}
            full
          />
        </PopCard>
      </Screen>
    );
  }

  // ---------- Stage: results ----------
  return (
    <Screen>
      <BackLink label="New search" onPress={() => setStage('form')} />
      <View style={styles.head}>
        <Txt variant="label">{cfg.name.toUpperCase()}</Txt>
        <Txt variant="h1">
          {results.length} match{results.length === 1 ? '' : 'es'}
        </Txt>
        {!!note && <Txt style={styles.note}>{note}</Txt>}
      </View>
      <View style={{ gap: space.lg }}>
        {results.map(({ opp, reason, tier }) => (
          <PopCard key={opp.id} style={{ gap: space.sm }}>
            <View style={styles.cardHead}>
              <Txt variant="h3" style={styles.flex1}>
                {opp.name}
              </Txt>
              {tier === 'strong' ? (
                <Badge label="STRONG FIT" bg={colors.lime} fg={colors.ink} />
              ) : (
                <Badge label="WORTH A LOOK" bg={colors.borderSoft} fg={colors.navy} />
              )}
            </View>
            {!!opp.org && <Txt variant="small">{opp.org}</Txt>}
            {!!reason && (
              <Txt variant="bodyStrong" style={styles.reason}>
                “{reason}”
              </Txt>
            )}
            {!!opp.summary && (
              <Txt variant="body" numberOfLines={3}>
                {opp.summary}
              </Txt>
            )}
            <View style={styles.actions}>
              {!!opp.url && (
                <PopButton label="Open" variant="secondary" onPress={() => Linking.openURL(opp.url as string)} />
              )}
              <PopButton
                label={added.has(opp.id) ? 'Added ✓' : 'Add to tracker'}
                variant={added.has(opp.id) ? 'primary' : 'purple'}
                onPress={() => addToTracker(opp, reason)}
                disabled={added.has(opp.id)}
              />
            </View>
          </PopCard>
        ))}
      </View>
    </Screen>
  );
}

function BackLink({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.back} onPress={onPress}>
      <Ionicons name="chevron-back" size={16} color={colors.navy} />
      <Txt variant="label">{label}</Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  head: { gap: space.xs, marginBottom: space.xs },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: space.lg },
  gridItem: { flexGrow: 1, flexBasis: '46%', minWidth: 150 } as object,
  kindCard: { gap: 4, minHeight: 104, justifyContent: 'center' },
  kindDesc: { color: colors.inkSoft },
  back: { flexDirection: 'row', alignItems: 'center', gap: 2, marginBottom: space.xs },
  note: { color: colors.orange, fontFamily: 'PlusJakartaSans_700Bold', fontSize: 13 },
  cardHead: { flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' },
  flex1: { flex: 1 },
  reason: { color: colors.navy },
  actions: { flexDirection: 'row', gap: space.md, marginTop: space.xs, flexWrap: 'wrap' },
});
