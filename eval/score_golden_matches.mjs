// Score the golden matches into a labeled golden set.
//
// Adds, per result row:
//   match_verdict      good | loose | wrong   — is this opp a genuinely good match for the theme?
//   reason_quality     high | medium | low | none — specificity/usefulness of "why it fits"
//   fabrication        supported | minor_embellishment | fabricated | none
//   fab_evidence       the unsupported claim (quoted), if any
//   reason_summary_overlap  deterministic 0-1 lexical cross-check (incl. student-half; secondary)
//   judge_note         <=15-word note
//
// The verdicts are an LLM-as-judge PRE-LABEL (same live Gemini endpoint as the app) for human
// review, NOT ground truth — a model grading a sibling model is biased toward agreement, so
// treat `fabrication` especially as a triage flag to hand-check, not a final call. The
// deterministic `reason_summary_overlap` is an independent, reproducible cross-check.
//
// Fabrication is judged ONLY on the opportunity-half of the reason: the student-half (their
// project/goal) is not in the summary by design and must never count as fabrication.
//
// Env: WINGMAN_TOKEN, WINGMAN_API_BASE (default http://127.0.0.1:8000).

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.WINGMAN_API_BASE || 'http://127.0.0.1:8000';
let TOKEN = process.env.WINGMAN_TOKEN;
let REFRESH = process.env.WINGMAN_REFRESH;
if (!TOKEN) { console.error('WINGMAN_TOKEN not set'); process.exit(1); }

// Refresh the short-lived access token on a 401 so a long scoring run doesn't die partway.
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
  return r;
}

