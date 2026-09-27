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

# Light fields for the hub/facet listing cards — no action_items / eligibility prose.
SEO_CARD_FIELDS = "seo_slug,name,org,type,state,important_dates"

# The public facet taxonomy: one crawlable landing page per opportunity KIND. `db_type` is the
# opportunities.type value (ops.core.OPPORTUNITY_TYPES); `slug` is the URL segment under
# /opportunities/type/<slug>; `label`/`h1`/`blurb` are the on-page + SEO copy; `badge` is the
# short pill shown on a listing row. Order here is the order tiles render on the hub.
FACETS = [
    {"slug": "summer-programs", "db_type": "Program", "badge": "Summer Program",
     "label": "Summer Programs", "h1": "Summer Programs for High School Students",
     "blurb": "Pre-college programs, summer academies, and camps — with deadlines, eligibility, and how to apply."},
    {"slug": "internships", "db_type": "Internship", "badge": "Internship",
     "label": "Internships", "h1": "Internships for High School Students",
     "blurb": "Hands-on positions with labs, mentors, and organizations that take high schoolers."},
    {"slug": "research-competitions", "db_type": "Research", "badge": "Research",
     "label": "Research & Project Competitions", "h1": "Research & Project Competitions for High Schoolers",
     "blurb": "Science fairs, app challenges, and project-based contests where you enter your own work."},
    {"slug": "academic-competitions", "db_type": "Competition", "badge": "Competition",
     "label": "Academic Competitions", "h1": "Academic Competitions for High School Students",
     "blurb": "Olympiads, quiz bowls, and subject exams that test skills and knowledge."},
    {"slug": "volunteering", "db_type": "Volunteer", "badge": "Volunteering",
     "label": "Volunteer Opportunities", "h1": "Volunteer Opportunities for High School Students",
     "blurb": "Service roles and volunteer placements with organizations that welcome students."},
    {"slug": "conferences", "db_type": "Conference", "badge": "Conference",
     "label": "Conferences", "h1": "Conferences for High School Students",
     "blurb": "Academic conferences and workshops where high schoolers can attend or present."},
    {"slug": "journals", "db_type": "Journal", "badge": "Journal",
     "label": "Journals", "h1": "Journals for High School Students",
     "blurb": "Student and academic journals that publish high school research."},
]
_FACET_BY_SLUG = {f["slug"]: f for f in FACETS}
_TYPE_TO_FACET = {f["db_type"]: f for f in FACETS}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "Washington, D.C.",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

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

# The brand lockup, inline so the crawler and first paint both get it with no extra request.
# Ported 1:1 from frontend/src/ui/components.tsx Logo (which is public/favicon.svg): four
# ascending orange bars + the yellow "sun". Kept in sync with that mark.
def _logo_svg(size=30):
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 100 100" '
        'xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">'
        '<circle cx="71" cy="32" r="13" fill="#FACC15" opacity="0.35"/>'
        '<rect x="24" y="60" width="10" height="16" rx="2" fill="#F97316"/>'
        '<rect x="38" y="52" width="10" height="24" rx="2" fill="#F97316"/>'
        '<rect x="52" y="44" width="10" height="32" rx="2" fill="#F97316"/>'
        '<rect x="66" y="36" width="10" height="40" rx="2" fill="#F97316"/>'
        '<circle cx="71" cy="32" r="6.5" fill="#FACC15"/></svg>'
    )


