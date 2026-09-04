import argparse, csv, datetime, json, os

ROOT = os.path.dirname(os.path.abspath(__file__))

def read(name):
    path = name if os.path.isabs(name) else os.path.join(ROOT, name)
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))

# A scorecard is built for ONE run. `--run` is the run id (a date) that names the dated
# output page /evals serves per run; the canonical golden_scorecard.html is also refreshed
# so the eval-level "latest" link keeps working. Per-run input CSVs let us rebuild an older
# run's page from its backed-up files (golden_*_<run>.csv).
_ap = argparse.ArgumentParser(description="Build the Match Quality Scorecard for one run.")
_ap.add_argument("--run", default=datetime.date.today().isoformat(),
                 help="Run id / date (YYYY-MM-DD). Names the dated output page. Default: today.")
_ap.add_argument("--label", default="", help="Optional short run label shown in the header badge.")
_ap.add_argument("--scored", default="golden_matches_scored.csv",
                 help="Scored matches CSV for this run.")
_ap.add_argument("--summary", default="golden_run_summary.csv",
                 help="Run summary CSV for this run.")
_ap.add_argument("--no-canonical", action="store_true",
                 help="Write only the dated page, not golden_scorecard.html (use when rebuilding an OLD run).")
_args = _ap.parse_args()
RUN_ID = _args.run

profiles = read("golden_profiles.csv")
summary = {r["profile_id"]: r for r in read(_args.summary)}
matches = read(_args.scored)

by_pid = {}
for m in matches:
    by_pid.setdefault(m["profile_id"], []).append(m)

data = []
for p in profiles:
    pid = p["id"]
    s = summary.get(pid, {})
    data.append({
        "id": pid, "persona": p["persona"], "theme": p["theme"],
        "detail": p["detail_level"], "passion": p["has_passion_project"],
        "research": p["has_research_project"], "grade": p["grade"],
        "state": p["state"], "gender": p["gender"], "profile_text": p["profile_text"],
        "pool_size": s.get("pool_size", ""), "excluded": s.get("excluded_ineligible", ""),
        "strong": s.get("strong_count", ""), "reasoned": s.get("reasoned_count", ""),
        "results": [{
            "rank": m["rank"], "tier": m["tier"], "score": m["score"],
            "name": m["opp_name"], "org": m["org"], "type": m["type"],
            "why": m["why_it_fits"], "summary": m["summary"],
            "match": m["match_verdict"], "reason_q": m["reason_quality"],
            "fab": m["fabrication"], "fab_ev": m["fab_evidence"],
            "overlap": m["reason_summary_overlap"], "note": m["judge_note"],
        } for m in by_pid.get(pid, [])],
    })

payload = json.dumps(data, ensure_ascii=False)

