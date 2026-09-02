// Golden-dataset matching harness.
//
// Runs each profile through the FULL LIVE suggest pipeline exactly as the finder does:
//   1. POST /api/match  — server-side semantic recall (embeds the chosen theme(s),
//      cosine-recalls the catalog by match_vector, drops verified-ineligible rows),
//      returning the whole scored pool. (matching.py)
//   2. rankCandidates() — the REAL frontend function (src/lib/ranking.ts), imported here
//      so the "why it fits" prompt is byte-identical to production — over the top 12 rows,
//      producing the second-person reason + strong/look tier. (finder.tsx callMatchMapped)
//   3. Sort strong-before-look (finder.tsx sortedResults), take the top 10.
//
// "Assume a value at the profile theme selections stage": each profile carries one chosen
// theme {theme,intent,nextSteps} — the value the theme picker would have produced/selected.
//
// Output: eval/golden_matches.csv (one row per result) + eval/golden_run_summary.csv.
//
// Env: WINGMAN_TOKEN (bearer), WINGMAN_API_BASE (default http://127.0.0.1:8000).

import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { rankCandidates } from '../frontend/src/lib/ranking.ts';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.WINGMAN_API_BASE || 'http://127.0.0.1:8000';
const TOKEN = process.env.WINGMAN_TOKEN;
if (!TOKEN) { console.error('WINGMAN_TOKEN not set'); process.exit(1); }

const REASON_TOP_N = 12; // finder.tsx

