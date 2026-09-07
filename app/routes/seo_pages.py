"""Public, crawlable Layer-1 SEO pages — one server-rendered HTML page per opportunity —
plus /sitemap.xml and /robots.txt. These SHIP to production (unlike the ops console): they
exist to rank in search and pull signed-out visitors into a free trial.

Why server-rendered here and not in the Expo app: a crawler must read the content on the
first byte, and the RN-web bundle paints nothing until ~2MB of JS evaluates. So these are
plain HTML built from the catalog row the agents already maintain — no model call, no cost.

The index/noindex decision is computed LIVE from the row via wingman.seo_pages.evaluate_seo_page
(the same bar the sitemap and the admin evaluator use), so a page that has gone thin since the
last evaluation is served `noindex` immediately rather than waiting for a re-evaluation.

Routing note: app/main.py registers a catch-all `/{full_path:path}` static route LAST, so
this router (included in the normal loop) matches /opportunities/<slug>, /sitemap.xml and
/robots.txt first. /robots.txt in particular MUST be a route: the static route deny-lists
`.txt`, so public/robots.txt would 404 and fall through to the SPA shell without this.
"""
import datetime
import html
import json
import os

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.config import (SUPABASE_URL, SUPABASE_SERVICE_KEY, SEO_SITE_ORIGIN)
from app.core import _supabase_request
from wingman import REPO_ROOT
from wingman.seo_pages import evaluate_seo_page, STATUS_INDEXED

router = APIRouter()

# Everything a program page renders, plus the columns evaluate_seo_page needs for the live gate.
SEO_PAGE_FIELDS = (
    "id,name,org,summary,url,type,price,state,location,intl,season,eligibility,"
    "grade_min,grade_max,status,important_dates,was_estimated,important_date_note,"
    "action_items,action_items_source,review_summary,review_status,seo_slug"
)

# A page and the sitemap may be cached by the CDN/browser for an hour. Setting Cache-Control
# here also opts these responses out of app/main.py's blanket no-store (it defers to a route
# that made its own decision) — no-store on a crawlable page is wasteful, not wrong.
_PAGE_CACHE = "public, max-age=3600, must-revalidate"

_ROBOTS_PATH = os.path.join(REPO_ROOT, "public", "robots.txt")
# Fallback if public/robots.txt is somehow absent — never serve the SPA shell as robots.
_ROBOTS_FALLBACK = (
    "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin\n"
    f"Sitemap: {SEO_SITE_ORIGIN}/sitemap.xml\n"
)


# ----------------------------- small render helpers -----------------------------

def _esc(value):
    """HTML-escape any catalog value. Catalog text is agent/model-derived, so it is untrusted
    input and is escaped everywhere it reaches the page — the same stance the rest of the repo
    takes toward model output."""
    return html.escape("" if value is None else str(value), quote=True)