# BENTO & POP, ported from frontend/src/ui/theme.ts: navy #1D4E89 ink + borders, orange
# #F79256 primary CTA, cream #FBF8F3 canvas, Space Grotesk display / Plus Jakarta Sans body,
# and the signature hard un-blurred "pop" offset shadow on the hero surfaces.
_PAGE_CSS = """
:root{--navy:#1D4E89;--ink:#1A2540;--ink-soft:#4A6685;--cream:#FBF8F3;--card:#FFFFFF;
--orange:#F79256;--orange-deep:#F4791D;--teal:#00B2CA;--hairline:#D9DEEB;--muted:#8A93A6;
--pop:3px 3px 0 var(--navy)}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--cream);color:var(--ink);-webkit-font-smoothing:antialiased;
font-family:"Plus Jakarta Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.6}
a{color:inherit}
.site{border-bottom:2px solid var(--navy);background:var(--cream);position:sticky;top:0;z-index:5}
.site .in{max-width:820px;margin:0 auto;padding:12px 22px;display:flex;align-items:center;gap:10px}
.brand{display:flex;align-items:center;gap:9px;text-decoration:none}
.brand svg{display:block}
.brand b{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:19px;color:var(--navy);letter-spacing:-.01em}
.brand .beta{font-family:"Space Grotesk",sans-serif;font-size:10px;font-weight:700;color:var(--orange-deep);
border:1.5px solid var(--orange);border-radius:6px;padding:1px 5px;letter-spacing:.04em}
.site .spacer{flex:1}
.site .go{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:13px;text-decoration:none;
color:#1a1205;background:var(--orange);border:2px solid var(--navy);box-shadow:var(--pop);border-radius:9px;padding:8px 14px}
.wrap{max-width:820px;margin:0 auto;padding:0 22px}
.crumbs{font-size:12.5px;color:var(--muted);margin:24px 0 0;font-weight:600}
.crumbs a{color:var(--muted);text-decoration:none}
.crumbs a:hover{color:var(--navy)}
h1{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:clamp(28px,5vw,40px);
letter-spacing:-.02em;line-height:1.12;margin:8px 0 4px;color:var(--navy)}
.org{font-family:"Space Grotesk",sans-serif;font-weight:500;font-size:15px;color:var(--ink-soft);margin:0 0 14px}
.sub{font-size:17px;color:var(--ink-soft);margin:0 0 24px;max-width:60ch}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:0 0 26px}
.f{background:var(--card);border:1px solid var(--hairline);border-radius:12px;padding:13px 15px;
box-shadow:0 2px 14px rgba(15,23,42,.05)}
.fl{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.fv{font-family:"Space Grotesk",sans-serif;font-weight:600;font-size:16px;margin-top:5px;color:var(--ink)}
.fv .est{font-size:10px;color:var(--orange-deep);font-weight:700;margin-left:5px;letter-spacing:.03em}
h2{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:21px;margin:30px 0 12px;color:var(--navy)}
.card{background:var(--card);border:2px solid var(--navy);border-radius:16px;box-shadow:var(--pop);padding:20px 22px;margin:0 0 8px}
.chk{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:12px}
.chk li{display:grid;grid-template-columns:22px 1fr;gap:12px;align-items:start;font-size:15px;color:var(--ink)}
.chk .bx{width:18px;height:18px;border:2px solid var(--teal);border-radius:5px;margin-top:2px}
.src{font-size:12.5px;color:var(--muted);margin-top:12px}
.cta{margin:32px 0 12px;background:linear-gradient(135deg,#101C36,#182750);border-radius:16px;
padding:22px 24px;display:flex;gap:18px;align-items:center;flex-wrap:wrap;box-shadow:var(--pop)}
.cta .t{flex:1;min-width:200px}
.cta .t b{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:17px;display:block;color:#fff;margin-bottom:3px}
.cta .t span{font-size:13.5px;color:#AEB8CC}
.cta a.btn{background:var(--orange);color:#1a1205;font-weight:700;text-decoration:none;
font-family:"Space Grotesk",sans-serif;padding:12px 22px;border-radius:10px;box-shadow:3px 3px 0 rgba(0,0,0,.35)}
.ext{display:inline-block;margin:8px 0 0;font-size:14px;color:var(--teal);font-weight:600;text-decoration:none}
.ext:hover{text-decoration:underline}
footer{border-top:2px solid var(--navy);margin-top:38px;padding:24px 0 64px;color:var(--muted);font-size:12.5px}
.foot-brand{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.foot-brand b{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:15px;color:var(--navy)}
/* hub + facet listings */
.lead{font-size:17px;color:var(--ink-soft);margin:6px 0 24px;max-width:62ch}
.sec{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:13px;letter-spacing:.05em;
text-transform:uppercase;color:var(--muted);margin:32px 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.tile{display:block;text-decoration:none;background:var(--card);border:2px solid var(--navy);
border-radius:14px;box-shadow:var(--pop);padding:18px}
.tile .n{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:18px;color:var(--navy);margin:0 0 5px}
.tile .d{font-size:13px;color:var(--ink-soft);line-height:1.5}
.tile .c{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:12.5px;color:var(--orange-deep);margin-top:12px}
.rows{display:flex;flex-direction:column;gap:10px}
.row{display:flex;align-items:center;gap:12px;text-decoration:none;background:var(--card);
border:1px solid var(--hairline);border-radius:12px;padding:13px 16px;box-shadow:0 2px 14px rgba(15,23,42,.05)}
.row:hover{border-color:var(--navy)}
.row .m{flex:1;min-width:0}
.row .rn{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:16px;color:var(--ink);margin:0}
.row .ro{font-size:13px;color:var(--muted);margin:2px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .rd{font-family:"Space Grotesk",sans-serif;font-weight:600;font-size:12.5px;color:var(--navy);white-space:nowrap;flex-shrink:0}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;
color:var(--navy);background:#EEF0FB;border-radius:999px;padding:3px 9px;flex-shrink:0}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 6px}
.chips a{text-decoration:none;font-size:13px;font-weight:600;color:var(--navy);background:var(--card);
border:1.5px solid var(--hairline);border-radius:999px;padding:6px 13px}
.chips a:hover{border-color:var(--navy)}
.related{margin-top:30px;padding-top:20px;border-top:1px solid var(--hairline)}
@media (max-width:440px){.site .go{display:none}.brand b{font-size:17px}.row .ro{display:none}}
@media (prefers-color-scheme:dark){:root{--navy:#5B8FCB;--ink:#EEF1F6;--ink-soft:#B9C4D6;
--cream:#0D131F;--card:#161F2F;--hairline:#26314A;--muted:#93A0B6;--pop:3px 3px 0 #0a1220}
.site,footer{border-color:#26314A}.f,.card,.tile{border-color:#2c3a57}
.brand b,h1,h2,.foot-brand b,.tile .n{color:#EEF1F6}
.badge{background:#1f2a44;color:#cdd9f0}}
"""