// callGemini shim: identical contract to httpClient.callGemini (POST /api/messages,
// clean the text blocks). rankCandidates calls this via callGeminiJSON.
async function callGemini(system, userContent, useWebSearch = false, maxTokens) {
  const body = { system, userContent, useWebSearch };
  if (maxTokens) body.maxTokens = maxTokens;
  const r = await fetch(`${BASE}/api/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`/api/messages ${r.status}: ${await r.text()}`);
  const data = await r.json();
  const clean = (data.content ?? [])
    .filter((b) => b.type === 'text').map((b) => b.text ?? '').join('\n')
    .replace(/```json|```/g, '').trim();
  if (!clean) throw new Error('Empty response from API');
  return clean;
}

async function callMatch(blob) {
  const r = await fetch(`${BASE}/api/match`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify(blob),
  });
  if (!r.ok) throw new Error(`/api/match ${r.status}: ${await r.text()}`);
  return r.json();
}

// The 10 golden profiles + their ASSUMED chosen theme (theme-selection stage value).
const PROFILES = [
  { id: 'P01', persona: 'CS / AI app builder', grade: 11, state: 'California',
    theme: 'Building software products that help students',
    intent: 'Take my ADHD-focused app Adio from beta to real adoption and pitch it for funding',
    nextSteps: ['reach real users', 'get into an accelerator or pitch competition', 'raise funding'] },
  { id: 'P02', persona: 'Computational linguistics researcher', grade: 12, state: 'Massachusetts',
    theme: 'Computational linguistics research and problem-solving',
    intent: 'Publish my grapheme-to-phoneme research and compete in linguistics olympiads',
    nextSteps: ['find a venue to publish or present', 'train sequence models', 'prepare for NACLO'] },
  { id: 'P03', persona: 'Debate & policy', grade: 10, state: 'Texas',
    theme: 'Competitive debate and public policy',
    intent: 'Sharpen my argumentation and explore pre-law and political science',
    nextSteps: ['attend a debate summer program', 'compete at a higher level', 'study law and policy'] },
  { id: 'P04', persona: 'Journalism & creative writing', grade: 11, state: 'New York',
    theme: 'Journalism and editorial writing',
    intent: 'Grow my student magazine and get real editorial mentorship',
    nextSteps: ['join a summer journalism program', 'grow readership', 'get mentorship'] },
  { id: 'P05', persona: 'Environmental science & activism', grade: 11, state: 'Washington',
    theme: 'Environmental science and watershed research',
    intent: 'Publish my creek water-quality study and grow my cleanup nonprofit',
    nextSteps: ['enter a science fair', 'register a nonprofit', 'study environmental engineering'] },
  { id: 'P06', persona: 'Business & entrepreneurship', grade: 12, state: 'Illinois',
    theme: 'Entrepreneurship and running a small business',
    intent: 'Scale my stationery shop and meet other young founders',
    nextSteps: ['join a pitch competition or accelerator', 'learn to scale', 'study business or economics'] },
  { id: 'P07', persona: 'Pre-med / biology researcher', grade: 12, state: 'Ohio',
    theme: 'Biomedical research and medicine',
    intent: 'Deepen my wet-lab research toward an MD or MD-PhD',
    nextSteps: ['find a research program', 'build wet-lab skills', 'get mentorship'] },
  // Scant profiles: a bare theme, no intent, no next steps — mirrors the thin profile.
  { id: 'P08', persona: 'Scant — math only', grade: null, state: null,
    theme: 'Mathematics', intent: null, nextSteps: [] },
  { id: 'P09', persona: 'Scant — vague helper', grade: 9, state: null,
    theme: 'Medicine and helping people', intent: null, nextSteps: [] },
  { id: 'P10', persona: 'Scant — arts', grade: null, state: null,
    theme: 'Art and drawing', intent: null, nextSteps: [] },
];

function csvCell(v) {
  const s = v == null ? '' : String(v);
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function csvRow(arr) { return arr.map(csvCell).join(','); }

async function runProfile(p) {
  const themeTag = { tag: p.theme, intent: p.intent ?? undefined,
                     nextSteps: p.nextSteps && p.nextSteps.length ? p.nextSteps : undefined };
  const blob = {
    grade: p.grade,
    ...(p.state ? { location: { state: p.state } } : {}),
    profile_themes: [{ theme: p.theme, intent: p.intent ?? null,
                       next_steps: (p.nextSteps || []).join('; ') || null }],
    highlight_projects: [],
  };
  const resp = await callMatch(blob);
  const rows = resp.results || [];

  // reasonDesc = themeDesc (finder.tsx: theme.intent.nextSteps, NOT raw profile text).
  const themeDesc = [themeTag.tag, themeTag.intent || '', (themeTag.nextSteps || []).join('; ')]
    .filter(Boolean).join('. ');

  const reasons = {};
  const top = rows.slice(0, REASON_TOP_N);
  if (top.length) {
    let ranked = [];
    try {
      ranked = await rankCandidates(callGemini, themeDesc, top, null, false);
    } catch (e1) {
      await new Promise((r) => setTimeout(r, 1200));
      try { ranked = await rankCandidates(callGemini, themeDesc, top, null, false); }
      catch (e2) { console.warn(`  ${p.id} rank failed: ${e2.message}`); }
    }
    ranked.forEach((x) => { if (x && x.id) reasons[x.id] = { reason: x.reason || '', tier: x.tier === 'strong' ? 'strong' : 'look' }; });
  }

  const mapped = rows.map((row) => {
    const rz = reasons[row.id];
    return { opp: row, reason: rz?.reason ?? '', tier: rz ? rz.tier : 'look',
             score: row.score ?? null, strong: rz ? rz.tier === 'strong' : false };
  });
  // finder.tsx sortedResults: (tracked, none here) then tier strong(0) before look(1); stable.
  const tierOrder = { strong: 0, look: 1 };
  const sorted = [...mapped].sort((a, b) => tierOrder[a.tier] - tierOrder[b.tier]);
  const top10 = sorted.slice(0, 10);
  return { resp, top10, themeDesc };
}

const matchRows = [['profile_id', 'persona', 'theme_used', 'rank', 'tier', 'score',
  'opp_id', 'opp_name', 'org', 'type', 'why_it_fits', 'summary']];
const summaryRows = [['profile_id', 'persona', 'theme_used', 'pool_size',
  'excluded_ineligible', 'checked', 'embed_cost_usd', 'reasoned_count', 'strong_count', 'note']];

for (const p of PROFILES) {
  process.stdout.write(`Running ${p.id} (${p.persona})… `);
  try {
    const { resp, top10, themeDesc } = await runProfile(p);
    const strongCount = top10.filter((r) => r.tier === 'strong').length;
    const reasonedCount = top10.filter((r) => r.reason).length;
    top10.forEach((r, i) => {
      const o = r.opp;
      matchRows.push([p.id, p.persona, p.theme, i + 1, r.tier,
        r.score != null ? Number(r.score).toFixed(4) : '',
        o.id, o.name, o.org ?? '', o.type ?? '', r.reason,
        (o.summary ?? '').slice(0, 300)]);
    });
    summaryRows.push([p.id, p.persona, p.theme, resp.pool_size ?? top10.length,
      (resp.excluded_ineligible || []).length, resp.checked ?? '',
      resp.embed_cost_usd != null ? Number(resp.embed_cost_usd).toFixed(6) : '',
      reasonedCount, strongCount, resp.note ?? '']);
    console.log(`ok — pool ${resp.pool_size ?? '?'}, ${strongCount} strong / ${top10.length} shown`);
  } catch (e) {
    console.log(`FAILED: ${e.message}`);
    summaryRows.push([p.id, p.persona, p.theme, '', '', '', '', '', '', `ERROR: ${e.message}`]);
  }
}

writeFileSync(join(__dirname, 'golden_matches.csv'), matchRows.map(csvRow).join('\n') + '\n');
writeFileSync(join(__dirname, 'golden_run_summary.csv'), summaryRows.map(csvRow).join('\n') + '\n');
console.log('\nWrote eval/golden_matches.csv and eval/golden_run_summary.csv');