def _truncate(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _grade_band(opp):
    lo, hi = opp.get("grade_min"), opp.get("grade_max")
    if lo and hi:
        return f"Grades {lo}–{hi}"
    if lo:
        return f"Grade {lo}+"
    if hi:
        return f"Up to grade {hi}"
    return _esc(opp.get("eligibility")) and (opp.get("eligibility") or "").strip() or ""


def _fmt_date(iso):
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
        return d.strftime("%b %-d, %Y") if os.name != "nt" else d.strftime("%b %d, %Y")
    except Exception:
        return str(iso)


def _dates_rows(opp):
    """(label, formatted date, is_estimated) for each real important_dates entry, in the order
    stored. Used for both the visible facts and the JSON-LD."""
    out = []
    for d in opp.get("important_dates") or []:
        if not isinstance(d, dict) or not d.get("date_iso"):
            continue
        label = _clean(d.get("label")) or _clean(d.get("type")).replace("_", " ").title() or "Date"
        out.append((label, d["date_iso"], bool(d.get("estimated"))))
    return out


def _clean(v):
    return v.strip() if isinstance(v, str) else ""


def _checklist(opp):
    """Page-verified application steps as plain strings, in order."""
    out = []
    for it in opp.get("action_items") or []:
        if isinstance(it, dict):
            t = _clean(it.get("text")) or _clean(it.get("task"))
        else:
            t = _clean(it)
        if t:
            out.append(t)
    return out


# ----------------------------- data access -----------------------------

# Latched off the first time a select 400s because seo_slug is not migrated yet (the same
# "degrade until the DDL is run" convention as app/services/opportunities._match_vector_available
# and app/services/action_items). Once off, pages resolve by id only and render without a
# canonical slug — so /opportunities/<id> keeps working before db/seo_pages_schema.sql is run;
# the pretty slug URLs light up once it is. Re-latches on process restart.
_seo_slug_available = True


def _select(with_slug):
    fields = SEO_PAGE_FIELDS if with_slug else \
        ",".join(f for f in SEO_PAGE_FIELDS.split(",") if f != "seo_slug")
    return fields


def _fetch_by(field, value):
    global _seo_slug_available
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    rows = _supabase_request("opportunities", params={
        "select": _select(_seo_slug_available), field: f"eq.{value}",
        "is_active": "eq.true", "limit": "1",
    })
    if rows is None and _seo_slug_available:
        # Might be the un-migrated seo_slug column 400ing the whole select. Retry without it;
        # if that works, the column is the problem, so latch off and stop trying.
        retry = _supabase_request("opportunities", params={
            "select": _select(False), field: f"eq.{value}",
            "is_active": "eq.true", "limit": "1",
        })
        if retry is not None:
            _seo_slug_available = False
            rows = retry
    if not rows:
        return None
    return rows[0]


def _resolve(slug):
    """Find the active row for a URL segment. Prefer the stored seo_slug (the canonical path);
    fall back to treating the segment as a raw id so /opportunities/<id> keeps working before an
    evaluation has assigned slugs. Returns (row, canonical_slug_or_None)."""
    row = _fetch_by("seo_slug", slug)
    if row:
        return row, row.get("seo_slug")
    row = _fetch_by("id", slug)
    if row:
        return row, row.get("seo_slug")   # may be None → no redirect, render at /<id>
    return None, None


# ----------------------------- HTML -----------------------------

_PAGE_CSS = """
:root{--ink:#141d33;--muted:#5b6478;--line:#e3e6ec;--paper:#f6f7f5;--card:#fff;
--marigold:#c9790a;--teal:#0d857a;--wash:#fbf0da}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
font-family:"Plus Jakarta Sans",-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.6}
.wrap{max-width:760px;margin:0 auto;padding:0 22px}
h1{font-family:"Space Grotesk",Segoe UI,sans-serif;font-size:clamp(28px,5vw,40px);
letter-spacing:-.02em;line-height:1.15;margin:6px 0 8px}
.crumbs{font-size:12.5px;color:var(--muted);margin:26px 0 0;font-family:"Space Mono",monospace}
.crumbs a{color:var(--muted);text-decoration:none}
.sub{font-size:17px;color:#33405c;margin:0 0 22px;max-width:60ch}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:0 0 26px}
.f{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 14px}
.fl{font-family:"Space Mono",monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.fv{font-family:"Space Grotesk",sans-serif;font-weight:600;font-size:16px;margin-top:4px}
.fv .est{font-family:"Space Mono",monospace;font-size:10px;color:var(--marigold);font-weight:700;margin-left:5px}
h2{font-family:"Space Grotesk",sans-serif;font-size:20px;margin:28px 0 10px}
.chk{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:10px}
.chk li{display:grid;grid-template-columns:20px 1fr;gap:11px;align-items:start;font-size:15px}
.chk .bx{width:16px;height:16px;border:2px solid #c6ccd6;border-radius:4px;margin-top:3px}
.src{font-size:12.5px;color:var(--muted);margin-top:8px}
.cta{margin:30px 0 10px;background:var(--ink);border-radius:12px;padding:20px 22px;
display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.cta .t{flex:1;min-width:200px;color:#eef1f6}
.cta .t b{font-family:"Space Grotesk",sans-serif;font-size:16px;display:block;color:#fff}
.cta .t span{font-size:13px;color:#aeb8cc}
.cta a.btn{background:var(--marigold);color:#1a1205;font-weight:700;text-decoration:none;
font-family:"Space Grotesk",sans-serif;padding:12px 20px;border-radius:9px}
.ext{display:inline-block;margin:6px 0 0;font-size:14px;color:var(--teal)}
footer{border-top:1px solid var(--line);margin-top:36px;padding:22px 0 60px;color:var(--muted);font-size:12.5px}
@media (prefers-color-scheme:dark){:root{--ink:#eef1f6;--muted:#93a0b6;--line:#26314a;
--paper:#0d131f;--card:#161f2f;--marigold:#f0a838;--teal:#3ecabb;--wash:#2c2413}
.sub{color:#c3cbdb}}
"""


def _json_ld(opp, dates, canonical_url):
    """schema.org EducationalOccupationalProgram. Only real, present fields are emitted — an
    empty or invented property is worse than an absent one for rich results."""
    data = {
        "@context": "https://schema.org",
        "@type": "EducationalOccupationalProgram",
        "name": _clean(opp.get("name")),
        "url": canonical_url,
    }
    summary = _clean(opp.get("summary"))
    if summary:
        data["description"] = summary
    org = _clean(opp.get("org"))
    if org:
        data["provider"] = {"@type": "Organization", "name": org}
    program_url = _clean(opp.get("url"))
    if program_url:
        data["sameAs"] = program_url
    # The soonest non-estimated deadline, if any, as applicationDeadline.
    for label, iso, estimated in dates:
        if not estimated and "deadline" in label.lower():
            data["applicationDeadline"] = str(iso)[:10]
            break
    # Escaping </ inside a JSON-LD block prevents a "</script>" in any field breaking out.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _render_page(opp, canonical_slug):
    verdict = evaluate_seo_page(opp)             # LIVE gate — authoritative for index/noindex
    name = _clean(opp.get("name")) or "Opportunity"
    org = _clean(opp.get("org"))
    summary = _clean(opp.get("summary"))
    program_url = _clean(opp.get("url"))
    dates = _dates_rows(opp)

    path = f"/opportunities/{canonical_slug}" if canonical_slug else f"/opportunities/{opp.get('id')}"
    canonical_url = f"{SEO_SITE_ORIGIN}{path}"

    # <title> pattern: the name is the query people type; the suffix earns the click.
    year = datetime.date.today().year
    title = f"{name} — Deadlines, Eligibility & How to Apply ({year}) | Highschool Wingman"
    meta_desc = _truncate(summary or f"How to apply to {name}, with deadlines and eligibility.", 155)
    robots = "index,follow" if verdict["indexable"] else "noindex,follow"

    facts = []
    deadline_fact = next(((l, i, e) for (l, i, e) in dates if "deadline" in l.lower()), None)
    if deadline_fact:
        l, i, e = deadline_fact
        est = '<span class="est">(estimated)</span>' if e else ""
        facts.append(("Deadline", f"{_esc(_fmt_date(i))}{est}"))
    if _clean(opp.get("price")):
        facts.append(("Cost", _esc(opp.get("price"))))
    band = _grade_band(opp)
    if band:
        facts.append(("Eligibility", _esc(band)))
    fmt = _clean(opp.get("type")) or _clean(opp.get("location")) or _clean(opp.get("state"))
    if fmt:
        loc = _clean(opp.get("location")) or _clean(opp.get("state"))
        facts.append(("Format", _esc(fmt + (f" · {loc}" if loc and loc != fmt else ""))))

    facts_html = "".join(
        f'<div class="f"><div class="fl">{_esc(fl)}</div><div class="fv">{fv}</div></div>'
        for fl, fv in facts
    )

    checklist = _checklist(opp)
    checklist_html = ""
    if checklist:
        page_backed = (opp.get("action_items_source") or "").startswith("page")
        src = ('<p class="src">From the program’s own page — verified, not invented.</p>'
               if page_backed else
               '<p class="src">Typical steps — confirm the details on the program’s site.</p>')
        items = "".join(f'<li><span class="bx"></span><div>{_esc(t)}</div></li>' for t in checklist)
        checklist_html = f'<h2>How to apply</h2><ul class="chk">{items}</ul>{src}'

    other_dates = [(l, i, e) for (l, i, e) in dates if not (deadline_fact and (l, i, e) == deadline_fact)]
    dates_html = ""
    if other_dates:
        rows = "".join(
            f'<div class="f"><div class="fl">{_esc(l)}</div><div class="fv">{_esc(_fmt_date(i))}'
            f'{"<span class=est>(estimated)</span>" if e else ""}</div></div>'
            for (l, i, e) in other_dates
        )
        dates_html = f'<h2>Key dates</h2><div class="facts">{rows}</div>'

    ext_html = (f'<a class="ext" href="{_esc(program_url)}" rel="nofollow noopener" '
                f'target="_blank">Visit the official {_esc(org) or "program"} page →</a>'
                if program_url else "")

    doc = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(meta_desc)}">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{_esc(canonical_url)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{_esc(name)}">
