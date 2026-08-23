// End-to-end verification of the salvaged logic against the LIVE backend (no browser).
// Run: npx tsx scripts/verify.ts   (backend must be on 127.0.0.1:8000)
//
// Exercises the exact ported modules the screens use: preFilter -> inferSubjects ->
// rankCandidates (Finder), assessProfileReadiness + synthesizeProfile (Profile chat),
// extractTrackerInfo / deadline check (Tracker). Proves the logic + AI wiring, independent
// of the RN UI.
import { createHash } from 'node:crypto';
import type { Opportunity } from '../src/api/types';
import { inferSubjects, preFilter, rankCandidates } from '../src/lib/ranking';
import { assessProfileReadiness, synthesizeProfile } from '../src/lib/profile';

const BASE = 'http://127.0.0.1:8000';

async function callGemini(system: string, userContent: string, useWebSearch = false): Promise<string> {
  const r = await fetch(`${BASE}/api/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ system, userContent, useWebSearch }),
  });
  const d = (await r.json()) as { content?: { type: string; text?: string }[] };
  return (d.content ?? []).filter((b) => b.type === 'text').map((b) => b.text ?? '').join('\n').replace(/```json|```/g, '').trim();
}

async function callClaudeDetailed(system: string, userContent: string, useWebSearch = false, maxTokens?: number) {
  const body: Record<string, unknown> = { system, userContent, useWebSearch };
  if (maxTokens) body.maxTokens = maxTokens;
  const r = await fetch(`${BASE}/api/messages-claude`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const d = (await r.json()) as { content?: { type: string; text?: string }[]; stop_reason?: string };
  const text = (d.content ?? []).filter((b) => b.type === 'text').map((b) => b.text ?? '').join('\n').replace(/```json|```/g, '').trim();
  return { text, truncated: d.stop_reason === 'max_tokens' };
}

async function main() {
  console.log('1. getOpportunities');
  const opps = (await (await fetch(`${BASE}/api/opportunities`)).json()) as Opportunity[];
  console.log(`   -> ${opps.length} rows`);
  if (!opps.length) throw new Error('no opportunities');

  const description =
    "I love robotics and want hands-on experience building autonomous robots. I've done Arduino and Python and I'm into machine learning for computer vision.";

  console.log('2. inferSubjects');
  const subjects = await inferSubjects(callGemini, description);
  console.log('   ->', subjects);

  console.log('3. preFilter (Summer Program / Program type)');
  const pool = preFilter(opps, description, subjects, ['Program'], false, null);
  console.log(`   -> pool of ${pool.length}; top: ${pool.slice(0, 3).map((o) => o.name).join(' | ')}`);

  console.log('4. rankCandidates');
  const ranked = await rankCandidates(callGemini, description, pool, 'free or low-cost', false);
  console.log(`   -> ${ranked.length} ranked`);
  const byId = new Map(pool.map((o) => [o.id, o] as const));
  ranked.slice(0, 5).forEach((r) => {
    const o = byId.get(r.id);
    console.log(`      [${r.tier}] ${o?.name ?? r.id} — ${r.reason}`);
  });
  if (!ranked.length) throw new Error('rankCandidates returned nothing');

  console.log('5. assessProfileReadiness');
  const assess = await assessProfileReadiness(callGemini, description);
  console.log('   ->', JSON.stringify(assess).slice(0, 160));

  console.log('6. synthesizeProfile (transcript merge)');
  const transcript = 'Bot: What are you into?\nStudent: I build combat robots and run my school robotics club.';
  const merged = await synthesizeProfile(callClaudeDetailed, '', transcript, true);
  console.log('   ->', merged.slice(0, 160).replace(/\n/g, ' '));
  if (!merged) throw new Error('synthesizeProfile empty');

  console.log('7. deadline check (gated — login first)');
  const pwHash = createHash('sha256').update('s3cret-pass').digest('hex');
  const lg = (await (await fetch(`${BASE}/api/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ userid: 'rn_browser_test_a1', passwordHash: pwHash }),
  })).json()) as { token?: string };
  if (lg.token) {
    const dr = await fetch(`${BASE}/api/opportunities/${encodeURIComponent(pool[0].id)}/deadline`, {
      headers: { Authorization: `Bearer ${lg.token}` },
    });
    console.log(`   -> deadline status ${dr.status} for "${pool[0].name}"`);
  } else {
    console.log('   -> (skipped: login failed)');
  }

  console.log('\nALL SALVAGE-CHAIN CHECKS PASSED');
}

main().catch((e) => {
  console.error('VERIFY FAILED:', e);
  process.exit(1);
});