def _header_html():
    return (f'<header class="site"><div class="in">'
            f'<a class="brand" href="{SEO_SITE_ORIGIN}/">{_logo_svg(30)}<b>Highschool Wingman</b>'
            f'<span class="beta">BETA</span></a><span class="spacer"></span>'
            f'<a class="go" href="{SEO_SITE_ORIGIN}/login">Get started &rarr;</a></div></header>')


def _footer_html():
    return ('<footer>'
            f'<div class="foot-brand">{_logo_svg(24)}<b>Highschool Wingman</b></div>'
            'Highschool Wingman helps high schoolers find and track extracurricular opportunities — '
            'summer programs, internships, research and academic competitions, conferences and journals. '
            'Deadlines are checked and updated regularly; always confirm on the program’s official page.'
            '</footer>')


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

    # Internal-link context: this row's category facet and state facet. These are what turn the
    # program pages from sitemap-only orphans into a crawlable graph (hub -> facet -> program and
    # back), which is the fix for "Discovered - currently not indexed".
    facet = _TYPE_TO_FACET.get(opp.get("type"))
    state_code = (opp.get("state") or "").upper()
    state_name = US_STATES.get(state_code)

    crumb_html = (f'<nav class="crumbs"><a href="{SEO_SITE_ORIGIN}/">Home</a> &rsaquo; '
                  f'<a href="{SEO_SITE_ORIGIN}/opportunities">Opportunities</a>')
    if facet:
        crumb_html += f' &rsaquo; <a href="/opportunities/type/{facet["slug"]}">{_esc(facet["label"])}</a>'
    crumb_html += f' &rsaquo; {_esc(name)}</nav>'

    rel = []
    if facet:
        rel.append((f'/opportunities/type/{facet["slug"]}', facet["label"]))
    if state_name:
        rel.append((f'/opportunities/state/{state_code.lower()}', f'Opportunities in {state_name}'))
    rel.append(("/opportunities", "All opportunities"))
    related_html = ('<div class="related"><div class="sec">Explore more</div><div class="chips">'
                    + "".join(f'<a href="{_esc(u)}">{_esc(t)}</a>' for u, t in rel)
                    + '</div></div>')

    crumb_items = [("Home", f"{SEO_SITE_ORIGIN}/"),
                   ("Opportunities", f"{SEO_SITE_ORIGIN}/opportunities")]
    if facet:
        crumb_items.append((facet["label"], f'{SEO_SITE_ORIGIN}/opportunities/type/{facet["slug"]}'))
    crumb_items.append((name, canonical_url))
    crumb_ld = _breadcrumb_ld(crumb_items)

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
        checklist_html = f'<h2>How to apply</h2><div class="card"><ul class="chk">{items}</ul>{src}</div>'

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
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap">
<style>{_PAGE_CSS}</style>
<script type="application/ld+json">{_json_ld(opp, dates, canonical_url)}</script>
<script type="application/ld+json">{crumb_ld}</script>
</head><body>
{_header_html()}
<div class="wrap">
  {crumb_html}
  <h1>{_esc(name)}</h1>
  {f'<p class="org">{_esc(org)}</p>' if org else ''}
  {f'<p class="sub">{_esc(summary)}</p>' if summary else ''}
  {f'<div class="facts">{facts_html}</div>' if facts_html else ''}
  {checklist_html}
  {dates_html}
  <div class="cta">
    <div class="t"><b>Don’t miss this deadline.</b><span>Track {_esc(name)} free — Wingman reminds you and syncs it to your calendar.</span></div>
    <a class="btn" href="{SEO_SITE_ORIGIN}/login">Track this deadline &rarr;</a>
  </div>
  {ext_html}
  {related_html}
  {_footer_html()}