<meta property="og:description" content="{_esc(meta_desc)}">
<meta property="og:url" content="{_esc(canonical_url)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600&family=Space+Mono&display=swap">
<style>{_PAGE_CSS}</style>
<script type="application/ld+json">{_json_ld(opp, dates, canonical_url)}</script>
</head><body>
<div class="wrap">
  <nav class="crumbs"><a href="{SEO_SITE_ORIGIN}/">Highschool Wingman</a> &rsaquo; Opportunities &rsaquo; {_esc(name)}</nav>
  <h1>{_esc(name)}</h1>
  {f'<p class="sub">{_esc(summary)}</p>' if summary else ''}
  {f'<div class="facts">{facts_html}</div>' if facts_html else ''}
  {checklist_html}
  {dates_html}
  <div class="cta">
    <div class="t"><b>Don’t miss this deadline.</b><span>Track {_esc(name)} free — Wingman reminds you and syncs it to your calendar.</span></div>
    <a class="btn" href="{SEO_SITE_ORIGIN}/login">Track this deadline &rarr;</a>
  </div>
  {ext_html}
  <footer>Highschool Wingman helps high schoolers find and track extracurricular opportunities —
  summer programs, internships, research and academic competitions, conferences and journals.
  Deadlines are checked and updated regularly; always confirm on the program’s official page.</footer>