HTML = r"""<title>Wingman Match Scorecard</title>
<meta name="description" content="Golden-set quality review of the opportunity matcher, with match, reason and fabrication verdicts.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&family=Public+Sans:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{
  --ground:#f4f6f9; --surface:#ffffff; --surface-2:#eef1f6; --line:#dde2ea;
  --ink:#161a22; --muted:#5c6472; --faint:#8a93a2;
  --accent:#3b5bdb; --accent-soft:#e7ecfd;
  --good:#1f9d55; --good-bg:#e5f5ec;
  --warn:#b9770f; --warn-bg:#faf0dc;
  --crit:#d1435b; --crit-bg:#fbe6ea;
  --neutral:#8a93a2; --neutral-bg:#eceff3;
  --strong:#3b5bdb; --strong-bg:#e7ecfd;
  --shadow:0 1px 2px rgba(20,26,34,.04),0 4px 16px rgba(20,26,34,.06);
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --disp:"Archivo","Public Sans",system-ui,sans-serif;
  --body:"Public Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#0f1218; --surface:#171b23; --surface-2:#1e232d; --line:#2a303c;
  --ink:#e8ebf0; --muted:#9aa3b2; --faint:#6b7482;
  --accent:#6b8afd; --accent-soft:#1c2540;
  --good:#4cc98a; --good-bg:#12291d;
  --warn:#e0a542; --warn-bg:#2e2412;
  --crit:#f0748c; --crit-bg:#301820;
  --neutral:#8a93a2; --neutral-bg:#232833;
  --strong:#6b8afd; --strong-bg:#1c2540;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 20px rgba(0,0,0,.35);
}}
:root[data-theme="dark"]{
  --ground:#0f1218; --surface:#171b23; --surface-2:#1e232d; --line:#2a303c;
  --ink:#e8ebf0; --muted:#9aa3b2; --faint:#6b7482;
  --accent:#6b8afd; --accent-soft:#1c2540;
  --good:#4cc98a; --good-bg:#12291d;
  --warn:#e0a542; --warn-bg:#2e2412;
  --crit:#f0748c; --crit-bg:#301820;
  --neutral:#8a93a2; --neutral-bg:#232833;
  --strong:#6b8afd; --strong-bg:#1c2540;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 20px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--body);line-height:1.5;
  font-size:15px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:34px 22px 80px}
a{color:var(--accent)}
h1,h2,h3{font-family:var(--disp);margin:0}
.eyebrow{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--accent);font-weight:600}
header .eyebrow{color:var(--faint)}
h1{font-size:32px;font-weight:800;letter-spacing:-.02em;margin:6px 0 8px;text-wrap:balance}
.lede{color:var(--muted);max-width:70ch;font-size:15.5px}
.meta-line{font-family:var(--mono);font-size:12px;color:var(--faint);margin-top:14px;
  display:flex;flex-wrap:wrap;gap:6px 18px}
.rule{height:1px;background:var(--line);border:0;margin:26px 0}

/* KPI strip */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:20px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 16px 14px;
  box-shadow:var(--shadow)}
.kpi .n{font-family:var(--disp);font-size:30px;font-weight:800;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;line-height:1}
.kpi .k{font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--faint);margin-top:8px}
.kpi .sub{font-size:12.5px;color:var(--muted);margin-top:3px}

/* distribution bars */
.dists{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:8px}
.dist{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:15px 16px;
  box-shadow:var(--shadow)}
.dist h3{font-size:13px;font-weight:600;color:var(--muted);letter-spacing:.02em;
  display:flex;justify-content:space-between;align-items:baseline}
.dist h3 span{font-family:var(--mono);font-size:11px;color:var(--faint);font-weight:400}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;margin:12px 0 10px;background:var(--surface-2)}
.bar i{display:block;height:100%}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--muted)}
.legend b{font-family:var(--mono);color:var(--ink);font-weight:600}
.dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;vertical-align:middle}

/* filters */
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:22px 0 8px}
.filters .lbl{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--faint);margin-right:4px}
.chip{font-family:var(--mono);font-size:12px;padding:6px 12px;border-radius:20px;cursor:pointer;
  border:1px solid var(--line);background:var(--surface);color:var(--muted);transition:.12s}
.chip:hover{border-color:var(--accent);color:var(--ink)}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.count-note{font-size:12.5px;color:var(--faint);margin-left:auto;font-family:var(--mono)}

/* profile block */
.profile{background:var(--surface);border:1px solid var(--line);border-radius:14px;
  box-shadow:var(--shadow);margin-top:18px;overflow:hidden}
.phead{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:14px 20px;padding:18px 20px;
  border-bottom:1px solid var(--line);background:linear-gradient(180deg,var(--surface),var(--surface))}
.phead .pid{font-family:var(--mono);font-size:12px;color:var(--accent);font-weight:600}
.phead h2{font-size:19px;font-weight:700;letter-spacing:-.01em;margin:2px 0 3px}
.phead .theme{color:var(--muted);font-size:14px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.tag{font-family:var(--mono);font-size:11px;padding:3px 9px;border-radius:6px;background:var(--surface-2);
  color:var(--muted);border:1px solid var(--line)}
.tag.on{background:var(--accent-soft);color:var(--accent);border-color:transparent}
.tag.off{opacity:.6}
.pstats{display:flex;gap:18px;text-align:right;align-self:start}
.pstats .st .v{font-family:var(--disp);font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.pstats .st .l{font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--faint)}
.ptext{grid-column:1/-1;font-size:13.5px;color:var(--muted);white-space:pre-wrap;
  background:var(--surface-2);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
  max-width:74ch}
.ptext.scant{color:var(--faint);font-style:italic}

/* results table */
.tscroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px}
thead th{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--faint);text-align:left;font-weight:500;padding:10px 12px;border-bottom:1px solid var(--line);
  position:sticky;top:0;background:var(--surface);white-space:nowrap}
tbody td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr.flag-warn{box-shadow:inset 3px 0 0 var(--warn)}
tbody tr.flag-crit{box-shadow:inset 3px 0 0 var(--crit)}
tbody tr.hide{display:none}
.rk{font-family:var(--mono);color:var(--faint);font-variant-numeric:tabular-nums}
.sc{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--muted);font-size:12.5px}
.oname{font-weight:600;color:var(--ink)}
.oorg{color:var(--faint);font-size:12px;margin-top:2px}
.why{color:var(--muted);max-width:42ch}
.why.empty{color:var(--faint);font-style:italic}
.fabev{color:var(--crit);font-size:12px;margin-top:5px;font-family:var(--mono)}
.fabev.warn{color:var(--warn)}
.jn{color:var(--faint);font-size:12px;margin-top:5px}
.vc{display:flex;flex-direction:column;gap:5px;min-width:96px}
.pill{font-family:var(--mono);font-size:10.5px;font-weight:600;letter-spacing:.02em;
  padding:3px 8px;border-radius:20px;white-space:nowrap;display:inline-flex;align-items:center;gap:5px;
  align-self:flex-start}
.pill .d{width:6px;height:6px;border-radius:50%}
.p-good{background:var(--good-bg);color:var(--good)} .p-good .d{background:var(--good)}
.p-warn{background:var(--warn-bg);color:var(--warn)} .p-warn .d{background:var(--warn)}
.p-crit{background:var(--crit-bg);color:var(--crit)} .p-crit .d{background:var(--crit)}
.p-neutral{background:var(--neutral-bg);color:var(--neutral)} .p-neutral .d{background:var(--neutral)}
.tier{font-family:var(--mono);font-size:10px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
  padding:2px 7px;border-radius:5px}
.tier.strong{background:var(--strong-bg);color:var(--strong)}
.tier.look{background:var(--surface-2);color:var(--faint);border:1px solid var(--line)}
.pnote{font-size:12px;color:var(--faint);padding:10px 20px;background:var(--surface-2);
  border-top:1px solid var(--line);font-style:italic}
footer{margin-top:40px;padding-top:20px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--faint);max-width:78ch}
footer code{font-family:var(--mono);background:var(--surface-2);padding:1px 5px;border-radius:4px}
@media (max-width:640px){
  .phead{grid-template-columns:1fr}.pstats{text-align:left}
  h1{font-size:26px}
}
</style>

<div class="wrap">
  <header>
    <div class="eyebrow">Highschool Wingman · Matching eval</div>
    <h1>Match Quality Scorecard</h1>
    <p class="lede">Golden-set student profiles run through the live suggest pipeline —
      semantic recall (<code style="font-family:var(--mono);font-size:.85em">/api/match</code>)
      then the production <b>rankCandidates</b> reasoner — graded for match relevance, reason
      quality, and fabrication against each opportunity's own text.</p>
    <div class="meta-line">
      <span style="color:var(--accent);font-weight:600">Run __RUNBADGE__</span>
      <span id="mProfiles"></span><span id="mResults"></span>
      <span>LLM-judge pre-labels · human-overridable</span>
      <span><a href="/evals" style="font-family:var(--mono)">← Evals hub</a></span>
    </div>
  </header>
  <hr class="rule">
  <div class="kpis" id="kpis"></div>
  <div class="dists" id="dists"></div>
  <div class="filters" id="filters">
    <span class="lbl">Show</span>
  </div>
  <div id="profiles"></div>
  <footer>
    <b>How to read this.</b> Verdicts are an <b>LLM-as-judge pre-label</b> produced on the same
    live Gemini endpoint the app uses — a model grading a sibling model skews toward agreement,
    so treat them as triage, not ground truth, and override in the CSV. <b>Fabrication</b> is
    judged only on the opportunity-half of the reason (the student-half is not in the summary by
    design). <code>overlap</code> is an independent deterministic lexical cross-check: the share
    of a reason's content words found in the opportunity's name/summary — low overlap next to a
    fabrication flag is the strongest signal to hand-check. Blank reasons are <code>look</code>-tier
    rows the ranker deliberately declined to reason.
  </footer>
</div>

<script>
const DATA = __PAYLOAD__;

const MATCH_C={good:'good',loose:'warn',wrong:'crit'};
const REASON_C={high:'good',medium:'warn',low:'crit',none:'neutral'};
const FAB_C={supported:'good',minor_embellishment:'warn',fabricated:'crit',none:'neutral'};
const SEM={good:'var(--good)',warn:'var(--warn)',crit:'var(--crit)',neutral:'var(--neutral)'};

const rows=DATA.flatMap(p=>p.results);
const n=rows.length;
function tally(key){const m={};rows.forEach(r=>{const k=r[key]||'—';m[k]=(m[k]||0)+1});return m}
const mv=tally('match'),rq=tally('reason_q'),fb=tally('fab');
const totalStrong=rows.filter(r=>r.tier==='strong').length;
const fabricated=fb.fabricated||0, minor=fb.minor_embellishment||0;
const wrong=mv.wrong||0, loose=mv.loose||0, good=mv.good||0;
const blanks=rows.filter(r=>!r.why||!r.why.trim()).length;

// ---- KPIs ----
function kpi(nStr,k,sub){return `<div class="kpi"><div class="n">${nStr}</div><div class="k">${k}</div>${sub?`<div class="sub">${sub}</div>`:''}</div>`}
document.getElementById('kpis').innerHTML=[
  kpi(good+'<span style="font-size:16px;color:var(--faint)">/'+n+'</span>','Good matches',Math.round(good/n*100)+'% squarely on-theme'),
  kpi(totalStrong,'Strong-tier',totalStrong+' of '+n+' reasoned as strong fit'),
  kpi((rq.high||0),'High-quality reasons','of '+(n-blanks)+' reasoned rows'),
  kpi(fabricated,'Fabricated','+'+minor+' minor embellishment'),
  kpi(loose+wrong,'Flagged matches',loose+' loose · '+wrong+' wrong'),
].join('');

// ---- distributions ----
function distBar(title,counts,order,colorMap){
  const total=order.reduce((s,k)=>s+(counts[k]||0),0)||1;
  const segs=order.map(k=>counts[k]?`<i style="width:${(counts[k]/total*100).toFixed(2)}%;background:${SEM[colorMap[k]]}"></i>`:'').join('');
  const leg=order.filter(k=>counts[k]).map(k=>`<span><span class="dot" style="background:${SEM[colorMap[k]]}"></span>${k.replace(/_/g,' ')} <b>${counts[k]}</b></span>`).join('');
  return `<div class="dist"><h3>${title}<span>n=${total}</span></h3><div class="bar">${segs}</div><div class="legend">${leg}</div></div>`;
}
document.getElementById('dists').innerHTML=[
  distBar('Match verdict',mv,['good','loose','wrong'],MATCH_C),
  distBar('Reason quality',rq,['high','medium','low','none'],REASON_C),
  distBar('Fabrication',fb,['supported','minor_embellishment','fabricated','none'],FAB_C),
].join('');

// ---- filters ----
const FILTERS={
  all:{label:'All results',fn:()=>true},
  flagged:{label:'Flagged',fn:r=>r.match!=='good'||(r.fab!=='supported'&&r.fab!=='none')},
  wrong:{label:'Wrong match',fn:r=>r.match==='wrong'},
  loose:{label:'Loose match',fn:r=>r.match==='loose'},
  fab:{label:'Fabrication',fn:r=>r.fab==='fabricated'||r.fab==='minor_embellishment'},
  blank:{label:'Blank reason',fn:r=>!r.why||!r.why.trim()},
};
let active='all';
const fbox=document.getElementById('filters');
Object.entries(FILTERS).forEach(([k,f])=>{
  const b=document.createElement('button');
  b.className='chip';b.textContent=f.label;b.setAttribute('aria-pressed',k==='all');
  b.onclick=()=>{active=k;[...fbox.querySelectorAll('.chip')].forEach((c,i)=>c.setAttribute('aria-pressed',Object.keys(FILTERS)[i]===k));render()};
  fbox.appendChild(b);
});
const cnote=document.createElement('span');cnote.className='count-note';fbox.appendChild(cnote);

// ---- pills ----
function pill(text,cls){return `<span class="pill p-${cls}"><span class="d"></span>${text.replace(/_/g,' ')}</span>`}

// ---- render profiles ----
const host=document.getElementById('profiles');
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function metaChips(p){
  const c=[];
  c.push(`<span class="tag">${p.detail}</span>`);
  c.push(`<span class="tag ${p.passion==='yes'?'on':'off'}">passion project ${p.passion==='yes'?'✓':'—'}</span>`);
  c.push(`<span class="tag ${p.research==='yes'?'on':'off'}">research ${p.research==='yes'?'✓':'—'}</span>`);
  c.push(`<span class="tag">${p.grade||'grade —'}</span>`);
  c.push(`<span class="tag">${p.state||'state —'}</span>`);
  c.push(`<span class="tag">${p.gender||'gender —'}</span>`);
  return c.join('');
}
function build(){
  host.innerHTML=DATA.map(p=>{
    const rowsHtml=p.results.map(r=>{
      const blank=!r.why||!r.why.trim();
      const mc=MATCH_C[r.match]||'neutral',rc=REASON_C[r.reason_q]||'neutral',fc=FAB_C[r.fab]||'neutral';
      const flag=(r.match==='wrong'||r.fab==='fabricated')?'flag-crit':((r.match==='loose'||r.fab==='minor_embellishment')?'flag-warn':'');
      const fabEv=r.fab_ev?`<div class="fabev ${r.fab==='minor_embellishment'?'warn':''}">⚑ ${esc(r.fab_ev)}</div>`:'';
      return `<tr class="rrow ${flag}" data-pid="${p.id}"
        data-match="${r.match}" data-fab="${r.fab}" data-blank="${blank}">
        <td class="rk">${r.rank}</td>
        <td><span class="tier ${r.tier}">${r.tier}</span></td>
        <td class="sc">${r.score||''}</td>
        <td><div class="oname">${esc(r.name)}</div><div class="oorg">${esc(r.org)||'—'} · ${esc(r.type)}</div></td>
        <td><div class="why ${blank?'empty':''}">${blank?'— ranker declined to reason —':esc(r.why)}</div>${fabEv}${r.note?`<div class="jn">${esc(r.note)}</div>`:''}</td>
        <td><div class="vc">
          ${pill(r.match,mc)}
          ${pill('reason: '+r.reason_q,rc)}
          ${pill('fab: '+r.fab,fc)}
          ${!blank&&r.overlap?`<span class="pill p-neutral" title="lexical overlap with summary"><span class="d"></span>ov ${r.overlap}</span>`:''}
        </div></td>
      </tr>`;
    }).join('');
    return `<section class="profile" data-pid="${p.id}">
      <div class="phead">
        <div>
          <div class="pid">${p.id}</div>
          <h2>${esc(p.persona)}</h2>
          <div class="theme">Theme searched: <b>${esc(p.theme)}</b></div>
          <div class="tags">${metaChips(p)}</div>
        </div>
        <div class="pstats">
          <div class="st"><div class="v">${p.pool_size}</div><div class="l">Pool</div></div>
          <div class="st"><div class="v">${p.excluded}</div><div class="l">Excl.</div></div>
          <div class="st"><div class="v">${p.strong}</div><div class="l">Strong</div></div>
        </div>
        <div class="ptext ${p.detail==='scant'?'scant':''}">${esc(p.profile_text)}</div>
      </div>
      <div class="tscroll"><table>
        <thead><tr><th>#</th><th>Tier</th><th>Score</th><th>Opportunity</th><th>Why it fits</th><th>Verdicts</th></tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table></div>
    </section>`;
  }).join('');
}
function render(){
  const fn=FILTERS[active].fn;
  let shown=0;
  DATA.forEach(p=>{
    const sec=host.querySelector(`.profile[data-pid="${p.id}"]`);
    let vis=0;
    p.results.forEach((r,i)=>{
      const tr=sec.querySelectorAll('tbody tr')[i];
      const ok=fn(r);
      tr.classList.toggle('hide',!ok);
      if(ok){vis++;shown++;}
    });
    sec.style.display=vis?'':'none';
  });
  cnote.textContent=`${shown} / ${n} rows`;
}
build();render();
document.getElementById('mProfiles').textContent = DATA.length + ' profiles';
document.getElementById('mResults').textContent = n + ' ranked results';
</script>
"""

badge = RUN_ID + (f" · {_args.label}" if _args.label else "")
out = HTML.replace("__PAYLOAD__", payload).replace("__RUNBADGE__", badge)

written = []
dated = os.path.join(ROOT, f"golden_scorecard_{RUN_ID}.html")
with open(dated, "w", encoding="utf-8") as f:
    f.write(out)
written.append(dated)
if not _args.no_canonical:
    canonical = os.path.join(ROOT, "golden_scorecard.html")
    with open(canonical, "w", encoding="utf-8") as f:
        f.write(out)
    written.append(canonical)
for p in written:
    print("wrote", p, "bytes", len(out))
