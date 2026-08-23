import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { loadTrackerItems, removeTrackerItem, type TrackerItem } from '@/api/trackerStore';
import { ALL_BUCKETS, type Bucket } from '@/lib/constants';
import type { TrackerInfo } from '@/lib/tracker';

// Tracker — the items added from the Finder, grouped by bucket. Reloads on focus so a
// just-added item shows up. Each item can run the shared, cross-user-cached on-demand
// deadline check (GET /api/opportunities/<id>/deadline).
const BUCKET_LABELS: Record<Bucket, string> = {
  summerPrograms: 'Summer Programs',
  internships: 'Internships',
  researchCompetitions: 'Research & Project Competitions',
  pureCompetitions: 'Academic Competitions',
  conferences: 'Conferences',
  journals: 'Journals',
};

export default function Tracker() {
  const [items, setItems] = useState<TrackerItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deadlines, setDeadlines] = useState<Record<string, Partial<TrackerInfo> | 'loading' | 'none'>>({});

  useFocusEffect(
    useCallback(() => {
      let alive = true;
      loadTrackerItems()
        .then((rows) => alive && setItems(rows))
        .catch((e) => alive && setError((e as Error).message));
      return () => {
        alive = false;
      };
    }, []),
  );

  async function checkDeadline(oppId: string) {
    setDeadlines((d) => ({ ...d, [oppId]: 'loading' }));
    const info = await httpClient.getDeadlineCheck(oppId);
    setDeadlines((d) => ({ ...d, [oppId]: info ?? 'none' }));
  }

  async function remove(oppId: string) {
    const next = await removeTrackerItem(oppId);
    setItems(next);
  }

  if (error) return <Centered text={`Couldn't load tracker: ${error}`} />;
  if (!items) return <Centered spinner />;
  if (!items.length) {
    return <Centered text="Nothing tracked yet. Add matches from the Finder." />;
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      {ALL_BUCKETS.map((bucket) => {
        const rows = items.filter((i) => i.bucket === bucket);
        if (!rows.length) return null;
        return (
          <View key={bucket} style={styles.group}>
            <Text style={styles.groupTitle}>
              {BUCKET_LABELS[bucket]} ({rows.length})
            </Text>
            {rows.map((item) => {
              const dl = deadlines[item.oppId];
              return (
                <View key={item.oppId} style={styles.card}>
                  <Text style={styles.name}>{item.name}</Text>
                  {!!item.org && <Text style={styles.org}>{item.org}</Text>}
                  {!!item.reason && <Text style={styles.reason}>{item.reason}</Text>}

                  {dl === 'loading' && <Text style={styles.dim}>Checking deadlines…</Text>}
                  {dl === 'none' && <Text style={styles.dim}>No deadline info available.</Text>}
                  {dl && dl !== 'loading' && dl !== 'none' && (
                    <View style={styles.deadlineBox}>
                      {!!dl.status && <Text style={styles.status}>Status: {dl.status}</Text>}
                      {(dl.important_dates ?? []).map((d, i) => (
                        <Text key={i} style={styles.date}>
                          • {d.label}: {d.date_iso}
                        </Text>
                      ))}
                      {!dl.important_dates?.length && !!dl.deadline_label && (
                        <Text style={styles.date}>• {dl.deadline_label}</Text>
                      )}
                    </View>
                  )}

                  <View style={styles.actions}>
                    {!!item.url && (
                      <Pressable style={styles.btn} onPress={() => Linking.openURL(item.url as string)}>
                        <Text style={styles.btnText}>Open</Text>
                      </Pressable>
                    )}
                    <Pressable
                      style={styles.btn}
                      onPress={() => checkDeadline(item.oppId)}
                      disabled={dl === 'loading'}
                    >
                      <Text style={styles.btnText}>Check deadlines</Text>
                    </Pressable>
                    <Pressable style={styles.btn} onPress={() => remove(item.oppId)}>
                      <Text style={[styles.btnText, styles.remove]}>Remove</Text>
                    </Pressable>
                  </View>
                </View>
              );
            })}
          </View>
        );
      })}
    </ScrollView>
  );
}

function Centered({ text, spinner }: { text?: string; spinner?: boolean }) {
  return (
    <View style={styles.centered}>
      {spinner ? <ActivityIndicator /> : <Text style={styles.dim}>{text}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { padding: 20, gap: 16, maxWidth: 720, width: '100%', alignSelf: 'center' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  group: { gap: 8 },
  groupTitle: { fontSize: 16, fontWeight: '700', color: '#1a2540' },
  card: { borderWidth: 1, borderColor: '#e2e6ef', borderRadius: 12, padding: 14, gap: 4, backgroundColor: '#fafbff' },
  name: { fontSize: 16, fontWeight: '700' },
  org: { color: '#556', fontSize: 13 },
  reason: { color: '#1a2540', fontSize: 14, fontStyle: 'italic' },
  dim: { color: '#889', fontSize: 13 },
  deadlineBox: { marginTop: 6, gap: 2 },
  status: { fontSize: 13, fontWeight: '600', color: '#334' },
  date: { fontSize: 13, color: '#445' },
  actions: { flexDirection: 'row', gap: 8, marginTop: 8, flexWrap: 'wrap' },
  btn: { borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 8, paddingVertical: 6, paddingHorizontal: 12 },
  btnText: { color: '#2563eb', fontWeight: '600', fontSize: 13 },
  remove: { color: '#b91c1c' },
});