</div>
</body></html>"""
    return doc, robots


# ----------------------------- routes -----------------------------

@router.get("/opportunities/{slug}")
def program_page(slug: str):
    """One server-rendered program page. Resolves by seo_slug (canonical) or id (fallback);
    301s an id/legacy-slug hit to the canonical slug URL so ranking never splits across paths.
    404s when the row is missing or inactive — an inactive row has no public page."""
    row, canonical_slug = _resolve(slug)
    if not row:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    if canonical_slug and canonical_slug != slug:
        return RedirectResponse(f"/opportunities/{canonical_slug}", status_code=301)
    doc, _robots = _render_page(row, canonical_slug)
    return HTMLResponse(doc, headers={"Cache-Control": _PAGE_CACHE})


# PostgREST caps a single response at 1000 rows regardless of a higher `limit` in the query —
# the exact trap CLAUDE.md warns about. With >1000 indexed pages, a single request silently
# dropped the tail from the sitemap, so those pages were never advertised to crawlers. Page
# through with Range instead.
_SITEMAP_PAGE = 1000


def _fetch_indexed_rows():
    """Every active, indexed row's (seo_slug, seo_evaluated_at), paging past the 1000-row cap.
    Returns [] (→ sitemap degrades to the static URLs) if Supabase is unset or the column is
    not migrated — _supabase_request returns None on failure, which stops the loop."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return []
    rows, offset = [], 0
    while True:
        page = _supabase_request("opportunities", params={
            "select": "seo_slug,seo_evaluated_at", "seo_status": f"eq.{STATUS_INDEXED}",
            "is_active": "eq.true", "order": "seo_slug",
        }, extra_headers={"Range": f"{offset}-{offset + _SITEMAP_PAGE - 1}"})
        if not page:
            break
        rows.extend(page)
        if len(page) < _SITEMAP_PAGE:
            break
        offset += _SITEMAP_PAGE
    return rows


@router.get("/sitemap.xml")
def sitemap():
    """Lists the static pages plus every opportunity the last evaluation stored as `indexed`.

    Reads the durable seo_status column rather than recomputing the whole catalog on each hit:
    a sitemap does not need to be second-fresh (Google re-crawls on its own cadence and we
    regenerate the column on every evaluation run), and an indexed-only query is cheap. If the
    column is not migrated yet, this degrades to the static URLs alone rather than erroring."""
    urls = [f"{SEO_SITE_ORIGIN}/", f"{SEO_SITE_ORIGIN}/about.html",
            f"{SEO_SITE_ORIGIN}/pricing.html"]
    entries = [f"  <url><loc>{html.escape(u)}</loc></url>" for u in urls]

    for r in _fetch_indexed_rows():
        slug = _clean(r.get("seo_slug"))
        if not slug:
            continue
        loc = html.escape(f"{SEO_SITE_ORIGIN}/opportunities/{slug}")
        lastmod = str(r.get("seo_evaluated_at") or "")[:10]
        lm = f"<lastmod>{html.escape(lastmod)}</lastmod>" if lastmod else ""
        entries.append(f"  <url><loc>{loc}</loc>{lm}</url>")

    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(entries) + "\n</urlset>\n")
    return Response(content=xml, media_type="application/xml",
                    headers={"Cache-Control": _PAGE_CACHE})


@router.get("/robots.txt")
def robots():
    """Served as a route because the static file route deny-lists `.txt` (public/robots.txt
    would otherwise 404 into the SPA shell). Reads the checked-in file so there is one source
    of truth; falls back to a minimal policy if it is missing."""
    try:
        with open(_ROBOTS_PATH, "r", encoding="utf-8") as f:
            body = f.read()
    except Exception:
        body = _ROBOTS_FALLBACK
    return PlainTextResponse(body, headers={"Cache-Control": _PAGE_CACHE})