</div>
</body></html>"""
    return doc, robots


# ----------------------------- hub + facet pages -----------------------------

def _breadcrumb_ld(items):
    """schema.org BreadcrumbList from [(name, url), ...] — rich breadcrumbs in search results."""
    data = {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": u}
                                for i, (n, u) in enumerate(items)]}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _document(title, meta_desc, canonical_url, robots, inner_html, ld_blocks=()):
    """The shared page shell (head + branded header + footer), so hub/facet pages match the
    program page exactly. inner_html is placed inside .wrap; ld_blocks are JSON-LD strings."""
    ld = "".join(f'<script type="application/ld+json">{b}</script>' for b in ld_blocks)
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(meta_desc)}">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{_esc(canonical_url)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(meta_desc)}">
<meta property="og:url" content="{_esc(canonical_url)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap">
<style>{_PAGE_CSS}</style>{ld}
</head><body>
{_header_html()}
<div class="wrap">
{inner_html}
{_footer_html()}
</div>
</body></html>"""


# The listing cards are memoised briefly so a crawler sweeping every facet page in one pass does
# not re-query the whole catalog on each hit (the CDN also caches for an hour via _PAGE_CACHE).
_cards_cache = {"at": 0.0, "rows": None}
_CARDS_TTL = 300


def _all_indexed_cards():
    """Every active, indexed row's card fields, paged past PostgREST's 1000-row cap. Returns []
    if Supabase is unset or the seo_status column is not migrated (same degrade path as the
    sitemap), which makes the hub/facet routes 404 rather than error."""
    import time
    now = time.time()
    if _cards_cache["rows"] is not None and now - _cards_cache["at"] < _CARDS_TTL:
        return _cards_cache["rows"]
    rows = []
    if SUPABASE_URL and SUPABASE_SERVICE_KEY:
        offset = 0
        while True:
            page = _supabase_request("opportunities", params={
                "select": SEO_CARD_FIELDS, "seo_status": f"eq.{STATUS_INDEXED}",
                "is_active": "eq.true", "order": "name",
            }, extra_headers={"Range": f"{offset}-{offset + _SITEMAP_PAGE - 1}"})
            if not page:
                break
            rows.extend(page)
            if len(page) < _SITEMAP_PAGE:
                break
            offset += _SITEMAP_PAGE
    _cards_cache.update(at=now, rows=rows)
    return rows


def _deadline_chip(row):
    """A short 'Due <date>' for a listing card, if the row carries a deadline. Estimated dates
    are marked, never presented as confirmed — the same honesty the program page keeps."""
    for label, iso, est in _dates_rows(row):
        if "deadline" in label.lower():
            return f"Due {_fmt_date(iso)}" + (" (est.)" if est else "")
    return ""


