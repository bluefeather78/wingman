import { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { httpClient } from '@/api/httpClient';
import { addTrackerItem } from '@/api/trackerStore';
import type { Opportunity } from '@/api/types';
import { ACTIVE_KINDS, KIND_CONFIG } from '@/lib/kinds';
import { inferSubjects, preFilter, rankCandidates, type RankedPick } from '@/lib/ranking';
import { findBucketForKind } from '@/lib/tracker';

// Finder / Wizard — pick a kind, describe your interests, then the salvaged ranking chain
// (inferSubjects -> preFilter -> rankCandidates) produces ranked results. If the AI rank
// fails (e.g. offline), it degrades to the keyword pre-filter so the screen stays useful.
interface Result {
  opp: Opportunity;
  reason: string;
  tier: 'strong' | 'look';
}

const callGemini = httpClient.callGemini.bind(httpClient);

export default function Finder() {
  const [opps, setOpps] = useState<Opportunity[] | null>(null);
  const [oppsError, setOppsError] = useState<string | null>(null);
  const [kind, setKind] = useState<string>(ACTIVE_KINDS[0]);
  const [description, setDescription] = useState('');
  const [prefs, setPrefs] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<Result[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());

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

  async function runSearch() {
    if (!opps || !description.trim() || searching) return;
    setSearching(true);
    setResults(null);
    setNote(null);
    const strict = !!cfg.strictType;
    try {
      // Subject hints are best-effort — a failure here shouldn't block the search.
      let subjectHints: string[] = [];
      try {
        subjectHints = await inferSubjects(callGemini, description);
      } catch {
        /* ignore */
      }
      const pool = preFilter(opps, description, subjectHints, cfg.dbTypes ?? null, strict, null);
      const byId = new Map(pool.map((o) => [o.id, o]));
      try {
        const ranked: RankedPick[] = await rankCandidates(
          callGemini,
          description,
          pool,
          prefs.trim() || null,
          strict,
        );
        const mapped = ranked
          .map((r) => {
            const opp = byId.get(r.id);
            return opp ? { opp, reason: r.reason, tier: r.tier } : null;
          })
          .filter((x): x is Result => x !== null);
        if (mapped.length) {
          setResults(mapped);
        } else {
          throw new Error('empty rank');
        }
      } catch {
        // Fallback: show the keyword pre-filter's top hits with no AI reasons.
        setNote('Showing keyword matches (AI ranking unavailable).');
        setResults(pool.slice(0, 12).map((opp) => ({ opp, reason: '', tier: 'look' as const })));
      }
    } catch (e) {
      setNote(`Search failed: ${(e as Error).message}`);
    } finally {
      setSearching(false);
    }
  }

  const catalogStatus = useMemo(() => {
    if (oppsError) return `Couldn't load opportunities: ${oppsError}`;
    if (!opps) return 'Loading opportunities…';
    return `${opps.length} opportunities loaded`;
  }, [opps, oppsError]);

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Text style={styles.h1}>Find opportunities</Text>
      <Text style={styles.dim}>{catalogStatus}</Text>

      <Text style={styles.section}>What are you looking for?</Text>
      <View style={styles.kindRow}>
        {ACTIVE_KINDS.map((k) => (
          <Pressable
            key={k}
            style={[styles.chip, k === kind && styles.chipActive]}
            onPress={() => setKind(k)}
          >
            <Text style={[styles.chipText, k === kind && styles.chipTextActive]}>
              {KIND_CONFIG[k].name}
            </Text>
          </Pressable>
        ))}
      </View>

      <Text style={styles.section}>{cfg.label}</Text>
      <TextInput
        style={styles.textarea}
        multiline
        numberOfLines={5}
        placeholder={cfg.placeholder}
        value={description}
        onChangeText={setDescription}
      />

      <Text style={styles.section}>Preferences (optional)</Text>
      <TextInput
        style={styles.input}
        placeholder="e.g. free or low-cost, remote, near Seattle…"
        value={prefs}
        onChangeText={setPrefs}
      />

      <Pressable
        style={[styles.button, (!opps || !description.trim() || searching) && styles.buttonDisabled]}
        onPress={runSearch}
        disabled={!opps || !description.trim() || searching}
      >
        {searching ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.buttonText}>Find matches</Text>
        )}
      </Pressable>

      {note && <Text style={styles.note}>{note}</Text>}

      {results && (
        <View style={styles.results}>
          <Text style={styles.section}>
            {results.length} match{results.length === 1 ? '' : 'es'}
          </Text>
          {results.map(({ opp, reason, tier }) => (
            <View key={opp.id} style={styles.card}>
              <View style={styles.cardHead}>
                <Text style={styles.cardName}>{opp.name}</Text>
                <Text style={[styles.badge, tier === 'strong' ? styles.badgeStrong : styles.badgeLook]}>
                  {tier === 'strong' ? 'Strong' : 'Worth a look'}
                </Text>
              </View>
              {!!opp.org && <Text style={styles.cardOrg}>{opp.org}</Text>}
              {!!reason && <Text style={styles.cardReason}>{reason}</Text>}
              {!!opp.summary && (
                <Text style={styles.cardSummary} numberOfLines={2}>
                  {opp.summary}
                </Text>
              )}
              <View style={styles.cardActions}>
                {!!opp.url && (
                  <Pressable style={styles.cardBtn} onPress={() => Linking.openURL(opp.url as string)}>
                    <Text style={styles.cardBtnText}>Open link</Text>
                  </Pressable>
                )}
                <Pressable
                  style={[styles.cardBtn, added.has(opp.id) && styles.cardBtnDone]}
                  onPress={() => addToTracker(opp, reason)}
                  disabled={added.has(opp.id)}
                >
                  <Text style={[styles.cardBtnText, added.has(opp.id) && styles.cardBtnDoneText]}>
                    {added.has(opp.id) ? 'Added ✓' : 'Add to tracker'}
                  </Text>
                </Pressable>
              </View>
            </View>
          ))}
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 20, gap: 10, maxWidth: 720, width: '100%', alignSelf: 'center' },
  h1: { fontSize: 22, fontWeight: '700' },
  dim: { color: '#888', fontSize: 12 },
  section: { fontSize: 14, fontWeight: '700', color: '#333', marginTop: 8 },
  kindRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 999, paddingVertical: 6, paddingHorizontal: 12 },
  chipActive: { backgroundColor: '#2563eb', borderColor: '#2563eb' },
  chipText: { color: '#334', fontSize: 13 },
  chipTextActive: { color: '#fff', fontWeight: '600' },
  textarea: { borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 10, padding: 12, fontSize: 15, minHeight: 110, textAlignVertical: 'top' },
  input: { borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 10, padding: 12, fontSize: 15 },
  button: { backgroundColor: '#2563eb', borderRadius: 10, padding: 14, alignItems: 'center', marginTop: 12 },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  note: { color: '#b45309', fontSize: 13, marginTop: 8 },
  results: { gap: 10, marginTop: 8 },
  card: { borderWidth: 1, borderColor: '#e2e6ef', borderRadius: 12, padding: 14, gap: 4, backgroundColor: '#fafbff' },
  cardHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 },
  cardName: { fontSize: 16, fontWeight: '700', flex: 1 },
  badge: { fontSize: 11, fontWeight: '700', paddingVertical: 3, paddingHorizontal: 8, borderRadius: 999, overflow: 'hidden' },
  badgeStrong: { backgroundColor: '#dcfce7', color: '#166534' },
  badgeLook: { backgroundColor: '#eef0fb', color: '#4a5568' },
  cardOrg: { color: '#556', fontSize: 13 },
  cardReason: { color: '#1a2540', fontSize: 14, fontStyle: 'italic' },
  cardSummary: { color: '#667', fontSize: 13 },
  cardActions: { flexDirection: 'row', gap: 8, marginTop: 8 },
  cardBtn: { borderWidth: 1, borderColor: '#2563eb', borderRadius: 8, paddingVertical: 6, paddingHorizontal: 12 },
  cardBtnText: { color: '#2563eb', fontWeight: '600', fontSize: 13 },
  cardBtnDone: { borderColor: '#16a34a', backgroundColor: '#dcfce7' },
  cardBtnDoneText: { color: '#166534' },
});