// ---- minimal RFC4180 CSV parse/serialize ----
function parseCSV(text) {
  const rows = [];
  let row = [], field = '', i = 0, inQ = false;
  while (i < text.length) {
    const c = text[i];
    if (inQ) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i += 2; continue; } inQ = false; i++; continue; }
      field += c; i++; continue;
    }
    if (c === '"') { inQ = true; i++; continue; }
    if (c === ',') { row.push(field); field = ''; i++; continue; }
    if (c === '\r') { i++; continue; }
    if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; i++; continue; }
    field += c; i++;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.length > 1 || (r.length === 1 && r[0] !== ''));
}
function csvCell(v) {
  const s = v == null ? '' : String(v);
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

// ---- deterministic lexical overlap (secondary signal) ----
const GENERIC = new Set(('a an the and or but of to in on for with is are was were be been being it its this that these those ' +
  'you your you\'re i my me we our as at by from into about also can will would could should have has had not no so if than then which who what when where how more most some such just like using use used your youre student students program opportunity').split(/\s+/));
function overlap(reason, name, summary) {
  const toks = (reason || '').toLowerCase().match(/[a-z0-9']+/g) || [];
  const content = [...new Set(toks.filter((t) => t.length >= 3 && !GENERIC.has(t)))];
  if (!content.length) return '';
  const hay = `${name} ${summary}`.toLowerCase();
  const hit = content.filter((t) => hay.includes(t)).length;
  return (hit / content.length).toFixed(2);
}

async function judgeBatch(themeUsed, rows) {
  const items = rows.map((r) => ({ id: r.opp_id, name: r.opp_name, org: r.org, type: r.type,
    summary: r.summary, why_it_fits: r.why_it_fits }));
  const system = `You are a strict evaluator of a high-school opportunity recommender. For each item you get the student's chosen THEME, an opportunity (name, org, type, summary) that was recommended, and the "why it fits" blurb the app wrote. Judge three things, returning ONLY a raw JSON array (no markdown), one object per item in the SAME order:
{"id","match_verdict","reason_quality","fabrication","fab_evidence","note"}

match_verdict — is this opportunity a genuinely good match for a student whose theme is the one given?
  "good"  = squarely on-theme and actionable (e.g. theme "Competitive debate" -> a national debate tournament).
  "loose" = adjacent but off-target (e.g. theme "Competitive debate" -> a space-policy debate, or a quiz-bowl championship).
  "wrong" = mismatched, defunct, or not a real student opportunity (e.g. theme "Art and drawing" -> a baseball/softball athletics camp; or a summary saying the program is DISCONTINUED/CEASED; or a row that is tournament-management SOFTWARE, not a program a student joins).

reason_quality — how specific and useful is the "why it fits" blurb?
  "high"   = names a concrete thing about the student AND a concrete thing the opp offers (e.g. "You want to publish your creek study -> this peer-reviewed journal takes original high-school research").
  "medium" = on-topic but generic on one half.
  "low"    = vague filler that could apply to anyone ("a great chance to learn and grow").
  "none"   = the blurb is empty.

fabrication — does the blurb assert a fact ABOUT THE OPPORTUNITY that is NOT supported by its name/summary? Judge ONLY the opportunity-half. The student-half (their project, goal, next steps) is NOT in the summary by design and must NEVER be counted as fabrication.
  "supported"           = every opportunity-fact stated is in the name/summary.
  "minor_embellishment" = a plausible but unstated detail (e.g. summary says "urban areas", blurb says "Chesapeake Bay Watershed").
  "fabricated"          = a specific invented claim the summary contradicts or never supports (e.g. names a mentor, prize amount, or partner the summary never states).
  "none"                = the blurb is empty.
fab_evidence — if not "supported"/"none", quote the exact unsupported phrase from the blurb; else "".
note — <=15 words, the single most useful observation.`;
  const userContent = `THEME: ${themeUsed}\n\nITEMS (JSON):\n${JSON.stringify(items)}\n\nReturn the JSON array, one object per item, same order.`;
  const body = { system, userContent, useWebSearch: false, maxTokens: 6000 };
  const resp = await authedPost('/api/messages', body);
  if (!resp.ok) throw new Error(`/api/messages ${resp.status}: ${await resp.text()}`);
  const data = await resp.json();
  const clean = (data.content ?? []).filter((b) => b.type === 'text').map((b) => b.text ?? '')
    .join('\n').replace(/```json|```/g, '').trim();
  const start = clean.indexOf('['), end = clean.lastIndexOf(']');
  const arr = JSON.parse(clean.slice(start, end + 1));
  return arr;
}

// ---- main ----
const src = parseCSV(readFileSync(join(__dirname, 'golden_matches.csv'), 'utf-8'));
const header = src[0];
const idx = Object.fromEntries(header.map((h, i) => [h, i]));
const dataRows = src.slice(1).map((r) => ({
  profile_id: r[idx.profile_id], persona: r[idx.persona], theme_used: r[idx.theme_used],
  rank: r[idx.rank], tier: r[idx.tier], score: r[idx.score], opp_id: r[idx.opp_id],
  opp_name: r[idx.opp_name], org: r[idx.org], type: r[idx.type],
  why_it_fits: r[idx.why_it_fits], summary: r[idx.summary],
}));

// group by profile
const groups = new Map();
for (const r of dataRows) { if (!groups.has(r.profile_id)) groups.set(r.profile_id, []); groups.get(r.profile_id).push(r); }

const verdicts = new Map(); // opp key -> judge object
for (const [pid, rows] of groups) {
  process.stdout.write(`Judging ${pid} (${rows.length} rows)… `);
  try {
    let arr = await judgeBatch(rows[0].theme_used, rows);
    // align by id where possible, else by order
    const byId = new Map(arr.filter((x) => x && x.id).map((x) => [String(x.id), x]));
    rows.forEach((r, i) => {
      const v = byId.get(String(r.opp_id)) || arr[i] || {};
      verdicts.set(`${pid}::${r.opp_id}::${r.rank}`, v);
    });
    console.log('ok');
  } catch (e) {
    console.log(`FAILED: ${e.message}`);
    rows.forEach((r) => verdicts.set(`${pid}::${r.opp_id}::${r.rank}`, { note: `judge error: ${e.message}` }));
  }
}

const outHeader = [...header, 'match_verdict', 'reason_quality', 'fabrication', 'fab_evidence',
  'reason_summary_overlap', 'judge_note'];
const outRows = [outHeader];
for (const r of dataRows) {
  const v = verdicts.get(`${r.profile_id}::${r.opp_id}::${r.rank}`) || {};
  const blank = !r.why_it_fits || !r.why_it_fits.trim();
  outRows.push([
    r.profile_id, r.persona, r.theme_used, r.rank, r.tier, r.score, r.opp_id, r.opp_name,
    r.org, r.type, r.why_it_fits, r.summary,
    v.match_verdict ?? '', blank ? 'none' : (v.reason_quality ?? ''),
    blank ? 'none' : (v.fabrication ?? ''), blank ? '' : (v.fab_evidence ?? ''),
    blank ? '' : overlap(r.why_it_fits, r.opp_name, r.summary), v.note ?? '',
  ]);
}
writeFileSync(join(__dirname, 'golden_matches_scored.csv'),
  outRows.map((row) => row.map(csvCell).join(',')).join('\n') + '\n');

// quick tallies
function tally(col) {
  const m = {};
  outRows.slice(1).forEach((r) => { const k = r[outHeader.indexOf(col)] || '(blank)'; m[k] = (m[k] || 0) + 1; });
  return m;
}
console.log('\nWrote eval/golden_matches_scored.csv');
console.log('match_verdict:', tally('match_verdict'));
console.log('reason_quality:', tally('reason_quality'));
console.log('fabrication:', tally('fabrication'));