def _card_row(row):
    """One opportunity as a linked listing row: name, org, category badge, deadline chip."""
    slug = _clean(row.get("seo_slug"))
    if not slug:
        return ""
    name = _clean(row.get("name")) or "Opportunity"
    org = _clean(row.get("org"))
    facet = _TYPE_TO_FACET.get(row.get("type"))
    badge = facet["badge"] if facet else _clean(row.get("type"))
    chip = _deadline_chip(row)
    meta = f'<p class="rn">{_esc(name)}</p>' + (f'<p class="ro">{_esc(org)}</p>' if org else "")
    badge_html = f'<span class="badge">{_esc(badge)}</span>' if badge else ""
    chip_html = f'<div class="rd">{_esc(chip)}</div>' if chip else ""
    return (f'<a class="row" href="/opportunities/{_esc(slug)}">'
            f'<div class="m">{meta}</div>{badge_html}{chip_html}</a>')


def _hub_page(cards):
    """The master hub: category tiles (with live counts) + browse-by-state chips. Links to the
    facet pages rather than to every program, so PageRank flows hub -> facet -> program."""
    total = len(cards)
    by_type, by_state = {}, {}
    for c in cards:
        t = c.get("type")
        if t in _TYPE_TO_FACET:
            by_type[t] = by_type.get(t, 0) + 1
        st = (c.get("state") or "").upper()
        if st in US_STATES:
            by_state[st] = by_state.get(st, 0) + 1

    tiles = ""
    for f in FACETS:
        n = by_type.get(f["db_type"], 0)
        if not n:
            continue
        noun = "opportunity" if n == 1 else "opportunities"
        tiles += (f'<a class="tile" href="/opportunities/type/{f["slug"]}">'
                  f'<div class="n">{_esc(f["label"])}</div>'
                  f'<div class="d">{_esc(f["blurb"])}</div>'
                  f'<div class="c">{n} {noun} &rarr;</div></a>')

    state_items = sorted(by_state.items(), key=lambda kv: (-kv[1], US_STATES[kv[0]]))
    chips = "".join(f'<a href="/opportunities/state/{code.lower()}">{_esc(US_STATES[code])} ({n})</a>'
                    for code, n in state_items)
    states_html = (f'<div class="sec">Browse by state</div><div class="chips">{chips}</div>'
                   if chips else "")

    canonical = f"{SEO_SITE_ORIGIN}/opportunities"
    title = ("High School Opportunities — Summer Programs, Internships, Research & Competitions "
             "| Highschool Wingman")
    meta = (f"Browse {total} extracurricular opportunities for high school students — summer programs, "
            "internships, research and academic competitions, conferences and journals — with deadlines, "
            "eligibility, and how to apply.")
    inner = (f'<nav class="crumbs"><a href="{SEO_SITE_ORIGIN}/">Home</a> &rsaquo; Opportunities</nav>'
             f'<h1>High School Opportunities</h1>'
             f'<p class="lead">Deadlines, eligibility, and how to apply for {total} summer programs, '
             f'internships, research and academic competitions, conferences and journals — all in one '
             f'place, kept up to date.</p>'
             f'<div class="sec">Browse by category</div><div class="grid">{tiles}</div>'
             f'{states_html}')
    ld = [_breadcrumb_ld([("Home", f"{SEO_SITE_ORIGIN}/"), ("Opportunities", canonical)])]
    return _document(title, meta, canonical, "index,follow", inner, ld)


def _type_facet_page(facet, cards):
    """A category landing page: every indexed program of one type, linked, plus sibling-category
    and hub links so the page is both a ranking target and an internal-link hub."""
    cards = sorted(cards, key=lambda c: _clean(c.get("name")).lower())
    n = len(cards)
    rows = "".join(_card_row(c) for c in cards)
    canonical = f'{SEO_SITE_ORIGIN}/opportunities/type/{facet["slug"]}'
    title = f'{facet["h1"]} ({datetime.date.today().year}) | Highschool Wingman'
    meta = (f'{facet["blurb"]} Browse {n} {facet["label"].lower()} with verified deadlines, '
            'eligibility, and application steps.')
    sib = "".join(f'<a href="/opportunities/type/{g["slug"]}">{_esc(g["label"])}</a>'
                  for g in FACETS if g["slug"] != facet["slug"])
    inner = (f'<nav class="crumbs"><a href="{SEO_SITE_ORIGIN}/">Home</a> &rsaquo; '
             f'<a href="{SEO_SITE_ORIGIN}/opportunities">Opportunities</a> &rsaquo; {_esc(facet["label"])}</nav>'
             f'<h1>{_esc(facet["h1"])}</h1>'
             f'<p class="lead">{_esc(facet["blurb"])}</p>'
             f'<div class="rows">{rows}</div>'
             f'<div class="related"><div class="sec">Other categories</div>'
             f'<div class="chips">{sib}<a href="/opportunities">All opportunities</a></div></div>')
    ld = [_breadcrumb_ld([("Home", f"{SEO_SITE_ORIGIN}/"),
                          ("Opportunities", f"{SEO_SITE_ORIGIN}/opportunities"),
                          (facet["label"], canonical)])]
    return _document(title, meta, canonical, "index,follow", inner, ld)


