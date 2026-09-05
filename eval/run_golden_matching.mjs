// Golden-dataset matching harness.
//
// Runs each profile through the FULL LIVE suggest pipeline exactly as the finder does:
//   1. POST /api/match  — server-side semantic recall (embeds the chosen theme(s),
//      cosine-recalls the catalog by match_vector, drops verified-ineligible rows),
//      returning the whole scored pool. (matching.py)
//   2. rankCandidates() — the REAL frontend function (src/lib/ranking.ts), imported here
//      so the pipeline is byte-identical to production (the PROMPT itself is
//      server-side as of S1-1, which makes this stricter still) — over the top 12 rows,
//      producing the second-person reason + strong/look tier. (finder.tsx callMatchMapped)
//   3. CURATE to reranker-vouched rows: keep ONLY rows the reranker wrote a blurb for, exactly
//      as finder.tsx callMatchMapped does (commit 6e49034) — with the same guard that falls back
//      to the full pool if NOTHING got a reason. (MARQUEE M10.)
//   4. Sort strong-before-look (finder.tsx sortedResults), take the top 10.
//
// "Assume a value at the profile theme selections stage": each profile carries one chosen
// theme {theme,intent,nextSteps} — the value the theme picker would have produced/selected.
//
// Output: eval/golden_matches.csv (one row per result) + eval/golden_run_summary.csv.
//
// Env: WINGMAN_TOKEN (bearer), WINGMAN_API_BASE (default http://127.0.0.1:8000).

import { writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { rankCandidates } from '../frontend/src/lib/ranking.ts';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.WINGMAN_API_BASE || 'http://127.0.0.1:8000';
let TOKEN = process.env.WINGMAN_TOKEN;
let REFRESH = process.env.WINGMAN_REFRESH;
if (!TOKEN) { console.error('WINGMAN_TOKEN not set'); process.exit(1); }

const REASON_TOP_N = 12; // finder.tsx

// The access token is short-lived; a 50-profile run outlives it. Refresh with the refresh
// token on a 401 and retry once, so the whole run doesn't die partway through.
async function refreshToken() {
  if (!REFRESH) throw new Error('access token expired and no WINGMAN_REFRESH provided');
  const r = await fetch(`${BASE}/api/auth/refresh`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: REFRESH }),
  });
  if (!r.ok) throw new Error(`token refresh failed ${r.status}: ${await r.text()}`);
  const d = await r.json();
  TOKEN = d.token || d.accessToken;
  REFRESH = d.refresh_token || d.refreshToken || REFRESH;
}
async function authedPost(path, body) {
  const opts = () => ({ method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify(body) });
  let r = await fetch(`${BASE}${path}`, opts());
  if (r.status === 401) { await refreshToken(); r = await fetch(`${BASE}${path}`, opts()); }
  if (!r.ok) throw new Error(`${path} ${r.status}: ${await r.text()}`);
  return r;
}

// callFeature shim: identical contract to httpClient.callFeature (POST /api/ai, clean the
// text blocks). rankCandidates calls this via callFeatureJSON.
//
// S1-1 moved the prompt server-side, which makes this harness MORE faithful, not less: the
// grader used to import the prompt from src/lib/ranking.ts and post it to a dumb pipe, so
// it graded whatever that file said. It now names the same feature id production names, so
// it is graded against the prompt production actually sends.
async function callFeature(feature, inputs) {
  const data = await (await authedPost('/api/ai', { feature, inputs })).json();
  const clean = (data.content ?? [])
    .filter((b) => b.type === 'text').map((b) => b.text ?? '').join('\n')
    .replace(/```json|```/g, '').trim();
  if (!clean) throw new Error('Empty response from API');
  return { text: clean, truncated: data.stop_reason === 'max_tokens' };
}

async function callMatch(blob) {
  return (await authedPost('/api/match', blob)).json();
}

// The golden profiles + their ASSUMED chosen theme (theme-selection stage value), loaded from
// the single source of truth (eval/golden_profiles.json, written by gen_golden_profiles.py).
function parseGrade(g) {
  const m = String(g || '').match(/(\d{1,2})/);
  return m ? parseInt(m[1], 10) : null;
}
const PROFILES = JSON.parse(readFileSync(join(__dirname, 'golden_profiles.json'), 'utf-8'))
  .map((p) => ({
    id: p.id, persona: p.persona,
    grade: parseGrade(p.grade), state: p.state || null,
    theme: p.search_theme, intent: p.intent ?? null, nextSteps: p.next_steps || [],
  }));

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
      ranked = await rankCandidates(callFeature, themeDesc, top, null, false);
    } catch (e1) {
      await new Promise((r) => setTimeout(r, 1200));
      try { ranked = await rankCandidates(callFeature, themeDesc, top, null, false); }
      catch (e2) { console.warn(`  ${p.id} rank failed: ${e2.message}`); }
    }
    ranked.forEach((x) => { if (x && x.id) reasons[x.id] = { reason: x.reason || '', tier: x.tier === 'strong' ? 'strong' : 'look' }; });
  }

  const mapped = rows.map((row) => {
    const rz = reasons[row.id];
    return { opp: row, reason: rz?.reason ?? '', tier: rz ? rz.tier : 'look',
             score: row.score ?? null, strong: rz ? rz.tier === 'strong' : false };
  });
  // MARQUEE M10: CURATION — mirror finder.tsx callMatchMapped (commit 6e49034). The reranker
  // writes a "why it fits" ONLY for rows it genuinely vouches for; every blank-blurb row is
  // padding pulled up from cosine recall to fill the grid, and the app DROPS those. Keep this
  // byte-aligned with the finder so the scorecard measures what students actually see. Same
  // guard: if NOTHING got a reason, fall back to the full pool rather than emptying the page.
  const vouched = mapped.filter((m) => m.reason.trim());
  const curated = vouched.length ? vouched : mapped;
  // finder.tsx sortedResults: (tracked, none here) then tier strong(0) before look(1); stable.
  const tierOrder = { strong: 0, look: 1 };
  const sorted = [...curated].sort((a, b) => tierOrder[a.tier] - tierOrder[b.tier]);
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