def _state_facet_page(code, name, cards):
    """A per-state landing page: every indexed program whose state is `code`, plus category links."""
    cards = sorted(cards, key=lambda c: _clean(c.get("name")).lower())
    n = len(cards)
    rows = "".join(_card_row(c) for c in cards)
    canonical = f"{SEO_SITE_ORIGIN}/opportunities/state/{code.lower()}"
    noun = "opportunity" if n == 1 else "opportunities"
    title = f"High School Opportunities in {name} ({datetime.date.today().year}) | Highschool Wingman"
    meta = (f"{n} extracurricular {noun} for high school students in {name} — summer programs, "
            "internships, research and competitions — with deadlines and how to apply.")
    cats = "".join(f'<a href="/opportunities/type/{g["slug"]}">{_esc(g["label"])}</a>' for g in FACETS)
    inner = (f'<nav class="crumbs"><a href="{SEO_SITE_ORIGIN}/">Home</a> &rsaquo; '
             f'<a href="{SEO_SITE_ORIGIN}/opportunities">Opportunities</a> &rsaquo; {_esc(name)}</nav>'
             f'<h1>High School Opportunities in {_esc(name)}</h1>'
             f'<p class="lead">{n} {noun} for high schoolers in {_esc(name)}, with verified deadlines, '
             f'eligibility, and how to apply.</p>'
             f'<div class="rows">{rows}</div>'
             f'<div class="related"><div class="sec">Browse by category</div>'
             f'<div class="chips">{cats}<a href="/opportunities">All opportunities</a></div></div>')
    ld = [_breadcrumb_ld([("Home", f"{SEO_SITE_ORIGIN}/"),
                          ("Opportunities", f"{SEO_SITE_ORIGIN}/opportunities"),
                          (name, canonical)])]
    return _document(title, meta, canonical, "index,follow", inner, ld)


# ----------------------------- routes -----------------------------

@router.get("/opportunities")
def opportunities_hub():
    """The master hub. 404s (rather than showing an empty page) when nothing is indexed yet."""
    cards = _all_indexed_cards()
    if not cards:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    return HTMLResponse(_hub_page(cards), headers={"Cache-Control": _PAGE_CACHE})


@router.get("/opportunities/type/{slug}")
def type_facet(slug: str):
    facet = _FACET_BY_SLUG.get(slug.lower())
    if not facet:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    cards = [c for c in _all_indexed_cards() if c.get("type") == facet["db_type"]]
    if not cards:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    return HTMLResponse(_type_facet_page(facet, cards), headers={"Cache-Control": _PAGE_CACHE})


@router.get("/opportunities/state/{code}")
def state_facet(code: str):
    name = US_STATES.get(code.upper())
    if not name:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    cards = [c for c in _all_indexed_cards() if (c.get("state") or "").upper() == code.upper()]
    if not cards:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    return HTMLResponse(_state_facet_page(code.upper(), name, cards),
                        headers={"Cache-Control": _PAGE_CACHE})


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

    # Hub + facet landing pages — only the facets that actually have indexed rows, so the sitemap
    # never advertises a page that 404s.
    cards = _all_indexed_cards()
    if cards:
        urls.append(f"{SEO_SITE_ORIGIN}/opportunities")
        present_types = {c.get("type") for c in cards}
        present_states = {(c.get("state") or "").upper() for c in cards}
        for f in FACETS:
            if f["db_type"] in present_types:
                urls.append(f'{SEO_SITE_ORIGIN}/opportunities/type/{f["slug"]}')
        for code in sorted(present_states):
            if code in US_STATES:
                urls.append(f"{SEO_SITE_ORIGIN}/opportunities/state/{code.lower()}")

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
