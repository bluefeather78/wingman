"""Local-only operations core: agent orchestration, run history, cost/metrics
dashboards, snapshot commit, review-queue moderation, and scraper seeds.

Extracted verbatim from server.py (PLAN_1_decompose.md). Imported ONLY by the
local ops console (ops.admin) and never mounted on the shipped web service, so
none of the /api/agents/* or /api/seeds routes exist there. Imports the shared
seam from app.core (one-way: ops depends on app, never the reverse).

REPO_ROOT anchors the agent scripts, agent_logs/, and agent_settings.json at the
repository root — the agents still live there and are launched with cwd=REPO_ROOT,
exactly as before. In the monolith these paths were derived from __file__ (which
sat at the repo root); this module sits one level down in ops/, so the anchor is
made explicit.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import agent_common
import aggregators_common
import dryrun_common
import mailing_list_common
import query_telemetry
import seed_ledger
from supabase_common import supabase_get
from agent_common import PREVIEW_PREFIX as AGENT_PREVIEW_PREFIX
from app.config import *  # noqa: F401,F403
from app import config
from app.core import (
    _supabase_headers, _supabase_request, _supabase_request_strict, _users_request,
    _is_missing_column_error, _missing_table_error, _runs_cache, _runs_cache_lock,
    RUNS_CACHE_TTL, RECENT_RUNS_LIMIT, invalidate_runs_cache, flush_user_activity,
    INTERACTIVE_AGENTS, FEATURE_LABELS, PROVIDER_LABELS, provider_for_model,
    classify_feature, subscription_state,
)
from app.services.opportunities import _opportunities_cache, _opportunities_cache_lock
from subscription_common import is_trial_expired, promo_kind, PROMO_CODES
from app.services.mailing_list import (
    SIGNUPS_TABLE, SUBSCRIPTIONS_TABLE, SIGNUP_REVIEW_STATUSES, MAILING_LIST_SETUP_HINT,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- Agent execution tracking ----------
# check_reviews.py/scrape_opportunities.py/check_deadlines.py each write a row to a
# Supabase `agent_runs` table (insert at start, patch with items/cost/errors at finish
# — see each script's own docstring for the exact CREATE TABLE). That table is the
# authoritative source for run history and cost/items data: it's accurate even for
# runs triggered outside this console (cron, manual CLI), and survives server restarts,
# unlike a plain in-memory dict. server.py only tracks "is a subprocess currently
# running right now" itself (_agent_process), since that's process-local state the DB
# has no way to know; everything else (items processed, cost, last result) is read
# straight from agent_runs after each run completes.
_agent_process = {}  # {agent_name: subprocess.Popen or None}
_agent_runs_lock = threading.Lock()
_agent_runs = {}  # {agent_name: {status: "running"|"idle", started_at}} — live-run flag only

# The four agents, keyed by the identifier the admin console uses. `db_agent` is the
# literal each script writes into agent_runs.agent — renaming one here without also
# changing the script silently detaches a card from its own history.
#
# `unit` matters: the scraper's items_processed counts SEEDS (search angles), not
# opportunity rows, so its numbers must never be summed with the other three agents'.
# `writes` distinguishes agents that PATCH existing rows from the scraper, which only
# INSERTs new ones (items_updated vs items_added in agent_runs).
# `uses_gemini_search` marks the agents that contend on gemini_common's
# .gemini_web_search.lock — only one of them can run at a time.
# `defaults` are the timing values the console prefills and the server passes through
# as --min-delay / --timeout; see gemini_common._enforce_rate_limit.
#
# `name` is the FRONT-END LABEL only — it's free to be friendlier than the script name and
# is never matched against anything. The identifiers that must not drift are the dict key
# (used by the console's API calls) and `db_agent` (written by the script itself).
#
# DICT ORDER IS THE UI ORDER. The console iterates this map as-is, so the sequence here
# drives the agent cards, the chart legend and stacking, the timing table, and the history
# filter — changing it here changes all of them together. Ordered by how often you reach
# for them: find new opportunities, then update existing ones, then the periodic checks.
AGENT_CONFIGS_SCHEMA = {
    "scraper": {
        "name": "New Opportunity Scout",
        "description": "Search for new opportunities missing from the catalog",
        "script": "scrape_opportunities.py",
        "db_agent": "scraper",
        "unit": "seeds",
        "writes": "inserts",
        "uses_gemini_search": True,
        "api": "Gemini 3.6-flash + googleSearch",
        "defaults": {"min_delay": 5, "timeout": 280},
    },
    "metadata": {
        "name": "Update Opportunity",
        "description": "Refresh name, org, eligibility, pricing and other core fields",
        "script": "refresh_opportunities.py",
        "db_agent": "metadata_refresher",
        "unit": "rows",
        "writes": "updates",
        "uses_gemini_search": False,
        "api": "Gemini 3.5-flash-lite (no web search)",
        "defaults": {"min_delay": 5, "timeout": 120},
    },
    "deadline": {
        "name": "Deadline Checker",
        "description": "Check and update application deadlines and program status",
        "script": "check_deadlines.py",
        "db_agent": "deadline_checker",
        "unit": "rows",
        "writes": "updates",
        "uses_gemini_search": False,  # uses Claude's web_search, a separate quota
        "api": "Claude Haiku + web_search",
        "defaults": {"min_delay": 5, "timeout": 120},
    },
    "reviews": {
        # Display name only — the db_agent literal stays "review_checker" so agent_runs
        # history is unbroken. Renamed 2026-08-28 to match the console redesign.
        "name": "Reviews Generator",
        "description": "Generate the reviews shown on each opportunity — org legitimacy and "
                       "reputation, verified from independent sources",
        "script": "check_reviews.py",
        "db_agent": "review_checker",
        "unit": "rows",
        "writes": "updates",
        "uses_gemini_search": True,
        "api": "Gemini 3.6-flash + googleSearch",
        "defaults": {"min_delay": 5, "timeout": 120},
    },
    "tasks": {
        "name": "Task Generator",
        "description": "Work out each program's application steps, verified against its own page",
        "script": "generate_action_items.py",
        "db_agent": "action_item_generator",
        "unit": "rows",
        "writes": "updates",
        "uses_gemini_search": False,  # never searches — it is handed the page we fetched
        # A row whose page we cannot fetch makes NO model call: there would be nothing to
        # verify the answer against, so it gets a locally-built generic checklist for free.
        # ~1 page in 10 refuses our client, so the paid population is smaller than the row
        # count and the per-row average is pulled below what a fetchable row really costs.
        "api": "Claude Haiku 4.5 (no web search); unfetchable pages resolve locally for free",
        "defaults": {"min_delay": 5, "timeout": 120},
    },
    "mailinglist": {
        "name": "Mailing List Finder",
        "description": "Find each program's mailing-list signup form and store a recipe for it",
        "script": "find_mailing_lists.py",
        "db_agent": "mailing_list_finder",
        "unit": "rows",
        "writes": "updates",
        "uses_gemini_search": False,  # Claude, and no web search at all — see below
        # Most rows never reach the model: the page is fetched and regex-scanned locally,
        # and a row with no provider embed resolves for free. The one Haiku call, made
        # only when a form IS found, answers attribution ("is this THIS program's list?").
        # So this agent's per-row average is far below the others' and is dominated by
        # how many rows happen to have a form.
        "api": "Claude Haiku (no web search); most rows resolve locally for free",
        "defaults": {"min_delay": 5, "timeout": 120},
    },
    "links": {
        "name": "Link Checker",
        "description": "Find catalog rows whose URL is broken; deactivate the ones that are gone",
        "script": "check_links.py",
        "db_agent": "link_checker",
        "unit": "rows",
        # The only agent that DEACTIVATES rows. It never activates and never rejects — a
        # dead link puts a row in the review queue, it is not a verdict on the program.
        "writes": "deactivates",
        "uses_gemini_search": False,
        "api": "None — plain HTTP. This agent is free.",
        # `free` is read by estimate_agent_cost(): $0.00 here is a fact about the design,
        # not a "no history yet" fallback, and the console must not present the two the
        # same way. Every other agent's estimate is a floor; this one's is exact.
        "free": True,
        # No API to rate-limit, so no inter-call delay. The timeout is the per-URL HTTP
        # read timeout; 20s is generous for a page fetch and keeps a full 1374-row pass
        # near a minute at the default worker count.
        "defaults": {"min_delay": 0, "timeout": 20},
    },
}

# ---------- Editable timing overrides ----------
# AGENT_CONFIGS_SCHEMA's `defaults` are the built-in values. Anything you change from the
# console's timing table is stored here as an OVERRIDE, so the shipped defaults stay visible
# (and restorable) rather than being edited away.
#
# A local JSON file rather than a Supabase table on purpose: these are operational knobs for
# whichever machine actually runs the agents, they must be readable before any network call,
# and a wrong value here should never be something you have to reach the database to undo.
AGENT_SETTINGS_PATH = os.path.join(REPO_ROOT, "agent_settings.json")
_settings_lock = threading.Lock()

# Bounds are deliberately permissive but not unbounded: 0 delay is a legitimate (if risky)
# choice for a 2-row test, while a 1-second timeout would just burn money on requests whose
# answers are discarded.
SETTING_BOUNDS = {"min_delay": (0, 300), "timeout": (10, 1800)}
RECOMMENDED_MIN_DELAY = 5  # the value that resolved this pipeline's HTTP 429s


def load_agent_settings():
    """Read saved timing overrides. Missing or corrupt file falls back to no overrides —
    the built-in defaults must always be reachable."""
    try:
        with open(AGENT_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[WARN] Ignoring unreadable {AGENT_SETTINGS_PATH}: {e}")
        return {}


def agent_defaults(agent_name):
    """Effective timing for an agent: built-in defaults with any saved override applied.

    Everything that needs a delay or timeout goes through this — the config endpoint, the
    argv builder and the duration estimator — so an edit takes effect everywhere at once
    instead of only in the field the console happens to prefill.
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name, {})
    merged = dict(cfg.get("defaults") or {})
    override = (load_agent_settings().get(agent_name) or {})
    for key in SETTING_BOUNDS:
        if override.get(key) is not None:
            merged[key] = override[key]
    return merged


def save_agent_settings(agent_name, patch):
    """Persist timing overrides for one agent. Returns (settings, error).

    A value equal to the built-in default is stored as None (i.e. cleared), so 'reset to
    default' and 'happens to match the default' converge on the same state.
    """
    if agent_name not in AGENT_CONFIGS_SCHEMA:
        return None, f"Unknown agent: {agent_name}"
    builtin = AGENT_CONFIGS_SCHEMA[agent_name].get("defaults") or {}
    clean = {}
    for key, (low, high) in SETTING_BOUNDS.items():
        if key not in patch:
            continue
        raw = patch[key]
        if raw in (None, ""):
            clean[key] = None            # explicit reset
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None, f"{key} must be a number"
        if not (low <= value <= high):
            return None, f"{key} must be between {low} and {high}"
        if key == "timeout":
            value = int(value)
        clean[key] = None if value == builtin.get(key) else value

    if not clean:
        return None, "Nothing to update"

    with _settings_lock:
        settings = load_agent_settings()
        entry = dict(settings.get(agent_name) or {})
        entry.update(clean)
        entry = {k: v for k, v in entry.items() if v is not None}
        if entry:
            settings[agent_name] = entry
        else:
            settings.pop(agent_name, None)
        try:
            with open(AGENT_SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
        except Exception as e:
            return None, f"Could not write settings: {e}"
    return agent_defaults(agent_name), None


def agents_config_payload():
    """The agent registry as the console consumes it: static metadata plus EFFECTIVE
    timing, with the built-in values alongside so the UI can show what's been changed."""
    out = {}
    for key, cfg in AGENT_CONFIGS_SCHEMA.items():
        entry = {k: v for k, v in cfg.items() if k != "defaults"}
        entry["defaults"] = agent_defaults(key)
        entry["builtin_defaults"] = cfg.get("defaults") or {}
        out[key] = entry
    return out


def fetch_agent_runs(db_agent=None, limit=20, since_iso=None):
    """GET recent rows from the agent_runs table, newest first.

    Filters to one agent if db_agent is given, and to runs started at/after since_iso if
    given (how the dashboard scopes its charts to a time range).
    """
    params = {"select": "*", "order": "started_at.desc", "limit": str(limit)}
    if db_agent:
        params["agent"] = f"eq.{db_agent}"
    if since_iso:
        params["started_at"] = f"gte.{since_iso}"
    return _supabase_request("agent_runs", params=params) or []


def _parse_iso(value):
    """Parse a Supabase timestamptz, tolerating the trailing Z form. None on anything else."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _agent_key_for(db_agent):
    """Map an agent_runs.agent literal back to its console key.

    Falls back to the raw literal so runs from scripts with no console card (e.g.
    subject_tags_backfill) still appear in the activity log rather than vanishing.
    """
    for key, cfg in AGENT_CONFIGS_SCHEMA.items():
        if cfg["db_agent"] == db_agent:
            return key
    return db_agent


def _run_status(row):
    """Derive a run's status from its agent_runs row.

    'interrupted' matters as its own state: a row with started_at but no finished_at that
    is older than the subprocess timeout means the script died mid-pass without ever
    patching its totals, so its cost and item counts are understated rather than absent.
    Folding that into 'failed' would hide the difference between "ran and reported errors"
    and "vanished halfway through".
    """
    if row.get("errors"):
        return "failed"
    if row.get("finished_at"):
        return "success"
    started = _parse_iso(row.get("started_at"))
    if not started:
        return "interrupted"
    age = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
    return "running" if age < AGENT_RUN_TIMEOUT_SECS else "interrupted"


def _shape_run(row):
    """Project one agent_runs row into the shape the console consumes.

    items_added is included deliberately: it is the scraper's "rows created" count and was
    dropped entirely by the previous version of this endpoint, which is why new-row counts
    never showed up anywhere in the UI.
    """
    key = _agent_key_for(row.get("agent"))
    cfg = AGENT_CONFIGS_SCHEMA.get(key, {})
    if not cfg and key in INTERACTIVE_AGENTS:
        cfg = {"name": INTERACTIVE_AGENTS[key], "unit": "calls"}
    started, finished = _parse_iso(row.get("started_at")), _parse_iso(row.get("finished_at"))
    duration = round((finished - started).total_seconds(), 1) if started and finished else None
    return {
        "id": row.get("id"),
        "agent": key,
        "name": cfg.get("name", row.get("agent")),
        "known_agent": key in AGENT_CONFIGS_SCHEMA,
        "unit": cfg.get("unit", "items"),
        "status": _run_status(row),
        "mode": row.get("mode"),
        # Dry runs are marked by a "-dryrun" mode suffix rather than a dedicated column,
        # so no agent_runs schema change was needed to start logging them.
        "dry_run": str(row.get("mode") or "").endswith("-dryrun"),
        "interactive": row.get("agent") in INTERACTIVE_AGENTS,
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "duration_seconds": duration,
        "items_processed": row.get("items_processed") or 0,
        "items_updated": row.get("items_updated") or 0,
        "items_added": row.get("items_added") or 0,
        "errors": row.get("errors") or 0,
        "cost_usd": row.get("cost_usd"),
        "total_web_searches": row.get("total_web_searches"),
        "silent_search_count": row.get("silent_search_count"),
        "notes": row.get("notes"),
    }


def recent_runs(force=False):
    """Recent agent_runs rows across all agents, cached for RUNS_CACHE_TTL seconds."""
    with _runs_cache_lock:
        fresh = (time.time() - _runs_cache["at"]) < RUNS_CACHE_TTL
        if fresh and not force and _runs_cache["rows"]:
            return _runs_cache["rows"]
    rows = fetch_agent_runs(limit=RECENT_RUNS_LIMIT)
    with _runs_cache_lock:
        _runs_cache["at"] = time.time()
        _runs_cache["rows"] = rows
    return rows


def get_agent_history(agent=None, limit=50, days=None):
    """Recent runs for the activity log, newest first — straight from agent_runs."""
    rows = recent_runs()
    if days:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        rows = [r for r in rows if (_parse_iso(r.get("started_at")) or cutoff) >= cutoff]
    shaped = [_shape_run(r) for r in rows]
    if agent:
        shaped = [r for r in shaped if r["agent"] == agent]
    return shaped[:limit]


import dryrun_common

USER_COSTS_SETUP_SQL = "user_costs_schema.sql"
PLAN_PRICE_USD = 9.99  # mirrors subscription_common.PLAN_PRICE_CENTS


# One label, one bucket, for every row whose `model` is the empty string. Those rows are
# not a model named "" — they were written in the ~5-hour window on 2026-08-21 between
# per-user attribution going live (18:59:39, the first user_costs row ever) and
# user_costs_schema.sql being re-run to add the `model` column (before 2026-08-22 00:16:41,
# the first row that carries one). 13 rows, $0.19, one window, never to grow again.
#
# Only the model id is unknown in them: cost, calls, tokens, user, surface and feature are
# all correct and fully counted, and the provider still resolves through the surface
# fallback in provider_for_model(), which is why the provider split stays complete while
# the model table does not. They are deliberately NOT backfilled from the model pins in
# this file even though the answer is inferable: this column means "what was actually
# billed", and the Sonnet/Haiku drift documented above is exactly what happens when a
# plausible guess gets written into it. Unknown is the honest value.
#
# They collapse into one "Other" row rather than sitting among the real models, because a
# blank model id is not a peer of `gemini-3.5-flash-lite` — it is an absence, and ranking
# an absence by cost alongside real models invites reading it as a third model. It sorts
# last regardless of cost, and ages out of the 30-day window on its own around 20 Sep 2026.
UNTRACKED_MODEL_KEY = "untracked"
UNTRACKED_MODEL_LABEL = "Other"
UNTRACKED_MODEL_NOTE = (
    "Spend recorded before user_costs had a `model` column (21 Aug 2026, a single "
    "~5-hour window). Cost, calls, tokens, user and feature are exact for these rows; "
    "only the model id is unknown, and it is not backfilled because this column means "
    "what was actually billed. Ages out of the window on its own.")


def _group_untracked_models(model_list):
    """Fold every blank-model entry in the by-model list into one 'Other' row, sorted last."""
    named = [m for m in model_list if (m.get("model") or "") not in ("", "(before model tracking)")]
    blank = [m for m in model_list if m not in named]
    if not blank:
        return named
    merged = {
        "key": UNTRACKED_MODEL_KEY, "provider": UNTRACKED_MODEL_KEY,
        "provider_label": UNTRACKED_MODEL_LABEL, "model": UNTRACKED_MODEL_LABEL,
        "untracked": True, "note": UNTRACKED_MODEL_NOTE,
        # Which providers are actually inside the bucket, for the row's tooltip — the
        # information the merge would otherwise throw away.
        "providers": sorted({PROVIDER_LABELS.get(m.get("provider"), m.get("provider"))
                             for m in blank}),
        "cost_usd": round(sum(m["cost_usd"] for m in blank), 6),
        "calls": sum(m["calls"] for m in blank),
        "input_tokens": sum(m.get("input_tokens") or 0 for m in blank),
        "output_tokens": sum(m.get("output_tokens") or 0 for m in blank),
        "web_searches": sum(m.get("web_searches") or 0 for m in blank),
        "users": max((m.get("users") or 0) for m in blank),
    }
    merged["cost_per_call"] = (round(merged["cost_usd"] / merged["calls"], 6)
                               if merged["calls"] else 0.0)
    return named + [merged]


def _group_untracked_feature_models(entries):
    """Same fold, for the model list hanging off one feature."""
    entries = list(entries)
    named = sorted((m for m in entries if (m.get("model") or "")),
                   key=lambda m: m["cost_usd"], reverse=True)
    blank = [m for m in entries if not (m.get("model") or "")]
    if not blank:
        return named
    return named + [{
        "provider": UNTRACKED_MODEL_KEY, "model": UNTRACKED_MODEL_LABEL,
        "untracked": True, "note": UNTRACKED_MODEL_NOTE,
        "cost_usd": round(sum(m["cost_usd"] for m in blank), 6),
        "calls": sum(m["calls"] for m in blank),
    }]


_attribution_start_cache = {"at": None, "checked": None}


def _attribution_start():
    """Timestamp of the very first user_costs row, i.e. when attribution began recording.

    Everything billed before this could never have been attributed to anybody, and must
    not be reported as "signed-out users". Cached for an hour: it is a fixed historical
    fact that only moves once, on the first row ever written.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    checked = _attribution_start_cache["checked"]
    if checked and (now - checked).total_seconds() < 3600:
        return _attribution_start_cache["at"]
    rows = _supabase_request("user_costs", params={
        "select": "first_at", "order": "first_at.asc", "limit": "1"})
    at = _parse_iso((rows or [{}])[0].get("first_at")) if rows else None
    _attribution_start_cache.update({"at": at, "checked": now})
    return at


def get_user_costs(days=30, limit=200):
    """Per-user breakdown of interactive spend, for the console's cost-per-user card.

    Reads user_costs (the attribution table) and reconciles it against the SAME window's
    interactive total, which comes from agent_runs' interactive_* rollups plus
    deadline_check_log. attributed + unattributed == that total by construction; the
    residual is spend from calls that arrived with no userid, and is reported rather than
    quietly absorbed into somebody's row.

    Returns {"table_ready": False} rather than raising when the migration has not been
    applied, so the console can show the setup step instead of an error.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff_day = (now - datetime.timedelta(days=days)).date()
    today_iso = now.date().isoformat()  # reported for reference only — never a headline

    base_select = ("userid,day,surface,feature,calls,input_tokens,output_tokens,"
                   "web_searches,cost_usd,first_at,last_at")
    params = {
        "select": base_select + ",model",
        "day": f"gte.{cutoff_day.isoformat()}",
        "order": "day.desc",
        "limit": "20000",
    }
    rows = _supabase_request("user_costs", params=params)
    model_ready = True
    if rows is None:
        # Selecting a column PostgREST doesn't know 400s exactly like a missing table does,
        # so a half-migrated database is indistinguishable until the narrower select is
        # tried. Falling straight through to "table not ready" would hide every already
        # attributed dollar behind a migration that has, in fact, already been run.
        params["select"] = base_select
        rows = _supabase_request("user_costs", params=params)
        model_ready = False
    if rows is None:
        return {"table_ready": False, "model_ready": False,
                "setup_sql_file": USER_COSTS_SETUP_SQL, "days": days,
                "price_per_month_usd": PLAN_PRICE_USD, "users": [], "features": [],
                "providers": [], "models": [], "series": [], "totals": {}}

    # --- fold the rollup rows into one entry per user ---
    users = {}
    features = {}
    providers = {}
    models = {}
    per_day = {}
    for r in rows:
        uid = r.get("userid") or "(unknown)"
        cost = float(r.get("cost_usd") or 0)
        feat = r.get("feature") or "other"
        model = (r.get("model") or "").strip()
        provider = provider_for_model(model, r.get("surface"))
        u = users.setdefault(uid, {
            "userid": uid, "calls": 0, "web_searches": 0, "cost_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0,
            # Per-day totals for this user, folded down at the edge into "their most
            # recent day with spend". A calendar "today" cannot answer "is this still
            # recording?" — the rows are bucketed on the UTC day, which rolls at 5pm
            # Pacific, so a working pipeline reads $0.00 every evening. The latest ACTIVE
            # day is exact (no partial-day estimation the daily grain cannot support) and
            # never reads zero while there is recent activity.
            "by_day": {},
            "features": {}, "surfaces": {}, "providers": {}, "models": {},
            "first_at": None, "last_at": None,
        })
        u["calls"] += int(r.get("calls") or 0)
        day_key = r.get("day")
        if day_key:
            dd = u["by_day"].setdefault(day_key, {"cost_usd": 0.0, "calls": 0})
            dd["cost_usd"] += cost
            dd["calls"] += int(r.get("calls") or 0)
        u["web_searches"] += int(r.get("web_searches") or 0)
        u["input_tokens"] += int(r.get("input_tokens") or 0)
        u["output_tokens"] += int(r.get("output_tokens") or 0)
        u["cost_usd"] += cost
        u["features"][feat] = round(u["features"].get(feat, 0.0) + cost, 6)
        surf = r.get("surface") or "gemini"
        u["surfaces"][surf] = round(u["surfaces"].get(surf, 0.0) + cost, 6)
        u["providers"][provider] = round(u["providers"].get(provider, 0.0) + cost, 6)
        # Keyed by "provider/model" so two vendors can never collide on a shared model
        # name, and so a model entry is self-describing wherever it is rendered.
        mkey = provider + "/" + model if model else provider
        mrow = u["models"].setdefault(mkey, {"provider": provider, "model": model,
                                             "cost_usd": 0.0, "calls": 0})
        mrow["cost_usd"] = round(mrow["cost_usd"] + cost, 6)
        mrow["calls"] += int(r.get("calls") or 0)

        pr = providers.setdefault(provider, {
            "key": provider, "label": PROVIDER_LABELS.get(provider, provider),
            "cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "web_searches": 0, "users": set(), "models": set()})
        pr["cost_usd"] += cost
        pr["calls"] += int(r.get("calls") or 0)
        pr["input_tokens"] += int(r.get("input_tokens") or 0)
        pr["output_tokens"] += int(r.get("output_tokens") or 0)
        pr["web_searches"] += int(r.get("web_searches") or 0)
        pr["users"].add(uid)
        if model:
            pr["models"].add(model)

        m = models.setdefault(mkey, {
            "key": mkey, "provider": provider,
            "provider_label": PROVIDER_LABELS.get(provider, provider),
            # A blank model is a row written before the breakout existed, not a model
            # literally named "" — say so rather than rendering an empty cell.
            "model": model or "(before model tracking)",
            "cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "web_searches": 0, "users": set(), "features": {}})
        m["cost_usd"] += cost
        m["calls"] += int(r.get("calls") or 0)
        m["input_tokens"] += int(r.get("input_tokens") or 0)
        m["output_tokens"] += int(r.get("output_tokens") or 0)
        m["web_searches"] += int(r.get("web_searches") or 0)
        m["users"].add(uid)
        m["features"][feat] = round(m["features"].get(feat, 0.0) + cost, 6)
        for field in ("first_at", "last_at"):
            v = r.get(field)
            if not v:
                continue
            cur = u[field]
            if cur is None or (v < cur if field == "first_at" else v > cur):
                u[field] = v

        f = features.setdefault(feat, {"key": feat, "label": FEATURE_LABELS.get(feat, feat),
                                       "cost_usd": 0.0, "calls": 0, "users": set(),
                                       # Which model(s) served this feature. The console's
                                       # by-feature view needs the inverse of the by-model
                                       # view's feature split, and only this loop sees both.
                                       "models": {}})
        f["cost_usd"] += cost
        f["calls"] += int(r.get("calls") or 0)
        f["users"].add(uid)
        fm = f["models"].setdefault(mkey, {"provider": provider, "model": model,
                                           "cost_usd": 0.0, "calls": 0})
        fm["cost_usd"] = round(fm["cost_usd"] + cost, 6)
        fm["calls"] += int(r.get("calls") or 0)

        day = r.get("day")
        if day:
            d = per_day.setdefault(day, {"cost_usd": 0.0, "users": set(), "calls": 0})
            d["cost_usd"] += cost
            d["users"].add(uid)
            d["calls"] += int(r.get("calls") or 0)

    # --- decorate with account data: a cost figure only means something next to the plan ---
    try:
        accounts = _users_request("GET", "?" + urllib.parse.urlencode({
            "select": "userid,first_name,last_name,email,subscription_status,trial_ends_at,"
                      "subscription_end_at,created_at",
            "limit": "5000",
        })) or []
    except Exception as e:
        # The spend numbers are the point; losing the name/plan decoration degrades the
        # card rather than failing it.
        print(f"[WARN] Could not load accounts for per-user cost card: {e}")
        accounts = []
    by_id = {str(a.get("userid") or "").lower(): a for a in accounts}

    # Every account gets a row, spend or not. Built from user_costs alone this table was a
    # spend ledger wearing a roster's name: an account that signs up and never triggers a
    # billed call has no rows at all, so 9 of 15 accounts — including every recent signup —
    # could not appear at any range setting. A trial that costs $0 is not missing data, it
    # is the single most important thing this page can tell you.
    for a in accounts:
        uid = str(a.get("userid") or "").strip().lower()
        if not uid or uid in users:
            continue
        users[uid] = {
            "userid": uid, "calls": 0, "web_searches": 0, "cost_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0, "by_day": {},
            "features": {}, "surfaces": {}, "providers": {}, "models": {},
            "first_at": None, "last_at": None,
        }

    for uid, u in users.items():
        a = by_id.get(uid) or {}
        name = " ".join(x for x in [a.get("first_name"), a.get("last_name")] if x).strip()
        u["name"] = name or None
        u["email"] = a.get("email")
        u["known_account"] = bool(a)
        u["subscription_status"] = a.get("subscription_status") or ("unknown" if a else "no account")
        u["trial_ends_at"] = a.get("trial_ends_at")
        u["cost_usd"] = round(u["cost_usd"], 6)
        u["created_at"] = a.get("created_at")
        u["has_spend"] = u["cost_usd"] > 0 or u["calls"] > 0
        # The most recent day this user actually spent anything, and what it cost. Exact
        # at the daily grain the rows are stored at — unlike a rolling 24h window, which
        # would have to guess how much of yesterday's single rollup row falls inside it.
        by_day = u.pop("by_day", {})
        recent_day = max(by_day) if by_day else None
        u["recent_day"] = recent_day
        u["recent_day_cost_usd"] = round(by_day[recent_day]["cost_usd"], 6) if recent_day else 0.0
        u["recent_day_calls"] = by_day[recent_day]["calls"] if recent_day else 0
        # What this user's usage costs measured against one month of the plan. Over 100%
        # means the account loses money on inference alone, before any other cost.
        u["pct_of_plan"] = round(u["cost_usd"] / PLAN_PRICE_USD, 4) if PLAN_PRICE_USD else None
        u["margin_usd"] = round(PLAN_PRICE_USD - u["cost_usd"], 4)
        u["cost_per_call"] = round(u["cost_usd"] / u["calls"], 6) if u["calls"] else 0.0
        # Dict -> cost-sorted list at the edge, so the console renders in cost order
        # without re-sorting an object whose key order it should not have to trust.
        u["models"] = sorted(u["models"].values(), key=lambda mm: mm["cost_usd"],
                             reverse=True)

    # Spend first, then never-active accounts newest-first — a zero row is not a tie to be
    # broken arbitrarily, it is a signup worth reading in the order they arrived.
    ranked = sorted(users.values(),
                    key=lambda x: (x["has_spend"], x["cost_usd"], x.get("created_at") or ""),
                    reverse=True)
    truncated = max(0, len(ranked) - limit)
    ranked = ranked[:limit]
    accounts_total = len(accounts)
    # Known accounts only. Three userids carry attributed spend with no matching row in
    # `users` (leftover test ids) — counting those as accounts made both halves of the
    # Accounts tile wrong: 6 active / 9 idle, against a true 3 active / 12 idle.
    accounts_with_spend = sum(1 for u in users.values()
                              if u["has_spend"] and u.get("known_account"))

    # --- reconcile against the window's interactive total ---
    cutoff = now - datetime.timedelta(days=days)
    interactive_total = 0.0
    for r in recent_runs():
        if r.get("agent") not in INTERACTIVE_AGENTS:
            continue
        started = _parse_iso(r.get("started_at"))
        if started and started >= cutoff:
            interactive_total += float(r.get("cost_usd") or 0)
    interactive_total += float(fetch_deadline_check_cost(cutoff, now).get("cost_usd") or 0)

    attributed = round(sum(u["cost_usd"] for u in users.values()), 6)
    # Clamped at zero: the two sources are written by different code paths and a rounding
    # or in-flight difference must not surface as a negative "unattributed" figure.
    unattributed = round(max(0.0, interactive_total - attributed), 6)

    # The residual is two unrelated things, and lumping them together libels your users.
    # Spend that predates the attribution feature could never have been attributed to
    # anyone; spend since then that carries no userid is genuinely signed-out traffic.
    # Reported as one number, the first swamps the second and pins the attribution rate
    # at a figure that cannot improve until the old rows age out of the window.
    attribution_started_at = _attribution_start()
    pre_attribution = 0.0
    if attribution_started_at and attribution_started_at > cutoff:
        for r in recent_runs():
            if r.get("agent") not in INTERACTIVE_AGENTS:
                continue
            started = _parse_iso(r.get("started_at"))
            if started and cutoff <= started < attribution_started_at:
                pre_attribution += float(r.get("cost_usd") or 0)
        pre_attribution += float(
            fetch_deadline_check_cost(cutoff, attribution_started_at).get("cost_usd") or 0)
    pre_attribution = round(min(pre_attribution, unattributed), 6)
    signed_out = round(max(0.0, unattributed - pre_attribution), 6)
    # Rate over the attributable period only — the period the number can actually speak to.
    attributable_total = round(max(0.0, interactive_total - pre_attribution), 6)

    # days + 1 buckets, anchored on today rather than counted forward from the cutoff:
    # range(days) stops one short, so the current day — where every figure above is still
    # accumulating — was dropped from the series entirely and the card read as frozen at
    # yesterday while the totals beside it moved.
    series = []
    today = now.date()
    for i in range(days, -1, -1):
        day = (today - datetime.timedelta(days=i)).isoformat()
        d = per_day.get(day)
        series.append({"date": day,
                       "cost_usd": round(d["cost_usd"], 6) if d else 0.0,
                       "users": len(d["users"]) if d else 0,
                       "calls": d["calls"] if d else 0})

    feature_list = sorted(
        ({"key": f["key"], "label": f["label"], "cost_usd": round(f["cost_usd"], 6),
          "calls": f["calls"], "users": len(f["users"]),
          "cost_per_call": round(f["cost_usd"] / f["calls"], 6) if f["calls"] else 0.0,
          "models": _group_untracked_feature_models(f["models"].values()),
          } for f in features.values()),
        key=lambda f: f["cost_usd"], reverse=True)

    def _shape(entry, **extra):
        """Common tail for a provider/model aggregate: round, count users, add a rate."""
        out = {k: v for k, v in entry.items() if k not in ("users", "models", "features")}
        out["cost_usd"] = round(entry["cost_usd"], 6)
        out["users"] = len(entry["users"])
        out["cost_per_call"] = (round(entry["cost_usd"] / entry["calls"], 6)
                                if entry["calls"] else 0.0)
        out.update(extra)
        return out

    provider_list = sorted(
        (_shape(pr, models=sorted(pr["models"])) for pr in providers.values()),
        key=lambda pr: pr["cost_usd"], reverse=True)
    model_list = sorted(
        (_shape(m, features=sorted(
            ({"key": k, "label": FEATURE_LABELS.get(k, k), "cost_usd": v}
             for k, v in m["features"].items()),
            key=lambda f: f["cost_usd"], reverse=True)) for m in models.values()),
        key=lambda m: m["cost_usd"], reverse=True)
    model_list = _group_untracked_models(model_list)

    # The most recent day with any spend at all, which is what the console leads with.
    # per_day only ever holds days that had activity, so max() IS "latest active day".
    latest_day = max(per_day) if per_day else None
    latest = per_day.get(latest_day) if latest_day else None
    last_activity_at = max((u["last_at"] for u in users.values() if u["last_at"]),
                           default=None)
    spenders = [u for u in users.values() if u["has_spend"]]
    costs = [u["cost_usd"] for u in spenders]
    return {
        "table_ready": True,
        # False means the table exists but predates the provider/model breakout. Every
        # figure is still correct; the model just reads as "(before model tracking)"
        # until user_costs_schema.sql is re-run.
        "model_ready": model_ready,
        "setup_sql_file": USER_COSTS_SETUP_SQL,
        "providers": provider_list,
        "models": model_list,
        "days": days,
        "price_per_month_usd": PLAN_PRICE_USD,
        "users": ranked,
        "users_truncated": truncated,
        "features": feature_list,
        "series": series,
        # Server's UTC "today", so the console labels its own column with the same day
        # boundary the rows were bucketed on rather than the viewer's local one.
        "today": today_iso,
        # When attribution began recording. Everything billed before it is unattributable
        # by construction, not evidence of signed-out traffic.
        "attribution_started_at": (attribution_started_at.isoformat()
                                   if attribution_started_at else None),
        "totals": {
            # Latest day with spend — NOT "today". See the by_day comment above: a UTC
            # calendar day rolls at 5pm Pacific, so a "today" figure reads $0.00 all
            # evening on a day that cost real money.
            "latest_day": latest_day,
            "latest_day_cost_usd": round(latest["cost_usd"], 6) if latest else 0.0,
            "latest_day_calls": latest["calls"] if latest else 0,
            "latest_day_users": len(latest["users"]) if latest else 0,
            "last_activity_at": last_activity_at,
            "attributed_cost_usd": attributed,
            "unattributed_cost_usd": unattributed,
            "pre_attribution_cost_usd": pre_attribution,
            "signed_out_cost_usd": signed_out,
            "attributable_total_usd": attributable_total,
            "interactive_total_usd": round(interactive_total, 6),
            # Measured against attributable spend, so the rate reflects how well
            # attribution works rather than how old the window is.
            "attribution_rate": (round(attributed / attributable_total, 4)
                                 if attributable_total else 0),
            "accounts_total": accounts_total,
            "accounts_with_spend": accounts_with_spend,
            "accounts_no_spend": max(0, accounts_total - accounts_with_spend),
            "active_users": len(spenders),
            "calls": sum(u["calls"] for u in users.values()),
            "web_searches": sum(u["web_searches"] for u in users.values()),
            "avg_cost_per_user": round(attributed / len(spenders), 6) if spenders else 0.0,
            "max_cost_per_user": round(max(costs), 6) if costs else 0.0,
            "users_over_plan": sum(1 for c in costs if c > PLAN_PRICE_USD),
        },
    }

# Mirrors PROFILE_SUFFICIENT_LENGTH in script.js — the bar the app itself already uses to
# decide a profile is usable for auto-matching, and the "aim for at least 20 words"
# guidance students are shown. Deliberately NOT a second threshold invented here: two
# definitions of "meaningful profile" would let this console and the app disagree about
# the same student. If the bar should move, move it in both places.
PROFILE_SUFFICIENT_WORDS = 20
# "Rich" is a side metric, never a funnel gate — a student can search and track with a
# 20-word profile, so putting this in the chain would drop people who are further along.
PROFILE_RICH_WORDS = 100
PROFILE_RICH_ROUNDS = 3

# The three keys AppStorage writes into users.data (see the top of script.js).
PROFILE_DATA_KEY = "student-profile"
TRACKER_DATA_KEY = "hs-tracker-data"
TRACKER_SAVED_KEY = "hs-tracker-saved"
# Mirrors ALL_BUCKETS in script.js.
TRACKER_BUCKETS = ("summerPrograms", "internships", "researchCompetitions",
                   "pureCompetitions", "conferences", "journals")

# A billed call under one of these features is direct evidence the student ran a search.
# It is only ever a PROXY: mock mode bills nothing, so with no API key this reads zero for
# everybody. The stage predicate below therefore also accepts "has tracked something",
# which cannot happen without a search having run.
SEARCH_FEATURES = ("ranking", "infer_subjects", "venue_search")

# The funnel is CUMULATIVE and strictly ordered: a user counts at stage N only if they
# satisfy stages 1..N. That is what makes the step-over-step percentages mean anything.
# Two consequences worth stating, because both were got wrong first:
#
#  * Every predicate must be genuinely implied by the ones after it, or the cumulative
#    rule silently drops people who are further along than the chain can see. That is why
#    `ran_search` accepts tracked items as evidence — the proxy is incomplete, the
#    implication is not.
#  * "Came back after signup day" is NOT in this chain even though it reads like stage 2.
#    Someone can build a profile and track five things on the day they sign up and never
#    return; putting return-visits in the chain would score them as a total failure. It is
#    reported beside the funnel as a side metric instead.
FUNNEL_STAGES = [
    ("signed_up",          "Signed up",              "An account exists."),
    ("saved_data",         "Saved anything",         "Any profile or tracker data reached the server."),
    ("has_profile",        "Has a profile",          "A synthesized profile with any text in it."),
    ("meaningful_profile", "Meaningful profile",     f"That profile is at least {PROFILE_SUFFICIENT_WORDS} words — the same bar the app uses."),
    ("ran_search",         "Ran a search",           "A billed ranking/subject call, or anything in the tracker (which needs a search)."),
    ("tracked_1",          "Tracked an opportunity", "At least one opportunity actively tracked, excluding saved-for-later."),
    ("tracked_3",          "Tracked 3 or more",      "A real shortlist rather than a single trial run."),
    ("action_started",     "Started the work",       "Moved at least one action item off Not Started."),
]
FUNNEL_STAGE_KEYS = [k for k, _, _ in FUNNEL_STAGES]
# Everything below this index on the funnel is an account that has not got a usable
# profile — the population the "trial ending, no profile" warning is about.
ACTIVATED_STAGE_INDEX = FUNNEL_STAGE_KEYS.index("meaningful_profile")


def _json_obj(value):
    """A dict out of a users.data entry, whatever shape it arrived in.

    /api/data/save JSON.parses before writing, so these are normally real jsonb objects —
    but AppStorage hands round JSON *strings* client-side, and a value written by any
    other path could be either. Tolerate both rather than scoring a real profile as absent.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def _profile_facts(data):
    """(profile text word count, chat rounds, has any profile) for one user's data blob."""
    profile = _json_obj((data or {}).get(PROFILE_DATA_KEY))
    text = (profile.get("synthesized") or "").strip()
    rounds = profile.get("chatRounds")
    return (len(text.split()) if text else 0,
            int(rounds) if isinstance(rounds, (int, float)) else 0,
            bool(text),
            # The derived-filter slots are set the first time a profile is actually used
            # to search, so their presence is search evidence that survives mock mode.
            bool(profile.get("filterValues") or profile.get("filterTags")))


def _tracker_facts(data):
    """(actively tracked count, any action item started) for one user's data blob.

    Saved-for-later items are excluded. `hs-tracker-saved` maps an item id to true when
    the student explicitly parked it, and renderTracker() in script.js already refuses to
    count those as actively tracked — counting them here would inflate the single most
    important number on the page.
    """
    tracker = _json_obj((data or {}).get(TRACKER_DATA_KEY))
    saved = _json_obj((data or {}).get(TRACKER_SAVED_KEY))
    count = 0
    started = False
    for bucket in TRACKER_BUCKETS:
        for item in (tracker.get(bucket) or []):
            if not isinstance(item, dict):
                continue
            if saved.get(str(item.get("id"))):
                continue
            count += 1
            for action in (item.get("actionItems") or []):
                # Three states, from NEXT_ACTION_STATE in script.js: not_started ->
                # in_progress -> completed. Anything off not_started is work begun.
                if isinstance(action, dict) and action.get("state") not in (None, "", "not_started"):
                    started = True
    return count, started


def _stage_flags(record, searched_ids, returned_ids):
    """The ordered stage predicates for one account, before the cumulative rule applies."""
    uid = str(record.get("userid") or "").strip().lower()
    data = record.get("data") or {}
    words, rounds, has_profile, has_filters = _profile_facts(data)
    tracked, action_started = _tracker_facts(data)
    return {
        "signed_up": True,
        "saved_data": bool(data),
        "has_profile": has_profile,
        "meaningful_profile": words >= PROFILE_SUFFICIENT_WORDS,
        # Proxy OR implication — see SEARCH_FEATURES.
        "ran_search": uid in searched_ids or has_filters or tracked > 0,
        "tracked_1": tracked >= 1,
        "tracked_3": tracked >= 3,
        "action_started": action_started,
        # --- side metrics, deliberately outside the ordered chain ---
        "_returned": uid in returned_ids,
        "_rich_profile": words >= PROFILE_SUFFICIENT_WORDS and (
            words >= PROFILE_RICH_WORDS or rounds >= PROFILE_RICH_ROUNDS),
        "_calendar": bool(record.get("google_calendar_connected_at")),
        "_google_signup": bool(record.get("google_id")),
        "_words": words,
        "_rounds": rounds,
        "_tracked": tracked,
    }


def _cumulative_stage(flags):
    """How far down the funnel this account got: the index before the first failed stage."""
    reached = 0
    for i, key in enumerate(FUNNEL_STAGE_KEYS):
        if not flags.get(key):
            break
        reached = i
    return reached


def fetch_user_activity(since_day):
    """{userid: set(day)} for every recorded day at/after since_day, or None if unavailable."""
    rows = _supabase_request("user_activity", params={
        "select": "userid,day", "day": f"gte.{since_day}",
        "order": "day.desc", "limit": "50000"})
    if rows is None:
        return None
    by_user = {}
    for r in rows:
        uid = str(r.get("userid") or "").strip().lower()
        if uid and r.get("day"):
            by_user.setdefault(uid, set()).add(r["day"])
    return by_user


def _activity_first_day():
    """The earliest day ever recorded — where the DAU chart is allowed to start.

    Charting back past this would draw a line along zero through a period nobody was
    measuring, which reads as "the app was dead" rather than "we weren't looking yet".
    """
    rows = _supabase_request("user_activity", params={
        "select": "day", "order": "day.asc", "limit": "1"})
    return (rows or [{}])[0].get("day") if rows else None


# ---------- The metrics themselves ----------

def _fetch_all_accounts():
    """Every account, paginated past PostgREST's 1000-row cap.

    Selects `*` on purpose: google_id and google_calendar_connected_at only exist once
    their own migrations have run, and PostgREST 400s an entire read on one unknown
    column — a named select would make this endpoint fail wholesale on a database that is
    merely missing an optional feature. Nothing sensitive from these rows is ever put in
    the response; see _shape_account.
    """
    page_size, offset, out = 1000, 0, []
    while True:
        batch = _users_request("GET", "?" + urllib.parse.urlencode({
            "select": "*", "order": "created_at.asc",
            "limit": str(page_size), "offset": str(offset)})) or []
        out.extend(batch)
        if len(batch) < page_size:
            return out
        offset += page_size


def _fetch_user_spend(since_day):
    """({userid: {cost, calls}}, {userids that ran a search}) from user_costs."""
    rows = _supabase_request("user_costs", params={
        "select": "userid,feature,calls,cost_usd", "day": f"gte.{since_day}",
        "limit": "20000"})
    spend, searched = {}, set()
    if rows is None:
        return spend, searched, False
    for r in rows:
        uid = str(r.get("userid") or "").strip().lower()
        if not uid:
            continue
        e = spend.setdefault(uid, {"cost_usd": 0.0, "calls": 0})
        e["cost_usd"] += float(r.get("cost_usd") or 0)
        e["calls"] += int(r.get("calls") or 0)
        if r.get("feature") in SEARCH_FEATURES:
            searched.add(uid)
    return spend, searched, True


def _fetch_mailing_list_users():
    rows = _supabase_request("mailing_list_subscriptions",
                             params={"select": "userid", "limit": "20000"})
    if rows is None:
        return set()
    return {str(r.get("userid") or "").strip().lower() for r in rows if r.get("userid")}


def _week_start(d):
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def get_user_metrics(days=30, limit=500):
    """Everything behind the console's Metrics view.

    Reads the `users` table (the funnel, plan mix and conversion — no migration needed),
    user_activity (DAU/WAU/retention — degrades to activity_ready: false) and user_costs
    (cost per user, and the search proxy). Never raises for a missing optional table.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date()
    window_start = today - datetime.timedelta(days=days)

    # Any counts still sitting in memory belong in this reading. Without this the console
    # trails the buffer by up to one flush interval, which looks like a stalled pipeline
    # on a quiet day.
    flush_user_activity()

    accounts = _fetch_all_accounts()
    spend, searched_ids, costs_ready = _fetch_user_spend(window_start.isoformat())
    mailing_ids = _fetch_mailing_list_users()

    # Retention needs history from before the window, so activity is read from the earlier
    # of (window start) and (oldest signup + 30d) — a 7-day window must still be able to
    # say whether a user who signed up in April ever came back.
    activity_since = min(window_start, today - datetime.timedelta(days=max(days, 60)))
    activity = fetch_user_activity(activity_since.isoformat())
    activity_ready = activity is not None
    activity = activity or {}
    first_recorded = _activity_first_day() if activity_ready else None

    returned_ids = set()
    for record in accounts:
        uid = str(record.get("userid") or "").strip().lower()
        created = _parse_iso(record.get("created_at"))
        if not uid or not created:
            continue
        signup_day = created.date().isoformat()
        if any(d > signup_day for d in activity.get(uid, ())):
            returned_ids.add(uid)

    # --- per-account facts ---
    shaped, stage_counts, direct_counts = [], [0] * len(FUNNEL_STAGES), [0] * len(FUNNEL_STAGES)
    stage_members = [[] for _ in FUNNEL_STAGES]
    stage_missing = [[] for _ in FUNNEL_STAGES]
    by_status, sides = {}, {"returned": 0, "rich_profile": 0, "deep_engagement": 0,
                            "calendar": 0, "mailing_list": 0, "google_signup": 0,
                            "terms_stale": 0, "terms_missing": 0}
    signups_by_day = {}
    # Kept beside `shaped` rather than inside it: "has a Stripe subscription id" is
    # conversion evidence the code below needs, not something the console should render.
    # Zipping `shaped` against `accounts` to recover it would misalign the moment one
    # account is skipped for a blank userid.
    has_stripe_sub = {}

    for record in accounts:
        uid = str(record.get("userid") or "").strip().lower()
        if not uid:
            continue
        flags = _stage_flags(record, searched_ids, returned_ids)
        reached = _cumulative_stage(flags)
        state = subscription_state(record)
        status = state["status"]
        by_status[status] = by_status.get(status, 0) + 1

        for i, key in enumerate(FUNNEL_STAGE_KEYS):
            if flags.get(key):
                direct_counts[i] += 1
            if i <= reached:
                stage_counts[i] += 1
                stage_members[i].append(uid)
            elif i == reached + 1:
                # The people this exact step lost. Held per-stage so clicking a bar can
                # name them without a second request — the whole point of the page at
                # this account count.
                stage_missing[i].append(uid)

        days_active = activity.get(uid, set())
        last_active = max(days_active) if days_active else None
        sides["returned"] += 1 if flags["_returned"] else 0
        sides["rich_profile"] += 1 if flags["_rich_profile"] else 0
        sides["calendar"] += 1 if flags["_calendar"] else 0
        sides["google_signup"] += 1 if flags["_google_signup"] else 0
        on_list = uid in mailing_ids
        sides["mailing_list"] += 1 if on_list else 0
        sides["deep_engagement"] += 1 if (flags["_calendar"] or on_list) else 0
        if not record.get("terms_accepted_at"):
            sides["terms_missing"] += 1
        elif record.get("terms_version") and record.get("terms_version") != TERMS_VERSION:
            sides["terms_stale"] += 1

        created = _parse_iso(record.get("created_at"))
        if created:
            signups_by_day[created.date().isoformat()] = \
                signups_by_day.get(created.date().isoformat(), 0) + 1

        has_stripe_sub[uid] = bool(record.get("stripe_subscription_id"))
        money = spend.get(uid) or {"cost_usd": 0.0, "calls": 0}
        name = " ".join(x for x in [record.get("first_name"), record.get("last_name")] if x).strip()
        shaped.append({
            "userid": uid,
            "name": name or None,
            "email": record.get("email"),
            "created_at": record.get("created_at"),
            "status": status,
            "has_access": state["has_access"],
            "days_left": state["days_left"],
            "trial_ends_at": state["trial_ends_at"],
            "subscription_end_at": state["subscription_end_at"],
            "stage": reached,
            "stage_key": FUNNEL_STAGE_KEYS[reached],
            "stage_label": FUNNEL_STAGES[reached][1],
            "activated": reached >= ACTIVATED_STAGE_INDEX,
            "profile_words": flags["_words"],
            "chat_rounds": flags["_rounds"],
            "tracked": flags["_tracked"],
            "returned": flags["_returned"],
            "calendar": flags["_calendar"],
            "mailing_list": on_list,
            "google_signup": flags["_google_signup"],
            "last_active": last_active,
            "active_days": len(days_active),
            "cost_usd": round(money["cost_usd"], 6),
            "calls": money["calls"],
            "margin_usd": round(PLAN_PRICE_USD - money["cost_usd"], 4),
            # Consent bookkeeping, so "who never saw the current Terms" is answerable here
            # rather than from the SQL editor.
            "is_adult": record.get("is_adult"),
            "terms_version": record.get("terms_version"),
            "terms_stale": bool(record.get("terms_version")
                                and record.get("terms_version") != TERMS_VERSION),
            # Distinct from stale, and more serious: these accounts predate consent
            # capture entirely, so there is no record they ever saw the documents. A
            # stale version at least names what they agreed to.
            "terms_missing": not record.get("terms_accepted_at"),
        })

    total_accounts = len(shaped)
    funnel = []
    for i, (key, label, description) in enumerate(FUNNEL_STAGES):
        prev = stage_counts[i - 1] if i else stage_counts[0]
        funnel.append({
            "key": key, "label": label, "description": description,
            "count": stage_counts[i],
            # What the predicate alone says, ignoring the cumulative rule. Equal to `count`
            # unless a stage's evidence is weaker than the stage below it — which is how a
            # proxy going stale announces itself instead of quietly deflating the funnel.
            "direct": direct_counts[i],
            "of_all": round(stage_counts[i] / total_accounts, 4) if total_accounts else 0,
            "step": round(stage_counts[i] / prev, 4) if prev else 0,
            "lost": max(0, prev - stage_counts[i]) if i else 0,
            "userids": stage_members[i][:limit],
            "missing_userids": stage_missing[i][:limit],
            "proxy": key == "ran_search",
        })
    # The single worst step, named in a sentence under the bars. A funnel that makes you
    # compute the diff yourself is just a table with rounded corners.
    biggest_drop = max(funnel[1:], key=lambda s: s["lost"], default=None)

    # --- subscriptions ---
    converted, eligible, granted = [], [], []
    for u in shaped:
        status = u["status"]
        # A canceled or past_due account that carries a Stripe subscription id did convert
        # at some point — cancelling later is churn, not a failure to convert, and folding
        # the two together would make the rate fall every time a paying user leaves.
        paid = status == "active" or (status in ("canceled", "past_due")
                                      and has_stripe_sub.get(u["userid"], False))
        if status == "beta":
            # A grant is not a conversion decision — they never reached the choice. Left
            # out of the denominator and reported on its own so the rate isn't diluted by
            # accounts that were handed access.
            granted.append(u["userid"])
            continue
        if paid:
            converted.append(u["userid"])
            eligible.append(u["userid"])
        elif status == "trial" and u["trial_ends_at"] and is_trial_expired(u["trial_ends_at"]):
            eligible.append(u["userid"])
    # Denominator is accounts whose trial has actually ENDED, never all accounts: with a
    # 7-day trial, dividing by everyone scores every signup from the last week as a
    # failure before they have had a chance to decide.
    conversion = {
        "converted": len(converted), "eligible": len(eligible),
        "rate": round(len(converted) / len(eligible), 4) if eligible else None,
        "granted": len(granted),
        "still_deciding": sum(1 for u in shaped if u["status"] == "trial"
                              and not (u["trial_ends_at"] and is_trial_expired(u["trial_ends_at"]))),
    }

    ending_soon = sorted(
        ({"userid": u["userid"], "name": u["name"], "email": u["email"],
          "status": u["status"], "days_left": u["days_left"],
          "ends_at": u["trial_ends_at"] or u["subscription_end_at"],
          "stage": u["stage"], "stage_label": u["stage_label"],
          # The actual worked list: about to lose access, with nothing to lose.
          "at_risk": u["stage"] < ACTIVATED_STAGE_INDEX}
         for u in shaped
         if u["status"] in ("trial", "beta") and u["has_access"] and u["days_left"] <= 2),
        key=lambda x: x["days_left"])

    promos = {}
    for record in accounts:
        for code in (record.get("promo_codes_used") or []):
            entry = promos.setdefault(str(code).upper(), {"code": str(code).upper(), "uses": 0})
            entry["uses"] += 1
    for code, entry in promos.items():
        definition = PROMO_CODES.get(code)
        # grant and checkout codes travel through different endpoints and one cannot be
        # redeemed through the other's path, so the kind is not decoration.
        entry["kind"] = promo_kind(definition) if definition else "unknown"
        entry["description"] = (definition or {}).get("description")

    # --- series ---
    signup_series, activity_series = [], []
    for i in range(days, -1, -1):
        day = (today - datetime.timedelta(days=i)).isoformat()
        signup_series.append({"date": day, "count": signups_by_day.get(day, 0)})
        if activity_ready and (first_recorded is None or day >= first_recorded):
            activity_series.append({"date": day,
                                    "dau": sum(1 for d in activity.values() if day in d)})

    def _active_within(n):
        cutoff = (today - datetime.timedelta(days=n - 1)).isoformat()
        return sum(1 for d in activity.values() if any(x >= cutoff for x in d))

    dau = wau = mau = None
    if activity_ready:
        dau, wau, mau = _active_within(1), _active_within(7), _active_within(30)

    # --- retention cohorts, by signup week ---
    cohorts = []
    if activity_ready:
        grouped = {}
        for u in shaped:
            created = _parse_iso(u["created_at"])
            if created:
                grouped.setdefault(_week_start(created.date()), []).append(u)
        for week in sorted(grouped, reverse=True):
            members = grouped[week]
            row = {"cohort": week, "n": len(members)}
            for n in (1, 3, 7, 14):
                # Only ask the question of cohorts old enough to answer it. A cohort that
                # signed up yesterday has not failed D7; it has not reached it, and a 0%
                # there would read as churn.
                mature = [u for u in members
                          if (_parse_iso(u["created_at"]).date()
                              + datetime.timedelta(days=n)) <= today]
                if not mature:
                    row[f"d{n}"] = None
                    continue
                kept = 0
                for u in mature:
                    threshold = (_parse_iso(u["created_at"]).date()
                                 + datetime.timedelta(days=n)).isoformat()
                    if any(d >= threshold for d in activity.get(u["userid"], ())):
                        kept += 1
                row[f"d{n}"] = {"kept": kept, "of": len(mature),
                                "rate": round(kept / len(mature), 4)}
            cohorts.append(row)

    active_count = by_status.get("active", 0)
    activated = sum(1 for u in shaped if u["activated"])
    window_spend = round(sum(u["cost_usd"] for u in shaped), 6)
    result = {
        "days": days,
        "generated_at": now.isoformat(),
        "activity_ready": activity_ready,
        "activity_since": first_recorded,
        "costs_ready": costs_ready,
        "setup_sql": {"activity": USER_ACTIVITY_SETUP_SQL, "snapshots": USER_METRICS_SETUP_SQL},
        "price_per_month_usd": PLAN_PRICE_USD,
        "totals": {
            "accounts": total_accounts,
            "signups_in_window": sum(s["count"] for s in signup_series),
            "dau": dau, "wau": wau, "mau": mau,
            "stickiness": round(dau / mau, 4) if (dau and mau) else None,
            "activated": activated,
            "activated_rate": round(activated / total_accounts, 4) if total_accounts else 0,
            "paying": active_count,
            "mrr_usd": round(active_count * PLAN_PRICE_USD, 2),
            "ending_soon": len(ending_soon),
            "at_risk": sum(1 for e in ending_soon if e["at_risk"]),
            "window_cost_usd": window_spend,
            "cost_per_activated_usd": round(window_spend / activated, 6) if activated else None,
        },
        "funnel": funnel,
        "biggest_drop": ({"key": biggest_drop["key"], "label": biggest_drop["label"],
                          "lost": biggest_drop["lost"]}
                         if biggest_drop and biggest_drop["lost"] else None),
        "side_metrics": sides,
        "signups_by_day": signup_series,
        "activity_by_day": activity_series,
        "cohorts": cohorts,
        "subscriptions": {
            "by_status": by_status,
            "conversion": conversion,
            "ending_soon": ending_soon,
            "promos": sorted(promos.values(), key=lambda p: p["uses"], reverse=True),
            "mrr_usd": round(active_count * PLAN_PRICE_USD, 2),
            "margin_usd": round(active_count * PLAN_PRICE_USD - window_spend, 4),
        },
        "users": sorted(shaped, key=lambda u: (u["stage"], u["created_at"] or ""), reverse=True)[:limit],
    }
    record_metrics_snapshot(result)
    result["snapshots_ready"] = _metrics_snapshot_available
    return result


# ---------- The daily snapshot ----------
_snapshot_lock = threading.Lock()
_metrics_snapshot_available = True
_snapshot_last_write = {"day": None, "at": 0.0}
SNAPSHOT_MIN_INTERVAL = 300.0   # seconds between rewrites of today's row


def record_metrics_snapshot(metrics):
    """Freeze today's funnel and plan mix into user_metrics_daily.

    Written from the read path rather than on a schedule because there is no scheduler
    here, and because the alternative — computing it twice — is the kind of duplication
    that lets a chart and a tile disagree. Today's row is rewritten as the day progresses;
    past days stay as they last read.

    Throttled to one write per SNAPSHOT_MIN_INTERVAL so an open console tab polling the
    endpoint doesn't turn into a write every refresh.
    """
    global _metrics_snapshot_available
    if not _metrics_snapshot_available:
        return
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    now = time.time()
    with _snapshot_lock:
        if (_snapshot_last_write["day"] == day
                and now - _snapshot_last_write["at"] < SNAPSHOT_MIN_INTERVAL):
            return
        _snapshot_last_write.update({"day": day, "at": now})
    totals = metrics["totals"]
    row = {
        "day": day,
        "accounts": totals["accounts"],
        "funnel": {s["key"]: s["count"] for s in metrics["funnel"]},
        "by_status": metrics["subscriptions"]["by_status"],
        "mrr_usd": totals["mrr_usd"],
        # NULL rather than 0 when activity isn't set up: a zero here is indistinguishable
        # from a genuinely dead day, and these rows can never be recomputed.
        "dau": totals["dau"], "wau": totals["wau"], "mau": totals["mau"],
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        _supabase_request_strict(
            "user_metrics_daily", method="POST", data=[row],
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    except Exception as e:
        if _missing_table_error(e):
            _metrics_snapshot_available = False
            print(f"[WARN] user_metrics_daily unavailable - no history is being recorded, "
                  f"and it cannot be backfilled later. Run {USER_METRICS_SETUP_SQL} in "
                  f"the Supabase SQL editor.")
            return
        print(f"[WARN] Could not write today's metrics snapshot: {e}")


def fetch_metrics_snapshots(days=30):
    """Past days' funnel counts, for the trend lines. [] when the table isn't there."""
    since = (datetime.datetime.now(datetime.timezone.utc).date()
             - datetime.timedelta(days=days)).isoformat()
    rows = _supabase_request("user_metrics_daily", params={
        "select": "day,accounts,funnel,by_status,mrr_usd,dau,wau,mau",
        "day": f"gte.{since}", "order": "day.asc", "limit": "400"})
    return rows or []


# ---------- Committing a dry-run snapshot, and activating what the scraper found ----------
# Two operations that write to the catalog but call no API and cost nothing. They are the
# other half of the three run tiers: --dry-run already pays full price for an answer, and
# a scrape already writes its rows inactive, so both of those leave work sitting on disk
# or behind a flag with no way to act on it short of paying again.

def _commit_patch(opp_id, updates):
    """PATCH one opportunity, raising on failure so commit_snapshot can count it."""
    query = urllib.parse.urlencode({"id": f"eq.{opp_id}"})
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        data=json.dumps(updates).encode(), method="PATCH",
        headers={**_supabase_headers(), "Content-Type": "application/json",
                 "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def _commit_insert(rows):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities",
        data=json.dumps(rows).encode(), method="POST",
        headers={**_supabase_headers(), "Content-Type": "application/json",
                 "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def _existing_opportunity_urls():
    """Every URL already in the table, normalized — the dedupe set for snapshot inserts.

    Reads ALL rows, not just active ones: a row sitting inactive in the review queue is
    still a row, and re-inserting it would put a duplicate in front of the reviewer.
    """
    urls = set()
    offset, page_size = 0, 1000
    while True:
        page = _supabase_request("opportunities", params={
            "select": "url", "limit": str(page_size), "offset": str(offset)}) or []
        urls.update(dryrun_common.normalize_url(r.get("url")) for r in page if r.get("url"))
        if len(page) < page_size:
            break
        offset += page_size
    return urls


def commit_dryrun_snapshot(file_name, dry=False):
    """Apply a snapshot's pending writes. Free — no API call happens anywhere in here.

    A real commit is logged to agent_runs with a `-commit` mode suffix and cost_usd 0. That
    pairs correctly with the `-dryrun` row the original run already wrote: the dry run
    carries the cost and no row counts, the commit carries the row counts and no cost, and
    between them they describe one logical operation without double-counting either.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}
    result = dryrun_common.commit_snapshot(
        file_name, _commit_patch, _commit_insert, _existing_opportunity_urls, dry=dry)
    if dry or not result.get("ok") or not result.get("applied"):
        return result

    agent = result["agent"]
    db_agent = AGENT_CONFIGS_SCHEMA.get(agent, {}).get("db_agent", agent)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    inserted = result["kind"] == "insert"
    _supabase_request("agent_runs", method="POST", data=[{
        "agent": db_agent,
        "mode": "snapshot-commit",
        "started_at": now, "finished_at": now,
        "items_processed": result["entries"],
        "items_updated": 0 if inserted else result["applied"],
        "items_added": result["applied"] if inserted else 0,
        "errors": result["errors"], "cost_usd": 0,
        "total_web_searches": 0,
        "notes": f"Committed dry-run snapshot {file_name} — no API calls, cost already "
                 f"paid by the original run.",
    }])
    invalidate_runs_cache()
    # Committed rows must not wait out OPPORTUNITIES_CACHE_TTL before they are visible.
    with _opportunities_cache_lock:
        _opportunities_cache["fetched_at"] = 0.0
    return result


# The columns user_submissions_schema.sql adds. Selected separately from the base row so a
# database that has not run that migration still gets a working queue (see the ladder in
# list_pending_opportunities) rather than a blank one — PostgREST 400s the WHOLE select on
# one unknown column, so a single wide select would take the queue down entirely.
# Carries every EDITABLE_OPPORTUNITY_FIELDS column so the console's edit modal can prefill
# from the list it already has, without a per-row round-trip.
_BASE_PENDING_SELECT = ("id,name,org,type,url,summary,source,created_at,review_status,state,"
                        "price,category,cost,location,intl,season,eligibility,grade_min,"
                        "grade_max,subject_tags,contact_email")
_MODERATION_SELECT = ("moderation_status,submitted_by,submitted_at,reviewed_by,reviewed_at,"
                      "duplicate_of,dup_candidates,quality_flags")

# WHY a row was rejected — labeled training data for the scraper (see
# user_submissions_schema.sql, which must be re-run to add the column). Stored as
# "code" or "code: note"; the console owns the code list, the server only bounds length.
_MODERATION_REASON_COLUMN = "moderation_reason"
MODERATION_REASON_MAX_LEN = 500

# A human's verdict on a queued row. Mirrors the CHECK constraint in
# user_submissions_schema.sql — keep the two in step or a write here 400s.
MODERATION_STATUSES = ("pending_review", "approved", "rejected", "duplicate",
                       "suspected_duplicate")

# Statuses that mean "a human has already dealt with this", i.e. the rows the queue hides.
# These force is_active=False.
ADJUDICATED_STATUSES = ("rejected", "duplicate")

# Flag-in-place: the strict offline dedupe sweep marks a row it suspects is a duplicate but
# leaves it LIVE. Unlike an adjudicated status this does NOT deactivate — a suspected
# duplicate is still a working opportunity, and hiding it on a guess would pull a real
# program from students until a human looked (the cost check_links never pays, because a dead
# link really is broken). It surfaces in the 'flagged' queue slice for a human to release
# (Activate -> approved, stays live) or confirm (moderate -> duplicate, which then deactivates).
FLAGGED_STATUSES = ("suspected_duplicate",)

# Statuses that carry a duplicate_of survivor pointer, and statuses that may carry a reason.
POINTER_STATUSES = ("duplicate", "suspected_duplicate")
STATUSES_WITH_REASON = ADJUDICATED_STATUSES + FLAGGED_STATUSES

# The default 'queue' slice: awaiting a human, still inactive — neither adjudicated away nor
# flagged-in-place (a suspected_duplicate is active and lives in its own slice).
QUEUE_STATUSES = tuple(s for s in MODERATION_STATUSES
                       if s not in ADJUDICATED_STATUSES and s not in FLAGGED_STATUSES)


def list_pending_opportunities(limit=500, source=None, status="queue"):
    """Rows sitting at is_active=false, i.e. the review queue.

    Nothing in this repo ever flips is_active automatically — a scrape and a user
    submission both write inactive and stay inactive until a person acts here. That is
    deliberate: the scraper can and does return plausible-looking rows that are wrong, and
    the catalog is what students see.

    `status` selects which slice of the queue:
      queue     — awaiting a human (moderation_status NULL, pending_review, or approved).
                  NULL is the pre-migration/scraper case and means "never adjudicated",
                  NOT "rejected", so it must stay in the queue.
      rejected  — already adjudicated away (rejected/duplicate). Kept reachable so a
                  mistaken rejection can be put back without a database console.
      flagged   — flag-in-place: rows still LIVE (is_active=true) that the dedupe sweep
                  suspects are duplicates. This is the one slice that lists ACTIVE rows —
                  they still show to students and need a human to release or confirm them.
      all       — everything inactive, which is what the pre-moderation queue showed.
    """
    base = {
        "order": "created_at.desc,id.desc",
        "limit": str(max(1, min(limit, 2000))),
    }
    if source:
        base["source"] = f"eq.{source}"

    filters = {}
    if status == "flagged":
        # The only slice that lists is_active=TRUE rows: a suspected duplicate is left live
        # on purpose, so it cannot come from the is_active=false base every other slice uses.
        base["is_active"] = "eq.true"
        filters["moderation_status"] = f"in.({','.join(FLAGGED_STATUSES)})"
    else:
        base["is_active"] = "eq.false"
        if status == "queue":
            # NOT IN would drop the NULL rows too — in SQL, NULL NOT IN (...) is NULL, not
            # true. Every scraper row has a NULL moderation_status, so that would empty the
            # queue outright. Spell the null case out.
            filters["or"] = ("(moderation_status.is.null,moderation_status.in."
                             f"({','.join(QUEUE_STATUSES)}))")
        elif status == "rejected":
            filters["moderation_status"] = f"in.({','.join(ADJUDICATED_STATUSES)})"

    # Degrade one step at a time rather than failing: moderation_reason arrived after the
    # other moderation columns (2026-08-25), so a DB migrated before then must keep its
    # working queue and reject button — only the reason column reads as missing. Without
    # any migration there is no moderation_status to select or filter on either, and a
    # queue with no reject button beats no queue at all.
    attempts = [
        (dict(base, select=f"{_BASE_PENDING_SELECT},{_MODERATION_SELECT},"
                           f"{_MODERATION_REASON_COLUMN}", **filters), True, True),
        (dict(base, select=f"{_BASE_PENDING_SELECT},{_MODERATION_SELECT}", **filters),
         True, False),
        (dict(base, select=_BASE_PENDING_SELECT), False, False),
    ]
    rows, moderation_ready, reason_ready = None, False, False
    for params, ready, r_ready in attempts:
        rows = _supabase_request("opportunities", params=params)
        if rows is not None:
            moderation_ready, reason_ready = ready, r_ready
            break
    if rows is None:
        return {"ok": False, "error": "Could not read opportunities from Supabase."}
    if not moderation_ready and status in ("rejected", "flagged"):
        # Nothing can have been rejected or flagged yet if the column does not exist — and
        # for `flagged` the fallback select (no moderation filter) would otherwise return the
        # whole LIVE catalog, since that slice reads is_active=true.
        rows = []

    sources, statuses = {}, {}
    for r in rows:
        key = r.get("source") or "(no source)"
        sources[key] = sources.get(key, 0) + 1
        skey = r.get("moderation_status") or "(never reviewed)"
        statuses[skey] = statuses.get(skey, 0) + 1
    return {
        "ok": True,
        "total": len(rows),
        "opportunities": rows,
        "status": status,
        # {id: {name,url,is_active}} for the rows marked `duplicate` point at, so the queue
        # can name the survivor rather than showing a bare id.
        "duplicate_targets": _duplicate_targets(rows) if moderation_ready else {},
        # False means user_submissions_schema.sql has not been run: the console shows the
        # setup step and hides the reject controls instead of offering a button that 400s.
        "moderation_ready": moderation_ready,
        # False (with moderation_ready true) means the file predates moderation_reason and
        # must be RE-RUN: rejects still work, but the why is dropped with a notice.
        "reason_ready": reason_ready,
        "moderation_sql": "user_submissions_schema.sql",
        "statuses": statuses,
        "sources": sorted(({"source": k, "count": v} for k, v in sources.items()),
                          key=lambda x: x["count"], reverse=True),
    }


# The 7 values `type` actually holds across the catalog. It is a clean enum (unlike
# `category`, which carries legacy junk — 'COMPETITION', 'SUMMER_PROGRAM', mixed case — and
# is therefore left as free text here). A typo'd type would make the row invisible to the
# finder's KIND_CONFIG lookup rather than merely ugly, so this one is validated.
OPPORTUNITY_TYPES = ("Program", "Internship", "Competition", "Research", "Volunteer",
                     "Conference", "Journal")

# What a reviewer may fix before a row goes in front of students, and how each value is
# coerced. Deliberately NOT editable here:
#   is_active / moderation_status  — those are the Activate and Reject buttons, which carry
#                                    their own confirmations and cache invalidation.
#   id, source, created_at         — provenance. Editing it would erase where a row came from.
#   review_status/review_summary/review_sources/last_reviewed_at — check_reviews.py owns these.
#   status/important_dates/was_estimated/dates_last_checked_at   — check_deadlines.py owns
#                                    these, and a hand-typed date would be silently
#                                    overwritten by the next deadline check anyway.
EDITABLE_OPPORTUNITY_FIELDS = {
    "name": "text", "org": "text", "summary": "text", "url": "text",
    "type": "text", "category": "text", "price": "text", "cost": "text",
    "state": "text", "location": "text", "intl": "text", "season": "text",
    "eligibility": "text", "contact_email": "text",
    "grade_min": "int", "grade_max": "int",
    "subject_tags": "list",
}


def _coerce_field(key, kind, value):
    """One editable value → what the column wants, or raise ValueError with a message the
    console can show verbatim."""
    if kind == "int":
        if value in (None, "", "—"):
            return None
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number or blank.")
        if not 1 <= n <= 13:
            raise ValueError(f"{key} must be a grade between 1 and 13.")
        return n
    if kind == "list":
        if value in (None, ""):
            return None
        if isinstance(value, str):
            # The console sends a comma-separated string because that is what a text field
            # produces; the column is a Postgres array either way.
            value = [p.strip() for p in value.split(",")]
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list or a comma-separated string.")
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        return cleaned or None
    text = "" if value is None else str(value).strip()
    return text or None


def update_pending_opportunity(opp_id, fields):
    """Edit a queued row in place, before anyone activates it.

    Scoped to inactive rows on purpose. The whole point of the queue is to fix a row while
    it is still hidden — letting this endpoint touch a live row would turn it into a
    general catalog editor with no audit trail and no confirmation, which is not what the
    Activate/Reject buttons around it lead an operator to expect.
    """
    opp_id = str(opp_id or "").strip()
    if not opp_id:
        return {"ok": False, "error": "No opportunity id given."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}

    unknown = [k for k in (fields or {}) if k not in EDITABLE_OPPORTUNITY_FIELDS]
    if unknown:
        # PostgREST would 400 the whole PATCH on an unknown key anyway; say which one.
        return {"ok": False, "error": f"Not editable here: {', '.join(sorted(unknown))}."}

    existing = _supabase_request("opportunities", params={
        "select": "id,is_active,name,url", "id": f"eq.{opp_id}", "limit": "1"})
    if existing is None:
        return {"ok": False, "error": "Could not read that opportunity from Supabase."}
    if not existing:
        return {"ok": False, "error": f"No opportunity with id {opp_id}."}
    if existing[0].get("is_active"):
        return {"ok": False, "error": "That row is live in the catalog. Only rows still in "
                                      "the review queue can be edited here."}

    updates = {}
    try:
        for key, value in (fields or {}).items():
            updates[key] = _coerce_field(key, EDITABLE_OPPORTUNITY_FIELDS[key], value)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    # Name and URL are what the row IS — blanking either leaves an unreviewable stub, and
    # an empty url takes the row out of dedupe entirely.
    for required in ("name", "url"):
        if required in updates and not updates[required]:
            return {"ok": False, "error": f"{required} cannot be empty."}
    if updates.get("type") and updates["type"] not in OPPORTUNITY_TYPES:
        return {"ok": False, "error": f"type must be one of {', '.join(OPPORTUNITY_TYPES)}."}
    if updates.get("url") and not str(updates["url"]).lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http:// or https://."}
    if updates.get("state") and len(updates["state"]) != 2:
        return {"ok": False, "error": "state is the 2-letter US code (or blank)."}
    lo, hi = updates.get("grade_min"), updates.get("grade_max")
    if lo is not None and hi is not None and lo > hi:
        return {"ok": False, "error": "grade_min cannot be above grade_max."}
    if not updates:
        return {"ok": False, "error": "Nothing to change."}

    updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        _commit_patch(opp_id, updates)
    except Exception as e:
        if _is_missing_column_error(e):
            return {"ok": False, "error": f"Supabase rejected a column in {sorted(updates)} "
                                          f"— the schema has drifted from this whitelist."}
        return {"ok": False, "error": f"Update failed: {str(e)[:200]}"}

    row = _supabase_request("opportunities", params={
        "select": f"{_BASE_PENDING_SELECT},{_MODERATION_SELECT}",
        "id": f"eq.{opp_id}", "limit": "1"}) or []
    # The row is inactive, so the public cache cannot be holding it — no invalidation needed.
    return {"ok": True, "id": opp_id, "updated": sorted(k for k in updates if k != "updated_at"),
            "opportunity": row[0] if row else None}


def search_opportunities(q, limit=25):
    """Find a surviving row to point a duplicate at. Whole catalog, active or not.

    Deliberately searches inactive rows too: the survivor of two queued scrapes is another
    queued row, and restricting this to the live catalog would make that pair unresolvable.
    """
    q = str(q or "").strip()
    if len(q) < 2:
        return {"ok": True, "total": 0, "opportunities": [],
                "note": "Type at least two characters."}
    select = "id,name,org,url,type,is_active,source,moderation_status"
    limit = str(max(1, min(int(limit or 25), 100)))

    # An id is the unambiguous case and is what dup_candidates carries, so try it first
    # and exactly — an ilike on a 7-character id would also match by luck.
    exact = _supabase_request("opportunities", params={
        "select": select, "id": f"eq.{q}", "limit": "1"}) or []

    # PostgREST's or=() list is comma-separated and paren-delimited, so a comma or paren
    # inside the term would be parsed as syntax rather than as text. Strip them rather than
    # trying to quote: this is a search box, and a slightly broader match is harmless.
    safe = re.sub(r"[,()*%]", " ", q).strip()
    rows = []
    if safe:
        rows = _supabase_request("opportunities", params={
            "select": select,
            "or": f"(name.ilike.*{safe}*,org.ilike.*{safe}*,url.ilike.*{safe}*)",
            "order": "is_active.desc,name.asc",
            "limit": limit}) or []

    seen, merged = set(), []
    for row in exact + rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        merged.append(row)
    return {"ok": True, "total": len(merged), "opportunities": merged}


def _duplicate_targets(rows):
    """{id: {name, url, is_active}} for every duplicate_of a queue page points at.

    One extra request, and only when something is actually marked duplicate — the console
    otherwise has an id it cannot render as anything a reader recognises.
    """
    ids = sorted({(r.get("duplicate_of") or "").strip() for r in rows
                  if (r.get("duplicate_of") or "").strip()})
    if not ids:
        return {}
    found = _supabase_request("opportunities", params={
        "select": "id,name,url,is_active",
        "id": f"in.({','.join(ids)})", "limit": str(len(ids))}) or []
    return {row["id"]: row for row in found}


def _moderation_updates(status, duplicate_of, reason, now):
    """The PATCH body for one moderation verdict. Pure — everything testable lives here.

    The reason is stored only on adjudicated-away verdicts (that is where the scraper
    signal is) and is ALWAYS written, like duplicate_of: restoring a row must clear the
    old why, or it keeps explaining a verdict that no longer stands. A duplicate with no
    reason given gets one derived from its survivor, so the label is never blank for the
    one verdict whose cause is already known.
    """
    reason = str(reason or "").strip()[:MODERATION_REASON_MAX_LEN]
    if status == "duplicate" and not reason:
        reason = f"duplicate: superseded by {duplicate_of}"
    updates = {
        "moderation_status": status,
        "reviewed_by": "admin-console",
        "reviewed_at": now,
        "updated_at": now,
        # Always written, never merely set: restoring or rejecting a row that was previously
        # marked duplicate (or suspected) must clear the pointer, or it keeps naming a
        # survivor for a relationship that no longer exists.
        "duplicate_of": (duplicate_of or None) if status in POINTER_STATUSES else None,
        _MODERATION_REASON_COLUMN: reason if status in STATUSES_WITH_REASON and reason else None,
    }
    if status in ADJUDICATED_STATUSES:
        # An adjudicated-away row must not be left visible to students, whatever it was
        # before. Approving, by contrast, does NOT activate: that stays an explicit,
        # separate decision on the Activate button. A flag-in-place (suspected_duplicate) is
        # deliberately absent here too — the whole point is that it stays LIVE until a human
        # either releases it (approve) or confirms it (duplicate, which lands here).
        updates["is_active"] = False
    return updates


def _ROUNDUP_REJECT_REASON():
    """The reject reason that routes a page back into discovery, read from the one place that
    defines it. Imported lazily so the console still loads if the offline layer is unavailable."""
    try:
        import discovered_leads
        return discovered_leads.ROUNDUP_REJECT_REASON
    except Exception:
        return "third-party-roundup"


def _queue_roundup_leads_async(ids):
    """Classify the just-rejected round-ups and queue them, on a background thread."""
    def work():
        try:
            import discovered_leads
            rows = supabase_get(SUPABASE_URL, "opportunities",
                                {"select": "id,name,url,seed_id",
                                 "id": f"in.({','.join(ids)})"}, SUPABASE_SERVICE_KEY) or []
            leads, _trace = discovered_leads.from_rejected_rows(
                rows, known_keys=discovered_leads.lead_keys(discovered_leads.load_leads()),
                confirmed=True)
            n = discovered_leads.append_leads(leads)
            if n:
                print(f"[leads] queued {n} round-up(s) from the review queue.")
        except Exception as e:
            # Never surfaced to the operator: the rejection already succeeded, and a failure
            # here only means the page has to be picked up by the backfill sweep instead.
            print(f"[leads] could not queue rejected round-ups: {str(e)[:160]}")
    threading.Thread(target=work, daemon=True).start()


def moderate_opportunities(ids, status, reviewed_by="admin-console", duplicate_of=None,
                           reason=None):
    """Record a human verdict on an explicit list of queued rows.

    This is the counterpart to activate_opportunities: that one says "students should see
    this", this one says "a person looked and decided". They are separate because
    is_active alone cannot express "reviewed and declined" — without this, a junk row sits
    inactive forever and gets re-triaged every time the queue is opened, and the queue only
    ever grows.

    Rejecting never deletes. The row stays in the table on purpose: its URL keeps blocking
    re-submission through url_dedupe, and the decision is reversible by moderating it back
    to pending_review.
    """
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if status not in MODERATION_STATUSES:
        return {"ok": False,
                "error": f"Unknown moderation status: {status!r}. "
                         f"Expected one of {', '.join(MODERATION_STATUSES)}."}
    if not ids:
        return {"ok": False, "error": "No opportunity ids given."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}

    duplicate_of = str(duplicate_of or "").strip()
    # A survivor pointer belongs only to the two statuses that name one. It is REQUIRED for a
    # hard 'duplicate' (a duplicate with no survivor reads as a mislabelled rejection with
    # nowhere to follow to) and OPTIONAL for 'suspected_duplicate' (the sweep may flag a
    # cluster before a survivor is settled), but when present it is validated identically.
    if duplicate_of and status not in POINTER_STATUSES:
        return {"ok": False, "error": "duplicate_of only applies to the 'duplicate' or "
                                      "'suspected_duplicate' status."}
    if status == "duplicate" and not duplicate_of:
        return {"ok": False, "error": "Marking a row as a duplicate needs the id of the "
                                      "row it duplicates."}
    if duplicate_of:
        if duplicate_of in ids:
            return {"ok": False, "error": "A row cannot be a duplicate of itself."}
        target = _supabase_request("opportunities", params={
            "select": "id,name,moderation_status", "id": f"eq.{duplicate_of}", "limit": "1"})
        if target is None:
            return {"ok": False, "error": "Could not check the surviving row in Supabase."}
        if not target:
            return {"ok": False, "error": f"No opportunity with id {duplicate_of} to point at."}
        # Pointing at a row that was itself rejected or duplicated makes a chain the queue
        # has no way to follow, and quietly loses the real survivor.
        if target[0].get("moderation_status") in ADJUDICATED_STATUSES:
            return {"ok": False,
                    "error": f"{duplicate_of} is itself marked "
                             f"{target[0]['moderation_status']} — point at the row that "
                             f"survives, not at another discarded one."}

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    updates = _moderation_updates(status, duplicate_of, reason, now)
    updates["reviewed_by"] = reviewed_by

    done, errors, details = 0, 0, []
    payload, reason_dropped = updates, False
    for opp_id in ids:
        try:
            try:
                _commit_patch(opp_id, payload)
            except Exception as e:
                # moderation_reason arrived after the other moderation columns, so a DB
                # migrated before 2026-08-25 rejects the whole PATCH over that one key.
                # Drop the reason and keep the verdict — losing the why must never cost
                # the decision itself — and latch for the rest of the batch.
                if not (_is_missing_column_error(e)
                        and _MODERATION_REASON_COLUMN in payload):
                    raise
                payload = {k: v for k, v in updates.items()
                           if k != _MODERATION_REASON_COLUMN}
                reason_dropped = True
                _commit_patch(opp_id, payload)
            done += 1
        except Exception as e:
            if _is_missing_column_error(e):
                return {"ok": False, "moderation_ready": False,
                        "error": "The moderation columns do not exist yet. Run "
                                 "user_submissions_schema.sql in the Supabase SQL editor "
                                 "(it is idempotent), then restart the server."}
            errors += 1
            if len(details) < 5:
                details.append(f"{opp_id}: {str(e)[:160]}")

    if done and status in ADJUDICATED_STATUSES:
        with _opportunities_cache_lock:
            _opportunities_cache["fetched_at"] = 0.0
    # Feed the round-ups straight back in. The trigger is the REASON, not the rejection: the
    # operator looked at the page and said it lists many programs, which is better evidence than
    # anything this pipeline can derive. Every other reason says nothing about that and routes
    # nothing. Fire-and-forget on purpose — rejecting is the operation that matters, and a slow
    # or hung fetch must never make the button wait or the verdict fail.
    if done and status == "rejected" and reason == _ROUNDUP_REJECT_REASON():
        _queue_roundup_leads_async(ids)
    result = {"ok": errors == 0, "moderated": done, "status": status,
              "errors": errors, "error_details": details}
    if reason_dropped:
        result["reason_dropped"] = True
        result["note"] = ("The verdict was recorded, but the reason was dropped: the "
                          "moderation_reason column does not exist yet. Re-run "
                          "user_submissions_schema.sql (idempotent) to keep reasons.")
    return result


def _pick_survivor(a, b):
    """Suggest which of two suspected-duplicate rows to KEEP and which to FLAG.

    Higher score keeps: a human-approved row, then a live row, then the more specific
    (deeper-path) URL. Only a suggestion — the operator can swap keep/flag in the console
    before anything is written. Returns (keep, flag).
    """
    import url_dedupe

    def score(r):
        s = 0
        if r.get("moderation_status") == "approved":
            s += 8
        if r.get("is_active"):
            s += 4
        try:
            _, _, path, _ = url_dedupe.split_url(r.get("url") or "")
            s += min(len([seg for seg in path.split("/") if seg]), 3)
        except Exception:
            pass
        return s

    sa, sb = score(a), score(b)
    if sb > sa:
        return b, a
    return a, b  # tie keeps `a`, which the finder orders id.asc (the incumbent)


def duplicate_report_pairs(limit=2000):
    """Run the read-only catalog duplicate finder and return FLAG-ELIGIBLE pairs.

    find_catalog_dups.fetch_all_rows only reads active rows, so every pair here is flag-eligible
    by construction — both rows live, which is exactly when flag-in-place (suspected_duplicate)
    is the right tool. A pair involving an inactive row belongs in the ordinary queue's Duplicate
    action instead, not here. Writes nothing; this only proposes.

    As of 2026-08-30 this runs on the SAME embedding + dedupe_confidence tier engine
    dedupe_queue.py uses for the review queue (find_catalog_dups.find_duplicate_pairs), not a
    separate free heuristic scan — that scan (URL/name-similarity/acronym cuts) is retired. See
    find_catalog_dups.py's module docstring for why: it was three hand-tuned approximations of
    what one semantic-similarity pass now does directly, on rows already embedded for free.
    """
    import find_catalog_dups as fcd
    import dedupe_confidence as dc

    if not SUPABASE_URL:
        return {"ok": False, "error": "SUPABASE_URL not configured."}
    key = SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY
    try:
        rows = fcd.fetch_all_rows(SUPABASE_URL, key)
    except Exception as e:
        return {"ok": False, "error": f"Could not read the catalog: {str(e)[:200]}"}

    verdicts, unembedded = fcd.find_duplicate_pairs(rows)

    # PROOF/CONFIDENT read as near-certain and are pre-selected in the console's scan modal;
    # ADJUDICATE/HINT need a deliberate tick. Keeps the modal's existing "strong"/"weak" contract
    # so neither the modal nor the sort below had to change for the new engine.
    _STRONG_TIERS = (dc.TIER_PROOF, dc.TIER_CONFIDENT)

    pairs = []
    for v in verdicts:
        a, b = v["rows"]
        keep, flag = _pick_survivor(a, b)
        # v["reasons"] already carries "cos=0.NNN" when a cosine was available (classify_pair
        # inserts it) — do not add it again here.
        pairs.append({
            "keep": {"id": keep.get("id"), "name": keep.get("name"),
                     "url": keep.get("url"), "org": keep.get("org")},
            "flag": {"id": flag.get("id"), "name": flag.get("name"),
                     "url": flag.get("url"), "org": flag.get("org")},
            "reason": f"{v['tier']}: {', '.join(v['reasons'])}",
            "confidence": "strong" if v["tier"] in _STRONG_TIERS else "weak",
            "cut": v["tier"],
        })

    # Strongest first so the near-certain pairs are reviewed at the top.
    order = {"strong": 0, "weak": 1}
    pairs.sort(key=lambda p: order.get(p["confidence"], 9))
    result = {"ok": True, "pairs": pairs[:max(1, limit)], "total": len(pairs),
              "scanned": len(rows)}
    if unembedded:
        # Surfaced, not silently dropped: these rows were never compared by the embedding pass
        # because the index has no vector for them yet — "Refresh Dedupe Index" covers them.
        result["unembedded"] = len(unembedded)
    return result


def flag_suspected_duplicate_pairs(pairs, reviewed_by="admin-console"):
    """Flag the weaker row of each reviewed pair as suspected_duplicate, pointing at the
    survivor and leaving BOTH rows live.

    Reuses moderate_opportunities per pair so every write goes through the same validation
    (the survivor exists, is not itself adjudicated, is not the row being flagged). One extra
    survivor lookup per pair is fine for a hand-reviewed batch.
    """
    pairs = pairs or []
    if not pairs:
        return {"ok": False, "error": "No pairs to flag."}
    flagged, errors, details = 0, 0, []
    for p in pairs:
        fid = str((p or {}).get("id") or "").strip()
        survivor = str((p or {}).get("duplicate_of") or "").strip()
        reason = str((p or {}).get("reason") or "").strip() \
            or f"dedupe sweep: suspected duplicate of {survivor}"
        if not fid or not survivor:
            errors += 1
            if len(details) < 5:
                details.append(f"{fid or '?'}: missing row id or survivor id")
            continue
        r = moderate_opportunities([fid], "suspected_duplicate", reviewed_by=reviewed_by,
                                   duplicate_of=survivor, reason=reason)
        if r.get("ok") and r.get("moderated"):
            flagged += r["moderated"]
        else:
            errors += 1
            if len(details) < 5:
                details.append(f"{fid}: {r.get('error') or 'flag failed'}")
    return {"ok": errors == 0, "flagged": flagged, "errors": errors, "error_details": details}


# The queue marker added by activation_refresh_schema.sql. Set when a row is activated,
# cleared by refresh_opportunities.py once it reads the page. A one-shot queue flag, NOT a
# staleness clock — see that file's header.
ACTIVATION_REFRESH_COLUMN = "activation_refresh_queued_at"
ACTIVATION_REFRESH_SQL = "activation_refresh_schema.sql"


def metadata_refresh_queue(limit=200):
    """Rows ACTIVATED but not yet run through refresh_opportunities.py.

    Read-only. Backs the console's Core Details card. A row is in the queue while its
    `activation_refresh_queued_at` is non-null; the refresh agent nulls it on a successful
    page read. Oldest-queued first, so the top of the list is what has waited longest.

    Degrades if activation_refresh_schema.sql has not been run: the column does not exist,
    the select 400s, and we return queue_ready=False + the file name rather than an error —
    the card then shows the setup line, exactly like the moderation queue does.
    """
    params = {
        "select": f"id,name,org,url,source,{ACTIVATION_REFRESH_COLUMN}",
        ACTIVATION_REFRESH_COLUMN: "not.is.null",
        "is_active": "eq.true",
        "order": f"{ACTIVATION_REFRESH_COLUMN}.asc",
        "limit": str(max(1, min(int(limit or 200), 2000))),
    }
    rows = _supabase_request("opportunities", params=params)
    if rows is None:
        # Could be the missing column (feature off) or a transient read failure. A HEAD
        # count on the bare table tells them apart: if that also fails it is a real outage.
        probe = _supabase_request("opportunities", params={"select": "id", "limit": "1"})
        if probe is not None:
            return {"ok": True, "queue_ready": False, "count": 0, "rows": [],
                    "queue_sql": ACTIVATION_REFRESH_SQL}
        return {"ok": False, "error": "Could not read opportunities from Supabase."}
    return {"ok": True, "queue_ready": True, "count": len(rows), "rows": rows,
            "truncated": len(rows) >= max(1, min(int(limit or 200), 2000)),
            "queue_sql": ACTIVATION_REFRESH_SQL}


# MARQUEE M9: this makes a PAID embedding call (Gemini) per newly-activated row. It keeps the
# content-embedding dedupe index current so the NEXT scrape matches new candidates against rows a
# human just made live. See MARQUEE_DECISIONS.md M9. Toggling/removing the paid call is a marquee
# change.
def _index_activated_rows(ids):
    """Best-effort: embed the just-activated rows into the dedupe index (catalog_embeddings.jsonl)
    so a later scrape's embedding dedupe can see them. Never raises and never blocks activation —
    on any failure (no GEMINI_API_KEY, network, etc.) the rows are simply left for the next
    build_catalog_embeddings.py run to pick up. Active-only, matching the index's design: a row is
    embedded when a human makes it LIVE, not while it sits pending. Reuses the build script's own
    incremental selection + representation so the index and the reader can never drift. Returns the
    count added."""
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not ids:
        return 0
    try:
        import os
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            return 0  # mock / no-key env: skip silently, exactly like the AI proxies do
        import embed_common
        import build_catalog_embeddings as bce
        rows = _supabase_request("opportunities", params={
            "select": "id,name,org,type,summary,eligibility,is_active",
            "id": f"in.({','.join(ids)})"})
        if not rows:
            return 0
        existing = embed_common.load_index()
        todo = bce.select_rows_to_embed(rows, {e.get("id") for e in existing})
        if not todo:
            return 0
        entries = list(existing)
        B = embed_common.DEFAULT_BATCH
        for i in range(0, len(todo), B):
            chunk = todo[i:i + B]
            vectors, _cost = embed_common.embed_batch(
                [bce.row_representation(r) for r in chunk], key)
            for r, v in zip(chunk, vectors):
                if v:
                    entries.append(embed_common.index_entry(r["id"], v, rep="fields",
                                                            source="catalog"))
        embed_common.save_index(entries)
        return len(todo)
    except Exception:
        return 0


def activate_opportunities(ids, active=True):
    """Flip is_active for an explicit list of ids. Never called with anything but an
    operator's explicit selection — there is no "activate all matching" path on purpose."""
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not ids:
        return {"ok": False, "error": "No opportunity ids given."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}
    done, errors, details = 0, 0, []
    activated_ids = []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # Activating IS a human verdict, so stamp it as one — otherwise an activated row keeps
    # a NULL moderation_status and comes back round the queue on the next pass. Dropped
    # from the payload (not the whole write) if the migration has not been run.
    #
    # Enqueue for a metadata refresh: an activated row goes live with whatever metadata it
    # had (often a scraper's thin extraction), so mark it "awaiting refresh_opportunities"
    # here; the agent clears the mark when it reads the page. Deactivating CLEARS the mark
    # (a hidden row is not awaiting anything). See activation_refresh_schema.sql. This is a
    # SEPARATE column from the moderation one, so it degrades on its OWN ladder rung — a
    # missing queue column must never cost the moderation stamp (which would send the row
    # back round the queue). Rungs, most→least complete: full → moderation-only → plain.
    stamped = {"is_active": bool(active), "updated_at": now}
    if active:
        stamped.update({"moderation_status": "approved", "reviewed_by": "admin-console",
                        "reviewed_at": now})
    # full = stamped + the queue marker (now when activating, cleared when deactivating).
    full = dict(stamped, **{ACTIVATION_REFRESH_COLUMN: (now if active else None)})
    plain = {"is_active": bool(active), "updated_at": now}
    # Ladder rungs by index: 0 full, 1 moderation-only (queue column missing), 2 plain
    # (moderation columns also missing). `rung` latches at the first level that works, so
    # the rest of the batch does not re-probe a column we already know is absent.
    ladder = [full, stamped, plain]
    rung = 0
    for opp_id in ids:
        try:
            while True:
                try:
                    _commit_patch(opp_id, ladder[rung])
                    break
                except Exception as e:
                    if not _is_missing_column_error(e) or rung >= len(ladder) - 1:
                        raise
                    rung += 1  # drop to the next rung and retry this same row
            done += 1
            if active:
                activated_ids.append(opp_id)
        except Exception as e:
            errors += 1
            if len(details) < 5:
                details.append(f"{opp_id}: {str(e)[:160]}")
    # The public /api/opportunities response is cached for OPPORTUNITIES_CACHE_TTL seconds;
    # without this the operator activates a row and then cannot see it in the app.
    if done:
        with _opportunities_cache_lock:
            _opportunities_cache["fetched_at"] = 0.0
    # Keep the dedupe index current (MARQUEE M9): embed the rows just made live so the next scrape
    # matches new candidates against them. Best-effort — never affects the activation result above.
    indexed = _index_activated_rows(activated_ids) if active else 0
    return {"ok": errors == 0, "activated": done if active else 0,
            "deactivated": 0 if active else done,
            # How many newly-activated rows were added to the embedding dedupe index (0 in a
            # mock/no-key env or on any embed failure — activation itself still succeeded).
            "indexed": indexed,
            "errors": errors, "error_details": details,
            # False means activation_refresh_schema.sql has not been run: the row was still
            # (de)activated, it just was not enqueued for a refresh.
            "queue_ready": rung == 0}


# ---------------- Deadline cache ----------------
# The console half of clear_deadline_cache.py. Nulling dates_last_checked_at is the ONLY
# way to force a re-check inside the 7-day TTL, and until 2026-08-24 there was no working
# way to do it at all (the script wrote a column name that does not exist, so PostgREST
# rejected every PATCH). That mattered because the TTL is also what hides a bad answer:
# whatever a row holds is served to every student for a week.
DEADLINE_CACHE_COLUMN = "dates_last_checked_at"
DEADLINE_STALE_DAYS = 7            # mirrors app.services.deadlines.DEADLINE_STALE_DAYS
DEADLINE_CHECK_UNIT_COST = 0.07    # measured two-phase cost per verified check


def _deadline_cache_is_fresh(stamp):
    if not stamp:
        return False
    try:
        checked = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    return now - checked < datetime.timedelta(days=DEADLINE_STALE_DAYS)


def clear_deadline_cache(ids=None, want_all=False, dry=False):
    """Make rows due for a fresh deadline check by nulling their cache stamp.

    This agent-adjacent action is unlike the five agent cards in one important way: it
    costs nothing to run and everything later. No API call is made here, but each cleared
    row pays for a two-phase Claude check the next time a student opens it, so the console
    quotes the queued spend rather than a $0.00 that would read as free.

    status/important_dates are deliberately left in place. A stale answer beats a blank
    card while the re-check is pending, and check_deadlines.deadline_write_decision() needs
    the existing dates to know whether a later empty result may overwrite them.
    """
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not ids and not want_all:
        return {"ok": False, "error": "Give at least one opportunity id, or ask for all."}
    if ids and want_all:
        return {"ok": False, "error": "Clearing all takes no id list."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}

    select = f"id,name,{DEADLINE_CACHE_COLUMN}"
    try:
        if want_all:
            # Active rows only — an inactive row is not reachable from the app, so clearing
            # it buys nothing and would make a reviewer's queue pay for a check.
            #
            # PAGINATED past PostgREST's 1000-row max-rows cap. The catalog is ~1240 active
            # rows, so a single request silently returns the first 1000 and the tail is never
            # cleared — and the count shown to the operator would be short too, which is the
            # worse half: "37 rows will be re-checked" reads as a complete answer.
            rows, offset, page_size = [], 0, 1000
            while True:
                page = _supabase_request("opportunities", params={
                    "select": select, "is_active": "eq.true", "order": "id",
                    "limit": str(page_size), "offset": str(offset)}) or []
                rows.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size
        else:
            rows = []
            for i in range(0, len(ids), 50):
                rows.extend(_supabase_request("opportunities", params={
                    "select": select,
                    "id": "in.(%s)" % ",".join(ids[i:i + 50])}) or [])
    except Exception as e:
        return {"ok": False, "error": f"Could not read the catalog: {str(e)[:200]}"}

    found = {r["id"] for r in rows}
    missing = [i for i in ids if i not in found]
    # Only rows that actually carry a stamp have anything to clear.
    targets = [r for r in rows if r.get(DEADLINE_CACHE_COLUMN)]
    fresh = sum(1 for r in targets if _deadline_cache_is_fresh(r.get(DEADLINE_CACHE_COLUMN)))
    summary = {
        "ok": True,
        "matched": len(rows),
        "cached": len(targets),
        "still_fresh": fresh,
        "missing_ids": missing,
        "est_queued_cost_usd": round(len(targets) * DEADLINE_CHECK_UNIT_COST, 2),
        "sample": [{"id": r["id"], "name": r.get("name")} for r in targets[:20]],
    }
    if dry:
        summary["cleared"] = 0
        summary["dry"] = True
        return summary

    cleared, errors, details = 0, 0, []
    for r in targets:
        try:
            _commit_patch(r["id"], {DEADLINE_CACHE_COLUMN: None})
            cleared += 1
        except Exception as e:
            errors += 1
            if len(details) < 5:
                details.append(f"{r['id']}: {str(e)[:160]}")
    summary.update({"ok": errors == 0, "cleared": cleared, "errors": errors,
                    "error_details": details, "dry": False})
    return summary


_SNAPSHOT_COMMIT_RE = re.compile(r"Committed dry-run snapshot (\S+)")


def annotate_committed_snapshots(snapshots):
    """Mark snapshots that have already been committed, from agent_runs' own history.

    `pending` is computed purely from the file on disk and never compared against the
    database, and nothing links a snapshot to the commit that consumed it — so a snapshot
    read identically before and after being applied, and the console offered "Commit…" on
    a file whose writes had already landed. Harmless for the scraper (inserts dedupe on
    normalized URL, so a second commit writes nothing) but not for the three PATCH agents,
    where re-committing re-applies days-old field values over whatever has changed since.

    The filename is recovered from the `notes` string commit_snapshot_file() already
    writes, rather than by adding a column — no migration, and every existing commit in
    the table is recognised retroactively.
    """
    committed = {}
    for r in recent_runs():
        if (r.get("mode") or "") != "snapshot-commit":
            continue
        m = _SNAPSHOT_COMMIT_RE.search(r.get("notes") or "")
        if not m:
            continue
        prev = committed.get(m.group(1))
        at = r.get("finished_at") or r.get("started_at")
        if prev is None or (at or "") > (prev.get("at") or ""):
            committed[m.group(1)] = {
                "at": at,
                "applied": (r.get("items_updated") or 0) + (r.get("items_added") or 0),
            }
    for snap in snapshots:
        hit = committed.get(snap.get("file"))
        snap["committed"] = bool(hit)
        snap["committed_at"] = hit["at"] if hit else None
        snap["committed_applied"] = hit["applied"] if hit else None
    return snapshots


def list_signup_recipes(status="pending_review", limit=200):
    """Recipes for the console's Mailing lists tab, newest discovery first.

    Joined against the catalog in a second request rather than with a PostgREST embed:
    the embed needs a declared foreign-key relationship name, and one wrong guess there
    400s the whole read — the same trap CLAUDE.md records for selecting a column that
    does not exist.
    """
    params = {"select": "*", "order": "discovered_at.desc", "limit": str(limit)}
    if status and status != "all":
        params["status"] = f"eq.{status}"
    try:
        rows = _supabase_request_strict(SIGNUPS_TABLE, params=params)
    except Exception as e:
        if _missing_table_error(e):
            return {"ok": True, "mailing_list_ready": False, "recipes": [], "counts": {},
                    "setup_hint": MAILING_LIST_SETUP_HINT}
        return {"ok": False, "error": f"Could not read {SIGNUPS_TABLE}: {e}"}

    ids = [r["opportunity_id"] for r in rows]
    catalog = {}
    if ids:
        # PostgREST in.() is comma-separated and paren-delimited; ids in this catalog are
        # hex-ish slugs, but strip anything that could be read as syntax regardless.
        safe = ",".join(re.sub(r"[(),*%]", "", str(i)) for i in ids)
        found = _supabase_request("opportunities", params={
            "select": "id,name,org,url,type,is_active", "id": f"in.({safe})"}) or []
        catalog = {row["id"]: row for row in found}

    counts = {}
    for row in rows:
        row["opportunity"] = catalog.get(row["opportunity_id"])
        row["provider_name"] = mailing_list_common.PROVIDER_NAMES.get(row.get("method"))
        counts[row.get("status")] = counts.get(row.get("status"), 0) + 1
    return {"ok": True, "mailing_list_ready": True, "recipes": rows, "counts": counts}


def moderate_signup_recipes(ids, status, reviewed_by="admin-console"):
    """Record a human verdict on an explicit list of recipes.

    Promoting to 'verified' is the ONLY way a recipe becomes executable, and it is the
    whole accuracy control for this feature: discovery proposes, a person decides. There
    is deliberately no "verify everything above confidence X" path — the confidence score
    is the model's opinion of its own work, which is exactly what review exists to check.
    """
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if status not in SIGNUP_REVIEW_STATUSES:
        return {"ok": False, "error": f"Unknown recipe status: {status!r}. "
                                      f"Expected one of {', '.join(SIGNUP_REVIEW_STATUSES)}."}
    if not ids:
        return {"ok": False, "error": "No recipe ids given."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    updates = {"status": status, "reviewed_by": reviewed_by, "reviewed_at": now,
               "updated_at": now}
    done, errors, details = 0, 0, []
    for opp_id in ids:
        try:
            _supabase_request_strict(SIGNUPS_TABLE, method="PATCH",
                                     params={"opportunity_id": f"eq.{opp_id}"},
                                     data=updates)
            done += 1
        except Exception as e:
            # A table created from an older version of the schema fails here on every
            # row with an opaque error. Name the file instead: this is a setup step, not
            # a bug, and "Supabase PATCH failed" sends the reader looking in the wrong
            # place. Strict (raising) so the cause is actually visible — the swallowing
            # helper collapses every failure into an indistinguishable None.
            if _missing_table_error(e):
                return {"ok": False, "mailing_list_ready": False,
                        "error": MAILING_LIST_SETUP_HINT}
            errors += 1
            if len(details) < 5:
                details.append(f"{opp_id}: {str(e)[:160]}")
    return {"ok": errors == 0, "moderated": done, "status": status,
            "errors": errors, "error_details": details}


# ---------- Sources: the trusted-domain allowlist (P5) ----------
# The console half of trusted_aggregators. The READ side both features consume lives in
# aggregators_common; this is the operator's CRUD over it. Writes invalidate that module's
# cache so an approval is live on the deadline path at once, not up to a TTL later — the
# console and the deadline loop run in the SAME process (server.py mounts ops), so the
# in-process cache is shared.
AGGREGATORS_TABLE = "trusted_aggregators"
AGGREGATOR_STATUSES = ("trusted", "blocked")
AGGREGATORS_SETUP_HINT = (
    "The trusted_aggregators table is missing. Run trusted_aggregators_schema.sql in the "
    "Supabase SQL editor (it is idempotent), then restart with restart_server.ps1. Until "
    "then no off-domain source is trusted anywhere: the deadline loop keeps nothing off the "
    "program's own site, and aggregator tasks (P6) all stay parked.")


def list_trusted_aggregators():
    """The allowlist for the console Sources tab, plus counts. Degrades to a setup notice
    (aggregators_ready=False) when the table is absent, exactly like the mailing-list tab."""
    try:
        rows = _supabase_request_strict(
            AGGREGATORS_TABLE,
            params={"select": "domain,status,label,notes,added_by,created_at,updated_at",
                    "order": "updated_at.desc"})
    except Exception as e:
        if _missing_table_error(e):
            return {"ok": True, "aggregators_ready": False, "aggregators": [], "counts": {},
                    "setup_hint": AGGREGATORS_SETUP_HINT}
        return {"ok": False, "error": f"Could not read {AGGREGATORS_TABLE}: {e}"}
    counts = {}
    for row in rows or []:
        counts[row.get("status")] = counts.get(row.get("status"), 0) + 1
    return {"ok": True, "aggregators_ready": True, "aggregators": rows or [], "counts": counts}


def upsert_trusted_aggregator(domain, status, label=None, notes=None, added_by="admin-console"):
    """Add or update one domain. Domain is normalized (aggregators_common) so what the
    deadline filter compares against is what the operator sees, and a paste of a full URL
    ("https://www.lumiere-education.com/guides") stores as the bare domain. Upsert on the
    domain primary key so re-approving an existing row edits it rather than 400ing."""
    if status not in AGGREGATOR_STATUSES:
        return {"ok": False, "error": f"Unknown status {status!r}. "
                                      f"Expected one of {', '.join(AGGREGATOR_STATUSES)}."}
    norm = aggregators_common.normalize_domain(domain)
    if not norm:
        return {"ok": False, "error": f"Could not read a domain from {domain!r}."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    row = {"domain": norm, "status": status, "added_by": added_by, "updated_at": now}
    # Only overwrite label/notes when the operator supplied them, so editing a domain's
    # status from the row action doesn't blank a note typed earlier.
    if label is not None:
        row["label"] = label.strip() or None
    if notes is not None:
        row["notes"] = notes.strip() or None
    try:
        _supabase_request_strict(
            AGGREGATORS_TABLE, method="POST",
            params={"on_conflict": "domain"}, data=[row],
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    except Exception as e:
        if _missing_table_error(e):
            return {"ok": False, "aggregators_ready": False, "error": AGGREGATORS_SETUP_HINT}
        return {"ok": False, "error": f"Could not write {AGGREGATORS_TABLE}: {e}"}
    aggregators_common.invalidate_policy_cache()
    return {"ok": True, "domain": norm, "status": status}


def delete_trusted_aggregator(domain):
    """Remove a domain from the allowlist. Removing (as opposed to blocking) returns the
    domain to PENDING — the safe default — so this is how you undo a mistaken approval
    without asserting the domain is bad."""
    norm = aggregators_common.normalize_domain(domain)
    if not norm:
        return {"ok": False, "error": f"Could not read a domain from {domain!r}."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}
    try:
        _supabase_request_strict(AGGREGATORS_TABLE, method="DELETE",
                                 params={"domain": f"eq.{norm}"})
    except Exception as e:
        if _missing_table_error(e):
            return {"ok": False, "aggregators_ready": False, "error": AGGREGATORS_SETUP_HINT}
        return {"ok": False, "error": f"Could not delete from {AGGREGATORS_TABLE}: {e}"}
    aggregators_common.invalidate_policy_cache()
    return {"ok": True, "domain": norm}


def get_agents_summary(days=30):
    """Aggregates for the dashboard KPI strip and charts.

    Totals are split per agent AND kept unit-aware: the scraper's items_processed counts
    SEEDS, not opportunity rows, so summing it into a catalog-wide "rows touched" figure
    would silently inflate that number. rows_updated/rows_added therefore only accumulate
    from agents whose unit is 'rows' plus the scraper's genuine items_added.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    prev_cutoff = now - datetime.timedelta(days=days * 2)

    rows = recent_runs()
    current, previous = [], []
    for r in rows:
        started = _parse_iso(r.get("started_at"))
        if not started:
            continue
        if started >= cutoff:
            current.append(r)
        elif started >= prev_cutoff:
            previous.append(r)

    def totals(bucket, deadline_cost=0.0):
        out = {"runs": 0, "cost_usd": 0.0, "agent_cost_usd": 0.0, "app_cost_usd": 0.0,
               "rows_updated": 0, "rows_added": 0, "errors": 0, "failed_runs": 0,
               "dry_runs": 0, "unknown_cost_runs": 0}
        for r in bucket:
            key = _agent_key_for(r.get("agent"))
            cfg = AGENT_CONFIGS_SCHEMA.get(key, {})
            cost = float(r.get("cost_usd") or 0)
            interactive = r.get("agent") in INTERACTIVE_AGENTS
            out["cost_usd"] += cost
            out["app_cost_usd" if interactive else "agent_cost_usd"] += cost

            if interactive:
                continue  # rollup rows aren't "runs" and have no rows-changed semantics

            out["runs"] += 1
            out["errors"] += r.get("errors") or 0
            if r.get("errors"):
                out["failed_runs"] += 1
            # A run that ended without patching its totals spent money we can't see.
            # Counting it silently as $0 is what made the old figure look complete.
            if r.get("cost_usd") is None and not r.get("finished_at"):
                out["unknown_cost_runs"] += 1
            # Dry runs cost real money but change nothing, so their cost counts while
            # their would-have-been row counts must not.
            if str(r.get("mode") or "").endswith("-dryrun"):
                out["dry_runs"] += 1
                continue
            out["rows_added"] += r.get("items_added") or 0
            if cfg.get("unit", "rows") == "rows":
                out["rows_updated"] += r.get("items_updated") or 0

        # On-demand deadline checks live in their own table, not agent_runs.
        out["deadline_cost_usd"] = round(deadline_cost, 4)
        out["app_cost_usd"] = round(out["app_cost_usd"] + deadline_cost, 4)
        out["cost_usd"] = round(out["cost_usd"] + deadline_cost, 4)
        out["agent_cost_usd"] = round(out["agent_cost_usd"], 4)
        out["error_rate"] = round(out["failed_runs"] / out["runs"], 3) if out["runs"] else 0
        return out

    # Per-day cost series, per band — the stacked chart's input. Days with no runs are
    # emitted as zeros so the x-axis stays evenly spaced instead of collapsing gaps.
    #
    # Bands, not raw agent literals. The five console agents each keep their own band
    # because each is a thing an operator deliberately runs; everything else collapses
    # into two, because splitting them was answering a question nobody asked of this
    # chart. `parts` keeps the components for the tooltip, so collapsing a band hides
    # nothing — it just stops the legend reading like an implementation detail.
    per_day = {}

    def _add(day, key, cost, part_label=None, runs=0, rows=0):
        bucket = per_day.setdefault(day, {})
        entry = bucket.setdefault(key, {"cost_usd": 0.0, "runs": 0, "rows": 0, "parts": {}})
        entry["cost_usd"] = round(entry["cost_usd"] + cost, 4)
        entry["runs"] += runs
        entry["rows"] += rows
        if part_label:
            entry["parts"][part_label] = round(entry["parts"].get(part_label, 0.0) + cost, 4)

    for r in current:
        started = _parse_iso(r.get("started_at"))
        day = started.date().isoformat()
        key = _agent_key_for(r.get("agent"))
        cost = float(r.get("cost_usd") or 0)
        rows = (r.get("items_updated") or 0) + (r.get("items_added") or 0)
        if key in AGENT_CONFIGS_SCHEMA:
            _add(day, key, cost, runs=1, rows=rows)
        elif key in INTERACTIVE_AGENTS:
            _add(day, "end_user", cost, INTERACTIVE_AGENTS[key], runs=1, rows=rows)
        else:
            # A run from a script with no console card — backfills and one-off passes.
            _add(day, "other", cost, key.replace("_", " "), runs=1, rows=rows)

    # On-demand deadline checks, folded in under their own key. Without this the chart
    # could not sum to the KPI no matter what the console drew, because this spend is not
    # in agent_runs at all.
    for day, cost in fetch_deadline_check_cost_by_day(cutoff, now).items():
        _add(day, "end_user", cost, "App — deadline checks")

    # Anchored on today and inclusive of it — see the same loop in get_user_costs(). A
    # forward count from the cutoff ends at yesterday, so today's runs never got a bar.
    series = []
    today = now.date()
    for i in range(days, -1, -1):
        day = (today - datetime.timedelta(days=i)).isoformat()
        series.append({"date": day, "agents": per_day.get(day, {})})

    # Every key the series actually contains, labelled, so the chart can draw all of them
    # instead of only the five with console cards. It used to iterate the card list, which
    # silently dropped the interactive rollups, on-demand deadline checks, and any agent
    # run from a script with no card (subject_tags_backfill) — $4.84 of $14.24, invisible
    # directly under a KPI that included it.
    series_keys = []
    for key in sorted({k for bucket in per_day.values() for k in bucket}):
        if key in AGENT_CONFIGS_SCHEMA:
            series_keys.append({"key": key, "label": AGENT_CONFIGS_SCHEMA[key]["name"],
                                "group": "agent"})
        elif key == "end_user":
            series_keys.append({
                "key": key, "label": "End User Initiated", "group": "app",
                "note": "Spend your users caused, not runs anyone started here: every AI "
                        "feature in the app plus on-demand deadline checks. Broken out by "
                        "who triggered it on the Cost per user tab — the same money, never "
                        "additional spend."})
        else:
            series_keys.append({
                "key": key, "label": "Other", "group": "other",
                "note": "Standalone scripts with no card here — one-off backfills and "
                        "full-catalog passes run from the command line. Their spend counts "
                        "toward the totals; they are not scheduled and cannot be started "
                        "from this console."})

    by_agent = {}
    for key in AGENT_CONFIGS_SCHEMA:
        db_agent = AGENT_CONFIGS_SCHEMA[key]["db_agent"]
        by_agent[key] = totals([r for r in current if r.get("agent") == db_agent])

    dl_now = fetch_deadline_check_cost(cutoff, now)
    dl_prev = fetch_deadline_check_cost(prev_cutoff, cutoff)

    return {
        "days": days,
        "current": totals(current, dl_now["cost_usd"]),
        "previous": totals(previous, dl_prev["cost_usd"]),
        "by_agent": by_agent,
        "interactive": {
            key: {
                "name": label,
                "calls": sum(r.get("items_processed") or 0
                             for r in current if r.get("agent") == key),
                "cost_usd": round(sum(float(r.get("cost_usd") or 0)
                                      for r in current if r.get("agent") == key), 4),
            } for key, label in INTERACTIVE_AGENTS.items()
        },
        "deadline_checks": dl_now,
        "series": series,
        "series_keys": series_keys,
        # What the totals above still cannot see, stated rather than implied.
        "caveats": [
            "Costs are estimated locally from token counts, not read from provider billing.",
            "A client-side timeout still bills server-side; that spend is never captured.",
            "Runs that ended without reporting totals contribute $0 to these figures.",
        ],
    }


def fetch_deadline_check_cost_by_day(start, end):
    """On-demand deadline spend per UTC day, for the spend chart.

    These checks are the single largest thing the chart used to omit: they live in
    deadline_check_log, never in agent_runs, so they were absent from the series entirely
    while still counting toward the KPI directly above it.
    """
    rows = _supabase_request("deadline_check_log", params={
        "select": "cost_usd,checked_at",
        "checked_at": f"gte.{start.isoformat()}",
        "and": f"(checked_at.lt.{end.isoformat()})",
        "limit": "10000",
    }) or []
    per_day = {}
    for r in rows:
        cost = r.get("cost_usd")
        day = (r.get("checked_at") or "")[:10]
        if cost is None or not day:
            continue
        per_day[day] = round(per_day.get(day, 0.0) + float(cost), 6)
    return per_day


def fetch_deadline_check_cost(start, end):
    """Cost of on-demand deadline checks in a window, from deadline_check_log.

    These are user-triggered checks from the app, logged to their own table rather than
    agent_runs — so they were absent from every figure the console showed. Cached rows
    have a null cost because they made no API call; only fresh checks are billed.
    """
    # Both bounds. `end` used to be accepted and ignored, which made the summary's
    # "previous period" deadline cost include the current period as well — the two figures
    # the KPI deltas are computed from overlapped, so every delta involving deadline spend
    # was wrong.
    rows = _supabase_request("deadline_check_log", params={
        "select": "cost_usd,source,web_searches",
        "checked_at": f"gte.{start.isoformat()}",
        "and": f"(checked_at.lt.{end.isoformat()})",
        "limit": "10000",
    }) or []
    rows = [r for r in rows if (r.get("cost_usd") is not None)]
    return {
        "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 4),
        "billed_checks": len(rows),
    }


# ---------- Billed spend, pulled from the providers ----------
# Everything else in this file is an ESTIMATE computed locally from token counts. This is
# the only place that reports what a provider actually billed, so the console can show the
# drift between the two.
#
# Anthropic: GET /v1/organizations/cost_report returns real billed cost per day.
#   Requires an ADMIN key (sk-ant-admin..., x-api-key) or an org:admin OAuth token — a
#   regular sk-ant-api key CANNOT read it, and the Admin API is unavailable to individual
#   accounts entirely (an organization must be set up in the Console first).
#   Amounts come back in the currency's lowest unit as a decimal string, i.e. CENTS.
#
# Google/Gemini: there is NO equivalent. The Gemini API exposes no billing endpoint at all
#   — spend is visible only in the AI Studio dashboard or the Cloud Billing console, and
#   programmatic access would need the separate Google Cloud Billing API with a GCP service
#   account (an AI Studio API key cannot authenticate to it). So Gemini gets a
#   reconciliation link rather than a live figure.
ANTHROPIC_COST_URL = "https://api.anthropic.com/v1/organizations/cost_report"
ANTHROPIC_ADMIN_KEY = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
GEMINI_BILLING_URL = "https://aistudio.google.com/spend"
ANTHROPIC_BILLING_URL = "https://platform.claude.com/cost"


def fetch_anthropic_billed_cost(days=30):
    """Real billed Anthropic spend for the last `days`, or a reason it's unavailable."""
    if not ANTHROPIC_ADMIN_KEY:
        return {
            "available": False,
            "reason": "No ANTHROPIC_ADMIN_KEY in .env. The cost API needs an Admin key "
                      "(sk-ant-admin…), which a regular API key cannot substitute for, and "
                      "which requires an organization account.",
            "dashboard_url": ANTHROPIC_BILLING_URL,
        }
    start = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)).replace(
                 hour=0, minute=0, second=0, microsecond=0)
    params = urllib.parse.urlencode({
        "starting_at": start.isoformat().replace("+00:00", "Z"),
        "bucket_width": "1d",
        "limit": min(days, 31),
    })
    req = urllib.request.Request(
        f"{ANTHROPIC_COST_URL}?{params}",
        headers={"x-api-key": ANTHROPIC_ADMIN_KEY, "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        hint = ("The key was rejected. The cost report needs an Admin key (sk-ant-admin…) "
                "from an organization account — a regular sk-ant-api key returns 401."
                if e.code in (401, 403) else detail)
        return {"available": False, "reason": f"HTTP {e.code}: {hint}",
                "dashboard_url": ANTHROPIC_BILLING_URL}
    except Exception as e:
        return {"available": False, "reason": str(e), "dashboard_url": ANTHROPIC_BILLING_URL}

    total_cents = 0.0
    series = []
    for bucket in data.get("data", []):
        # `amount` is a decimal string in the lowest currency unit (cents), not dollars.
        day_cents = sum(float(item.get("amount") or 0) for item in bucket.get("results", []))
        total_cents += day_cents
        series.append({"date": (bucket.get("starting_at") or "")[:10],
                       "cost_usd": round(day_cents / 100, 4)})
    return {
        "available": True,
        "cost_usd": round(total_cents / 100, 4),
        "currency": "USD",
        "days": days,
        "series": series,
        "dashboard_url": ANTHROPIC_BILLING_URL,
    }


def get_billed_costs(days=30):
    """Provider-billed spend beside our own estimate, with the drift between them."""
    anthropic = fetch_anthropic_billed_cost(days)
    summary = get_agents_summary(days=days)
    estimated_total = summary["current"]["cost_usd"]

    drift = None
    if anthropic.get("available") and estimated_total:
        # Anthropic-billed vs OUR TOTAL estimate is apples-to-oranges (our estimate spans
        # both providers), so this is only indicative — say so rather than implying it's
        # a clean comparison.
        drift = round(anthropic["cost_usd"] - estimated_total, 4)

    return {
        "days": days,
        "estimated_total_usd": estimated_total,
        "anthropic": anthropic,
        "gemini": {
            "available": False,
            "reason": "The Gemini API exposes no billing endpoint. Spend is only visible "
                      "in the AI Studio dashboard, or via the separate Google Cloud "
                      "Billing API, which an AI Studio key cannot authenticate to.",
            "dashboard_url": GEMINI_BILLING_URL,
        },
        "drift_usd": drift,
    }


def get_admin_console_html():
    """Returns the admin console HTML, read fresh from disk on each request so edits
    show up on reload without restarting the server."""
    admin_path = os.path.join(os.path.dirname(__file__), "admin_console.html")
    if os.path.exists(admin_path):
        with open(admin_path, "r", encoding="utf-8") as f:
            return f.read()
    return """<!DOCTYPE html><html><head><title>Admin Console</title></head>
    <body><h1>Admin Console</h1><p>admin_console.html not found. Serve from project root.</p></body></html>"""


def mark_agent_running(agent_name):
    with _agent_runs_lock:
        _agent_runs[agent_name] = {
            "status": "running",
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


def mark_agent_idle(agent_name):
    with _agent_runs_lock:
        _agent_runs.pop(agent_name, None)


def is_agent_running(agent_name):
    with _agent_runs_lock:
        return _agent_runs.get(agent_name, {}).get("status") == "running"


def running_gemini_search_agent(exclude=None):
    """Name of a currently-running agent that holds the shared Gemini web-search lock.

    check_reviews and scrape_opportunities both acquire gemini_common's
    .gemini_web_search.lock, so only one can run at a time — the second fails fast. Better
    to refuse the launch with a clear reason than to let it start and die a minute later.
    """
    with _agent_runs_lock:
        running = [k for k, v in _agent_runs.items() if v.get("status") == "running"]
    for key in running:
        if key != exclude and AGENT_CONFIGS_SCHEMA.get(key, {}).get("uses_gemini_search"):
            return key
    return None


def get_agent_status(agent_name, rows=None):
    """Status for one agent card.

    'running' is process-local state (this server's own subprocess tracking) because the
    database has no way to know it. Everything else — last run, items, cost — comes from
    the newest real agent_runs row, so it stays accurate across server restarts and for
    runs triggered outside this console. The exception is a subprocess that crashed before
    writing any row at all (a bad flag, a missing key): that error exists only in local
    memory, so it takes priority over whatever the last DB row says.
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name, {})
    with _agent_runs_lock:
        local = dict(_agent_runs.get(agent_name, {}))

    result = {
        "name": cfg.get("name", agent_name),
        "description": cfg.get("description"),
        "unit": cfg.get("unit", "items"),
        "writes": cfg.get("writes"),
        "api": cfg.get("api"),
        "uses_gemini_search": cfg.get("uses_gemini_search", False),
        "defaults": agent_defaults(agent_name),
        "status": "idle",
        "last_run": None,
        "last_mode": None,
        "items_processed": 0,
        "items_updated": 0,
        "items_added": 0,
        "cost_usd": None,
        "silent_search_count": None,
        "error": None,
    }

    if local.get("status") == "running":
        result["status"] = "running"
        result["started_at"] = local.get("started_at")
        return result

    if rows is None:
        rows = recent_runs()
    db_agent = cfg.get("db_agent")
    latest = next((r for r in rows if r.get("agent") == db_agent), None)

    if latest:
        shaped = _shape_run(latest)
        result.update({
            "last_run": shaped["finished_at"] or shaped["started_at"],
            "last_mode": shaped["mode"],
            "items_processed": shaped["items_processed"],
            "items_updated": shaped["items_updated"],
            "items_added": shaped["items_added"],
            "cost_usd": shaped["cost_usd"],
            "silent_search_count": shaped["silent_search_count"],
        })
        if shaped["status"] == "failed":
            result["status"] = "error"
            result["error"] = shaped["notes"] or f"{shaped['errors']} error(s) in last run"
        elif shaped["status"] == "interrupted":
            result["status"] = "interrupted"
            result["error"] = "Previous run ended without reporting totals"

    if local.get("status") == "error":
        result["status"] = "error"
        result["error"] = local.get("error")

    return result


def get_all_agents_status():
    """Status of all four agents from ONE cached Supabase read."""
    rows = recent_runs()
    return {name: get_agent_status(name, rows=rows) for name in AGENT_CONFIGS_SCHEMA}


# ---------- Building agent argv ----------

AGENT_RUN_TIMEOUT_SECS = 3600
LOG_BUFFER_LINES = 500
AGENT_LOG_DIR = os.path.join(REPO_ROOT, "agent_logs")

# Live output per agent: {agent: {"lines": [...], "file": path, "started_at": iso}}
_agent_logs = {}
_agent_logs_lock = threading.Lock()


def _int_or_none(value):
    """Coerce a config value to int. The console posts every field as a string (DOM input
    values), and empty strings mean 'not set' rather than zero."""
    if value in (None, "", False):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_agent_args(agent_name, config, preview=False):
    """Translate a console config dict into real argv for the agent's script.

    Every flag here was verified against the script's own argparse — inventing
    plausible-looking flags previously made every run fail with an argparse error, so do
    not add one without checking `python <script>.py --help` first.
    """
    cfg = AGENT_CONFIGS_SCHEMA[agent_name]
    args = [sys.executable, "-u", cfg["script"]]  # -u: unbuffered, so live output streams
    config = config or {}
    defaults = agent_defaults(agent_name)  # built-ins with saved overrides applied

    if agent_name == "metadata":
        # refresh_opportunities.py: [--sample N | --all | --awaiting-refresh] [--dry-run]
        # [--exclude-source S]
        scope = config.get("scope")
        if scope == "sample":
            args += ["--sample", str(_int_or_none(config.get("sampleSize")) or 50)]
        elif scope == "awaiting":
            # Drain the Awaiting-refresh queue: only active rows carrying the activation
            # marker. --exclude-source does not apply here (the agent ignores it in this
            # mode), so it is not forwarded.
            args.append("--awaiting-refresh")
        else:
            args.append("--all")
        if scope != "awaiting" and config.get("excludeSource"):
            args += ["--exclude-source", str(config["excludeSource"])]

    elif agent_name == "reviews":
        # check_reviews.py: [--sample N | --all] [--force] [--dry-run]
        scope = config.get("scope")
        if scope == "sample":
            args += ["--sample", str(_int_or_none(config.get("sampleSize")) or 50)]
        elif scope == "all":
            args.append("--all")
        # else: no flag = default (stale/unchecked rows only)
        if config.get("force"):
            args.append("--force")

    elif agent_name == "scraper":
        # scrape_opportunities.py: --mode {national,seattle} (required) [--dry-run]
        # [--seed-ids S] [--seed-indices S] [--max-searches N] [--gate-observe]
        args += ["--mode", config.get("mode") or "national"]
        if config.get("seedIds"):
            args += ["--seed-ids", str(config["seedIds"])]
        max_searches = _int_or_none(config.get("maxSearches"))
        if max_searches:
            args += ["--max-searches", str(max_searches)]
        # Discovery-gate mode. The console defaults to OBSERVE (every candidate lands in the
        # review queue labelled, nothing dropped or diverted) because a console operator is
        # reviewing, not running headless — the CLI default is the opposite (act). Pass
        # gateMode:"act" to let the gate drop stale rows and route hubs to mining on its own.
        if (config.get("gateMode") or "observe") != "act":
            args.append("--gate-observe")

    elif agent_name == "deadline":
        # check_deadlines.py: [--sample N | --all] [--dry-run]
        scope = config.get("scope")
        if scope == "sample":
            args += ["--sample", str(_int_or_none(config.get("sampleSize")) or 50)]
        else:
            args.append("--all")

    elif agent_name == "mailinglist":
        # find_mailing_lists.py: [--all | --ids A,B] [--limit N] [--force] [--dry-run]
        if config.get("ids"):
            args += ["--ids", str(config["ids"])]
        else:
            args.append("--all")
        limit = _int_or_none(config.get("limit"))
        if limit:
            args += ["--limit", str(limit)]
        if config.get("force"):
            args.append("--force")

    elif agent_name == "links":
        # check_links.py: [--all | --sample N | --ids A,B | --repair-flagged] [--force]
        # [--flag-only] [--no-repair] [--dry-run] [--workers N]
        scope = config.get("scope")
        if config.get("ids"):
            args += ["--ids", str(config["ids"])]
        elif scope == "flagged":
            # Its own scope, not a filter on top of one: --repair-flagged reads INACTIVE
            # rows, so it cannot be combined with --all/--sample, and --force (a staleness
            # escape hatch) means nothing against a set selected by flag rather than age.
            args.append("--repair-flagged")
        elif scope == "sample":
            args += ["--sample", str(_int_or_none(config.get("sampleSize")) or 100)]
        else:
            args.append("--all")
        if config.get("force") and scope != "flagged":
            args.append("--force")
        if config.get("noRepair"):
            args.append("--no-repair")
        # Off by default. Deactivating is this agent's whole point, and a flag that must be
        # remembered to make it act is a flag that gets forgotten — the operator opts OUT
        # of acting, having read the preview, rather than opting in.
        if config.get("flagOnly"):
            args.append("--flag-only")
        workers = _int_or_none(config.get("workers"))
        if workers:
            args += ["--workers", str(workers)]

    # Timing applies to every agent identically.
    min_delay = config.get("minDelay")
    if min_delay not in (None, ""):
        args += ["--min-delay", str(min_delay)]
    elif defaults.get("min_delay") is not None:
        args += ["--min-delay", str(defaults["min_delay"])]

    timeout = _int_or_none(config.get("timeout")) or defaults.get("timeout")
    if timeout:
        args += ["--timeout", str(timeout)]

    if preview:
        args.append("--preview")
    elif config.get("dryRun"):
        args.append("--dry-run")

    return args


def preview_agent(agent_name, config):
    """Run an agent with --preview: resolves scope, makes NO API calls, writes nothing.

    Pairs the script's own row count with a per-item cost derived from that agent's real
    agent_runs history, so the estimate reflects what this agent actually costs rather
    than a hardcoded guess. Row selection stays in the script (it cannot drift from what a
    real run would do); cost math stays here (this is where the history already lives).
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name)
    if not cfg:
        return {"ok": False, "error": f"Unknown agent: {agent_name}"}
    script = os.path.join(REPO_ROOT, cfg["script"])
    if not os.path.exists(script):
        return {"ok": False, "error": f"Script not found: {cfg['script']}"}

    args = build_agent_args(agent_name, config, preview=True)
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=120,
            cwd=REPO_ROOT,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Preview timed out after 120s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    payload = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith(AGENT_PREVIEW_PREFIX):
            try:
                payload = json.loads(line[len(AGENT_PREVIEW_PREFIX):].strip())
            except Exception:
                payload = None
    if payload is None:
        err = (proc.stderr or proc.stdout or "").strip()[-500:]
        return {"ok": False, "error": err or "Preview produced no PREVIEW_JSON line"}

    count = payload.get("count") or 0
    est = estimate_agent_cost(agent_name, count, min_delay=(config or {}).get("minDelay"))
    est.update({
        "ok": True,
        "agent": agent_name,
        "count": count,
        "unit": payload.get("unit") or cfg.get("unit", "items"),
        "sample": payload.get("sample") or [],
        "mode": payload.get("mode"),
        "seed_ids": payload.get("seed_ids"),
        "argv": args[1:],  # minus the interpreter path; shown in the UI for transparency
    })
    return est


def estimate_agent_cost(agent_name, count, min_delay=None):
    """Estimate cost and wall time for `count` items, from this agent's own history.

    Uses completed runs only (a run that died mid-pass has a cost that doesn't match its
    item count). Returns based_on_runs=0 when there's no history to learn from, which the
    UI surfaces rather than passing off a fabricated number as an estimate.

    `min_delay` is the delay the caller is ABOUT to run with, which may differ from the
    default the historical runs used. Wall time is max(measured per-item time, delay):
    raising the delay above measured latency slows the run proportionally, while lowering
    it below cannot speed the run past how long the API itself takes to answer.
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name, {})

    # A free agent's $0.00 is a property of its design, not an absence of history, and the
    # two must not render the same way. Without this the link checker would show "no
    # history to estimate from" on its first run and then — once it HAS history, all of it
    # at cost_usd = 0 — a $0.00 estimate carrying `provisional: true`, which reads as "we
    # are not sure it is free". It is free: it makes no API calls at all.
    if cfg.get("free"):
        secs_each = 0.06  # measured: ~1374 rows in ~60s at the default worker count
        return {
            "est_cost_usd": 0.0, "est_cost_per_item": 0.0,
            "est_cost_low_usd": 0.0, "est_cost_high_usd": 0.0,
            "est_seconds": round(secs_each * count) or None,
            "based_on_runs": 0, "provisional": False, "free": True,
        }

    db_agent = cfg.get("db_agent")
    rows = [r for r in recent_runs()
            if r.get("agent") == db_agent and r.get("finished_at")
            and (r.get("items_processed") or 0) > 0 and r.get("cost_usd") is not None
            # Successful runs only. A FAILED run still counted every row it touched but
            # errored out of most of them before paying for them, so it lands here as an
            # implausibly cheap per-item rate and drags the mean down — for review_checker,
            # 6 of the 10 most recent qualifying runs were failures and the estimate came
            # out at roughly half the rate the clean runs measure.
            and _run_status(r) == "success"
            # snapshot-commit rows carry real item counts against cost_usd = 0 BY
            # CONSTRUCTION (the dry run that produced the file already paid), so averaging
            # one in is averaging in a free run that never existed.
            and (r.get("mode") or "") != "snapshot-commit"]

    per_item = None
    per_item_secs = None
    per_item_low = None
    per_item_high = None
    if rows:
        sample = rows[:10]
        rates = [float(r["cost_usd"]) / r["items_processed"] for r in sample]
        per_item = sum(rates) / len(rates)
        # The spread, not just the mean. This agent's own history ranges over three orders
        # of magnitude per item depending on how much web search a run triggered; a lone
        # average hides that completely and reads as a precision the number does not have.
        per_item_low, per_item_high = min(rates), max(rates)
        durations = []
        for r in sample:
            t0, t1 = _parse_iso(r.get("started_at")), _parse_iso(r.get("finished_at"))
            if t0 and t1 and r["items_processed"]:
                durations.append((t1 - t0).total_seconds() / r["items_processed"])
        if durations:
            per_item_secs = sum(durations) / len(durations)

    # Wall time is usually dominated by the inter-call delay rather than the API itself.
    try:
        delay = float(min_delay) if min_delay not in (None, "") else None
    except (TypeError, ValueError):
        delay = None
    if delay is None:
        delay = float(agent_defaults(agent_name).get("min_delay") or 0)
    secs_each = max(per_item_secs or 0, delay)

    n = len(rows[:10])
    return {
        "est_cost_usd": round(per_item * count, 4) if per_item is not None else None,
        "est_cost_per_item": round(per_item, 6) if per_item is not None else None,
        "est_cost_low_usd": round(per_item_low * count, 4) if per_item_low is not None else None,
        "est_cost_high_usd": round(per_item_high * count, 4) if per_item_high is not None else None,
        "est_seconds": round(secs_each * count) if secs_each else None,
        "based_on_runs": n,
        # Fewer than three clean runs is not an estimate, it is one anecdote multiplied by
        # count. Say so rather than printing it at the same weight as a settled figure.
        "provisional": n < 3,
    }


# ---------- Running an agent ----------

def _append_log(agent_name, line):
    with _agent_logs_lock:
        entry = _agent_logs.get(agent_name)
        if entry is None:
            return
        entry["lines"].append(line)
        # Ring buffer: the on-disk file keeps everything, memory keeps the tail.
        if len(entry["lines"]) > LOG_BUFFER_LINES:
            entry["dropped"] += len(entry["lines"]) - LOG_BUFFER_LINES
            del entry["lines"][:-LOG_BUFFER_LINES]
        handle = entry.get("handle")
        if handle:
            try:
                handle.write(line + "\n")
                handle.flush()
            except Exception:
                pass


def start_agent_log(agent_name):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(AGENT_LOG_DIR, exist_ok=True)
    path = os.path.join(AGENT_LOG_DIR, f"{agent_name}_{stamp}.log")
    try:
        handle = open(path, "w", encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Could not open agent log {path}: {e}")
        handle, path = None, None
    with _agent_logs_lock:
        _agent_logs[agent_name] = {
            "lines": [], "dropped": 0, "handle": handle, "path": path,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    return path


def close_agent_log(agent_name):
    with _agent_logs_lock:
        entry = _agent_logs.get(agent_name) or {}
        handle = entry.pop("handle", None)
    if handle:
        try:
            handle.close()
        except Exception:
            pass


def get_agent_log(agent_name, since=0):
    """Lines after `since` for the live console, plus the running total.

    `dropped` tells the client the ring buffer discarded older lines, so it can say so
    instead of silently showing a gap.
    """
    with _agent_logs_lock:
        entry = _agent_logs.get(agent_name)
        if not entry:
            return {"lines": [], "next": 0, "dropped": 0, "running": is_agent_running(agent_name)}
        dropped = entry["dropped"]
        total = dropped + len(entry["lines"])
        start = max(0, int(since) - dropped)
        return {
            "lines": entry["lines"][start:],
            "next": total,
            "dropped": dropped,
            "path": entry.get("path"),
            "started_at": entry.get("started_at"),
            "running": is_agent_running(agent_name),
        }


def run_agent_subprocess(agent_name, config):
    """Run an agent script as a subprocess, streaming its output. Returns (ok, message).

    Output is read line by line on a reader thread rather than via communicate(), which
    blocks until the process exits — a full pass runs for over an hour, so the previous
    implementation meant the console showed nothing at all until the very end and then
    discarded the output entirely.
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name, {})
    script = cfg.get("script")
    root = REPO_ROOT
    if not script or not os.path.exists(os.path.join(root, script)):
        return False, f"Script not found: {script}"

    args = build_agent_args(agent_name, config)
    proc = None
    try:
        mark_agent_running(agent_name)
        log_path = start_agent_log(agent_name)
        _append_log(agent_name, f"$ {' '.join(args[1:])}")
        print(f"[INFO] Starting {agent_name}: {' '.join(args[1:])}")

        proc = subprocess.Popen(
            args, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
        _agent_process[agent_name] = proc

        for line in proc.stdout:
            _append_log(agent_name, line.rstrip("\n"))

        try:
            proc.wait(timeout=AGENT_RUN_TIMEOUT_SECS)
        except subprocess.TimeoutExpired:
            proc.kill()
            msg = f"Execution timed out (limit {AGENT_RUN_TIMEOUT_SECS // 60} min)"
            _append_log(agent_name, f"[ERROR] {msg}")
            with _agent_runs_lock:
                _agent_runs[agent_name] = {"status": "error", "error": msg}
            return False, msg

        invalidate_runs_cache()  # the script just wrote its agent_runs row

        if proc.returncode == 0:
            mark_agent_idle(agent_name)
            _append_log(agent_name, "[DONE] Agent completed (exit 0)")
            print(f"[INFO] {agent_name} finished (exit 0)")
            return True, "Agent completed"

        # A non-zero exit usually means it died before writing any agent_runs row (bad
        # flag, missing key), so the DB has no record of the attempt — surface the tail of
        # its output as the error, since that's the only trace that exists.
        with _agent_logs_lock:
            tail = "\n".join((_agent_logs.get(agent_name) or {}).get("lines", [])[-15:])
        _append_log(agent_name, f"[ERROR] Agent exited {proc.returncode}")
        with _agent_runs_lock:
            _agent_runs[agent_name] = {"status": "error", "error": tail[-500:]}
        print(f"[WARN] {agent_name} failed (exit {proc.returncode})")
        return False, f"Agent failed (exit {proc.returncode})"

    except Exception as e:
        msg = str(e)
        _append_log(agent_name, f"[ERROR] {msg}")
        with _agent_runs_lock:
            _agent_runs[agent_name] = {"status": "error", "error": msg}
        print(f"[ERROR] Failed to run {agent_name}: {msg}")
        return False, f"Error running agent: {msg}"
    finally:
        close_agent_log(agent_name)
        _agent_process[agent_name] = None


# ---------- Maintenance tools ----------
#
# Standalone scripts an operator runs by hand from the console's Run > Utilities / Core /
# Mailing Lists / Inspect flows. Unlike the seven agents in AGENT_CONFIGS_SCHEMA these carry
# no preview, no cost estimate and no agent_runs accounting — most are free and read-only,
# and the one paid tool (contact-email backfill) writes its own agent_runs row like any other
# script. They reuse the agent log/stream plumbing, keyed by "tool:<key>" so a tool run and
# an agent run can never collide in the ring buffer or the running-state map.
TOOL_KEY_PREFIX = "tool:"
TOOL_RUN_TIMEOUT_SECS = 1800  # a full contact-email backfill is the longest of these

MAINTENANCE_TOOLS = {
    "inspect": {
        "name": "Inspect an Opportunity",
        "description": "Dump everything stored for one program id — deadline data first. "
                       "The place to start when a student reports something wrong.",
        "script": "check_opp_data.py",
        "free": True, "writes": False,
        "params": [{"key": "ids", "label": "Opportunity id(s)", "required": True,
                    "placeholder": "ec18694 ec17433"}],
    },
    "refreshprog": {
        "name": "Refresh Progress Report",
        "description": "How many rows the Update Opportunity agent has recently touched. "
                       "Read-only.",
        "script": "check_refresh_progress.py",
        "free": True, "writes": False, "params": [],
    },
    # NOTE: the old "dupfinder" tool card was retired 2026-08-28. The same catalog scan now
    # backs the Run → Duplicates tab (duplicate_report_pairs / flag_suspected_duplicate_pairs),
    # which not only lists suspected pairs but lets a human flag them in place — a superset of
    # the report-only card. find_catalog_dups.py is still imported by duplicate_report_pairs.
    "minehub": {
        # Hub-first discovery: one page that LISTS many programs yields many real rows without
        # a per-search fee. Preview (discover + dedup) is free; extraction is one no-search
        # model call per surviving page (~$0.003) and inserts inactive rows for review.
        "name": "Mine Hub Pages",
        "description": "Discover programs by mining a hub page that lists many — a university "
                       "pre-college index, a city teen page, a listicle. Preview is free; a real "
                       "run extracts (~$0.003/page) and inserts inactive rows for review.",
        "script": "mine_hub_pages.py",
        "free": False, "writes": True,
        "params": [
            {"key": "fromLeads", "label": "…or take N from the discovery queue",
             "placeholder": "5",
             "help": "Leave the URL blank and set this to mine the top N queued leads. Each "
                     "carries its own direction (a round-up is mined off-site, an institution\u2019s "
                     "own index on-site), so the checkbox below is ignored for them."},
            {"key": "url", "label": "Hub page URL",
             "placeholder": "https://ceismc.gatech.edu/programs"},
            {"key": "offDomain", "type": "check",
             "label": "Listicle — follow off-site links, not same-domain"},
            {"key": "maxPages", "label": "Spend ceiling — pages this run may extract",
             "placeholder": "50",
             "help": "Spread evenly across hubs, so a ceiling never silently starves whichever "
                     "hubs came last. A hub it truncates stays queued."},
            {"key": "mode", "type": "select", "label": "Mode", "options": [
                ["preview", "Preview — free, no model call, no writes"],
                ["run", "Run — PAID: extracts and inserts rows for review"]]},
        ],
    },
    "harvestnames": {
        # The counterpart to Mine Hub Pages, for a page that NAMES programs without linking
        # them — a JS-built directory, a library calendar. Operator-pointed only: the router
        # never sends work here on its own, because a page that names without linking is far
        # more often one we simply could not read. Preview is free and prices the run.
        "name": "Harvest Names From a Page",
        "description": "For a page that LISTS programs by name but does not link them. Reads "
                       "the names free, then searches for each one's own page. Preview is free; "
                       "a real run is PAID (~$0.02/name) and inserts inactive rows for review.",
        "script": "harvest_names.py",
        "free": False, "writes": True,
        "params": [
            {"key": "url", "label": "Page URL", "required": True,
             "placeholder": "https://www.collegetransitions.com/dataverse/..."},
            {"key": "maxNames", "label": "Spend ceiling — names per page", "placeholder": "20"},
            {"key": "mode", "type": "select", "label": "Mode", "options": [
                ["preview", "Preview — free, no model call, no writes"],
                ["run", "Run — PAID: searches each name and inserts rows for review"]]},
        ],
    },
    "proposeangles": {
        # Finds thin catalog cells (under-served type/season/subject) and proposes angles to
        # fill them. Both exposed actions are free — commit writes DISABLED seeds a person must
        # still enable, so nothing runs or spends on its own.
        "name": "Propose Search Angles",
        "description": "Analyse where the catalog is thin and propose new scraper angles. "
                       "Preview prints them; Commit writes them as DISABLED seeds for you to "
                       "review and enable. Both are free.",
        "script": "propose_angles.py",
        "free": True, "writes": True,
        "params": [
            {"key": "mode", "type": "select", "label": "Scope", "options": [
                ["national", "National"], ["seattle", "Local (Seattle)"]]},
            {"key": "action", "type": "select", "label": "Action", "options": [
                ["preview", "Preview — print proposals"],
                ["commit", "Commit — write as disabled seeds"]]},
        ],
    },
    "refind": {
        # The counterpart to the Link Checker: check_links deactivates dead-link rows, this
        # tries to resurrect the real ones by finding where the page moved. Paid + gated, and
        # inserts inactive rows into the review queue like the scraper — Preview is free.
        "name": "Re-find Dead Links",
        "description": "Resurrect real programs whose stored link died — one narrow web search "
                       "per row, inserting a fresh inactive row for review. Preview is free; a "
                       "real run is PAID (~$0.02–0.05/row) and needs approval.",
        "script": "refind_dead_links.py",
        "free": False, "writes": True,
        "params": [
            {"key": "mode", "type": "select", "label": "Mode", "options": [
                ["preview", "Preview — free, no search, no writes"],
                ["run", "Run — PAID: searches and inserts rows for review"]]},
            {"key": "limit", "type": "number", "label": "Max rows to re-find (paid run)"},
        ],
    },
    "mlgrader": {
        "name": "Mailing-List Accuracy Grader",
        "description": "Pick the deterministic pilot sample and print the finder --ids string "
                       "to run next. Calls no API.",
        "script": "grade_mailing_lists.py", "fixed_args": ["--sample"],
        "free": True, "writes": False, "params": [],
    },
    "export": {
        "name": "Export Catalog Backup",
        "description": "Write the whole catalog to a diffable opportunities.json snapshot. "
                       "Local file only.",
        "script": "export_json.py",
        "free": True, "writes": True, "params": [],
    },
    "legal": {
        "name": "Rebuild Legal Pages",
        "description": "Regenerate terms.html / privacy.html from the legal markdown source. "
                       "Run after editing anything under legal/.",
        "script": "build_legal.py",
        "free": True, "writes": True, "params": [],
    },
    "contactemail": {
        "name": "Contact Email Finder (backfill)",
        "description": "Backfill only. New opportunities already get a contact email when "
                       "they’re added, and an Update Opportunity refresh keeps it current "
                       "— run this just to fill gaps in older rows or force a re-check.",
        "script": "find_contact_emails.py",
        "free": False, "writes": True,
        "params": [
            {"key": "scope", "type": "select", "label": "Scope", "options": [
                ["all", "All rows missing a contact email"], ["ids", "Specific ids"]]},
            {"key": "ids", "label": "Ids (comma-separated)", "placeholder": "ec18694,ec17433"},
            {"key": "limit", "type": "number", "label": "Limit (optional)"},
            {"key": "force", "type": "check", "label": "Re-check rows that already have one"},
            {"key": "dryRun", "type": "check",
             "label": "Dry run — still calls the paid API, but writes nothing"},
        ],
    },
    # --- Queue tools: they operate on the REVIEW QUEUE the scraper fills, in order. Classify
    # labels each pending page; dedupe finds the same program at a different URL; triage bulk-
    # rejects the junk classes; refresh-index keeps the dedupe index current. Each is preview-first.
    "classifyqueue": {
        "name": "Classify the Review Queue",
        "description": "Read each pending page and label it program / hub / none / stale, so the "
                       "queue shows a class pill and classified hubs feed the mining queue. "
                       "Preview is free; a real run is PAID (~$0.004/row, an unreadable page is "
                       "free) and stamps every row.",
        "script": "classify_queue.py",
        "free": False, "writes": True,
        "params": [
            {"key": "mode", "type": "select", "label": "Mode", "options": [
                ["preview", "Preview — free: row count + cost estimate, no writes"],
                ["write", "Run — PAID: classify each page and stamp the row"]]},
            {"key": "limit", "type": "number", "label": "Pilot: classify only the first N rows",
             "help": "Leave blank to classify the whole queue. A small number is a cheap pilot."},
        ],
    },
    "dedupequeue": {
        "name": "Dedupe the Review Queue",
        "description": "Embed each pending row's fields and find the same program at a DIFFERENT "
                       "URL that the name/URL check misses, tagging a tiered 'possible duplicate "
                       "of…' back-link. Preview is free; a real run is PAID (~a cent total; the "
                       "catalog index is prebuilt).",
        "script": "dedupe_queue.py",
        "free": False, "writes": True,
        "params": [
            {"key": "mode", "type": "select", "label": "Mode", "options": [
                ["preview", "Preview — free: counts + embed cost estimate"],
                ["write", "Run — PAID (~cents): embed and stamp back-links"]]},
            {"key": "classifiedOnly", "type": "check",
             "label": "Only rows a classify run has already labelled"},
        ],
    },
    "triagequeue": {
        "name": "Triage the Review Queue",
        "description": "Once the queue is classified, bulk-REJECT the safe classes in one step — "
                       "hubs (their programs are mined), non-opportunity pages, and stale "
                       "programs. Never touches a fresh program or an unreadable row. FREE, "
                       "reversible, and reuses the console's own moderation.",
        "script": "triage_queue.py",
        "free": True, "writes": True,
        "params": [
            {"key": "mode", "type": "select", "label": "Mode", "options": [
                ["preview", "Preview — free: show the plan, reject nothing"],
                ["reject", "Reject the junk — hubs + none + stale (reversible)"]]},
        ],
    },
    "embedindex": {
        "name": "Refresh Dedupe Index",
        "description": "Embed any active catalog rows missing from the dedupe index, so a scrape "
                       "matches new finds against everything live. Usually unnecessary — "
                       "activating a row now embeds it automatically. Preview is free; a commit "
                       "is PAID (~cents).",
        "script": "build_catalog_embeddings.py",
        "free": False, "writes": True,
        "params": [
            {"key": "mode", "type": "select", "label": "Mode", "options": [
                ["preview", "Preview — free: how many rows need embedding"],
                ["commit", "Commit — PAID (~cents): embed and write the index"]]},
            {"key": "rebuild", "type": "check",
             "label": "Rebuild — re-embed every row, not just the missing ones"},
        ],
    },
}


def maintenance_tools_public():
    """The tool registry for the console, without the script paths."""
    return {k: {"name": c["name"], "description": c["description"],
                "free": c.get("free", False), "writes": c.get("writes", False),
                "params": c.get("params", [])}
            for k, c in MAINTENANCE_TOOLS.items()}


def build_tool_args(tool_key, params):
    """argv for one maintenance tool. Mirrors build_agent_args but far smaller — these scripts
    take a handful of flags at most, and most take none."""
    cfg = MAINTENANCE_TOOLS[tool_key]
    args = [sys.executable, "-u", cfg["script"]]
    args += list(cfg.get("fixed_args") or [])
    params = params or {}
    if tool_key == "inspect":
        # check_opp_data.py takes positional ids; accept commas or spaces from the one field.
        args += str(params.get("ids") or "").replace(",", " ").split()
    elif tool_key == "refind":
        # Default to the free preview; a paid run must be chosen explicitly.
        if str(params.get("mode") or "preview") == "run":
            args += ["--limit", str(_int_or_none(params.get("limit")) or 20)]
        else:
            args.append("--preview")
    elif tool_key == "minehub":
        url = str(params.get("url") or "").strip()
        n = _int_or_none(params.get("fromLeads"))
        if url:
            args += ["--hubs", url]
            if params.get("offDomain"):
                args.append("--off-domain")
        if n:
            # Queued leads carry their own direction, so --off-domain is deliberately NOT passed
            # with them: it is a property of how each lead qualified, not of the run.
            args += ["--from-leads", str(n)]
        cap = _int_or_none(params.get("maxPages"))
        if cap:
            args += ["--max-pages", str(cap)]
        if str(params.get("mode") or "preview") != "run":
            args.append("--preview")
    elif tool_key == "harvestnames":
        url = str(params.get("url") or "").strip()
        if url:
            args += ["--hubs", url]
        cap = _int_or_none(params.get("maxNames"))
        if cap:
            args += ["--max-names", str(cap)]
        if str(params.get("mode") or "preview") != "run":
            args.append("--preview")
    elif tool_key == "proposeangles":
        args += ["--mode", str(params.get("mode") or "national")]
        if str(params.get("action") or "preview") == "commit":
            args.append("--commit")
        else:
            args.append("--preview")
    elif tool_key == "contactemail":
        if str(params.get("scope") or "") == "ids" and params.get("ids"):
            args += ["--ids", str(params["ids"])]
        else:
            args.append("--all")
        limit = _int_or_none(params.get("limit"))
        if limit:
            args += ["--limit", str(limit)]
        if params.get("force"):
            args.append("--force")
        if params.get("dryRun"):
            args.append("--dry-run")
    elif tool_key == "classifyqueue":
        # classify_queue.py: [--preview | --limit N] [--write]
        if str(params.get("mode") or "preview") == "write":
            args.append("--write")
            n = _int_or_none(params.get("limit"))
            if n:
                args += ["--limit", str(n)]
        else:
            args.append("--preview")
    elif tool_key == "dedupequeue":
        # dedupe_queue.py: [--preview] [--write] [--classified-only]
        if str(params.get("mode") or "preview") == "write":
            args.append("--write")
            if params.get("classifiedOnly"):
                args.append("--classified-only")
        else:
            args.append("--preview")
    elif tool_key == "triagequeue":
        # triage_queue.py: [--all-junk] [--dry-run]. Preview = the plan; reject = act.
        args.append("--all-junk")
        if str(params.get("mode") or "preview") != "reject":
            args.append("--dry-run")
    elif tool_key == "embedindex":
        # build_catalog_embeddings.py: [--commit] [--rebuild]; no flag = free preview.
        if str(params.get("mode") or "preview") == "commit":
            args.append("--commit")
            if params.get("rebuild"):
                args.append("--rebuild")
    return args


def run_tool_subprocess(tool_key, params):
    """Run a maintenance-tool script, streaming its output. Keyed by 'tool:<key>' so it reuses
    the agent log/running-state plumbing without colliding with an agent. Returns (ok, msg)."""
    cfg = MAINTENANCE_TOOLS.get(tool_key)
    if not cfg:
        return False, f"Unknown tool: {tool_key}"
    script = cfg.get("script")
    if not script or not os.path.exists(os.path.join(REPO_ROOT, script)):
        return False, f"Script not found: {script}"

    log_key = TOOL_KEY_PREFIX + tool_key
    args = build_tool_args(tool_key, params)
    proc = None
    try:
        mark_agent_running(log_key)
        start_agent_log(log_key)
        _append_log(log_key, f"$ {' '.join(args[1:])}")
        print(f"[INFO] Starting tool {tool_key}: {' '.join(args[1:])}")

        proc = subprocess.Popen(
            args, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
        _agent_process[log_key] = proc
        for line in proc.stdout:
            _append_log(log_key, line.rstrip("\n"))

        try:
            proc.wait(timeout=TOOL_RUN_TIMEOUT_SECS)
        except subprocess.TimeoutExpired:
            proc.kill()
            msg = f"Execution timed out (limit {TOOL_RUN_TIMEOUT_SECS // 60} min)"
            _append_log(log_key, f"[ERROR] {msg}")
            return False, msg

        if proc.returncode == 0:
            _append_log(log_key, "[DONE] Finished (exit 0)")
            return True, "Tool completed"
        _append_log(log_key, f"[ERROR] Exited {proc.returncode}")
        return False, f"Tool failed (exit {proc.returncode})"
    except Exception as e:
        _append_log(log_key, f"[ERROR] {e}")
        return False, f"Error running tool: {e}"
    finally:
        # Unlike an agent, a tool keeps no persistent error state — its output IS the result,
        # shown inline — so every exit path just clears the running flag and closes the log.
        mark_agent_idle(log_key)
        close_agent_log(log_key)
        _agent_process[log_key] = None


# ---------- Scraper seeds (the editable search angles) ----------

# A seed is an ANGLE. `category` was dropped from the editable/selected set 2026-08-23 —
# it was never sent to the model, nothing student-facing reads the column it fed, and its
# one live use was a `type` fallback that guessed wrong 27% of the time. The column stays
# in the table because it is `not null` and dropping it needs DDL this repo cannot run, so
# create_seed() writes SEED_CATEGORY_PLACEHOLDER to satisfy the constraint. Nothing reads it.
SEED_FIELDS = ("mode", "angle", "is_enabled", "sort_order")
SEED_CATEGORY_PLACEHOLDER = "unused"
SEED_SELECT = ("id,mode,angle,is_enabled,sort_order,total_runs,total_found,"
               "total_added,total_dupes,total_cost,last_run_at,created_at")
# disabled_reason/disabled_at arrived after the table (scraper_seeds_schema.sql ALTER block).
# Selected on top of SEED_SELECT, with a degrade rung so a DB migrated before them keeps a
# working grid — the moderation_reason pattern for opportunities, one table over.
SEED_DISABLE_COLS = "disabled_reason,disabled_at"


def list_seeds(mode=None):
    """All seeds (enabled and disabled) for the console's angle manager.

    Adds derived productivity figures the UI ranks by — added_per_dollar is the one that
    actually answers "is this angle worth paying for every month".
    """
    params = {"order": "mode.asc,sort_order.asc,id.asc"}
    if mode:
        params["mode"] = f"eq.{mode}"
    # Try with the disable-reason columns; fall back if that migration is still pending.
    rows = _supabase_request("scraper_seeds",
                             params={**params, "select": f"{SEED_SELECT},{SEED_DISABLE_COLS}"})
    if rows is None:
        rows = _supabase_request("scraper_seeds", params={**params, "select": SEED_SELECT})
    if rows is None:
        return None
    for r in rows:
        cost = float(r.get("total_cost") or 0)
        added = r.get("total_added") or 0
        found = r.get("total_found") or 0
        r["added_per_dollar"] = round(added / cost, 1) if cost > 0 else None
        r["dupe_rate"] = round((r.get("total_dupes") or 0) / found, 3) if found else None
    return rows


def get_seed_yield(seed_rows):
    """Per-seed funnels for the console grid — a live GROUP BY over the reviewer's verdicts.

    The catalog IS the ledger: each scraped row carries the seed_id of the angle that found
    it and the verdict a reviewer later records, so an angle's funnel is computed on read,
    not stored — a changed verdict corrects it on the next refresh with nothing to recompute.
    `seed_rows` is the already-loaded list_seeds() result (avoids a second read of the small
    seeds table). seed_id arrived with scraper_attribution_schema.sql; a DB migrated before
    that reads seed_ready=false and the grid hides the funnel columns rather than erroring.

    Uses the paginating supabase_get, not _supabase_request: this is a GROUP BY and must see
    every attributed row, not just the first PostgREST page.
    """
    if not seed_rows:
        return {"seed_ready": False, "funnels": {}}
    try:
        opp_rows = supabase_get(SUPABASE_URL, "opportunities",
                                {"select": "seed_id,moderation_status,moderation_reason,is_active",
                                 "seed_id": "not.is.null"}, SUPABASE_SERVICE_KEY)
    except Exception as e:
        # Tell the migration-pending case apart from a transient blip: only the former should
        # point the operator at the .sql file, and a timeout on an already-migrated DB must
        # not tell them to run a migration that is already in place.
        if _is_missing_column_error(e):
            print("[WARN] seed_id column missing — funnel disabled until "
                  "scraper_attribution_schema.sql is run.")
        else:
            print(f"[WARN] Could not read seed attribution ({e}); funnel temporarily "
                  f"unavailable.")
        return {"seed_ready": False, "funnels": {}}
    return {"seed_ready": True, "funnels": seed_ledger.build_seed_funnels(opp_rows, seed_rows)}


def list_discovered_leads(limit=60):
    """The discovery lead queue, for the console. FREE — reads a local file, never the model.

    The queue had no surface at all: `discovered_leads.jsonl` is gitignored, the console WROTE to
    it (the round-up reject hook) and never showed it, so the only way to see 245 queued leads was
    a terminal command. That is also why "is any of this automatic?" was hard to answer from the
    outside — the answer is that the queue fills itself and nothing ever drains it.

    Order is QUEUE order, deliberately: it is the order `--from-leads N` takes them, so the top of
    this list is exactly what a run of N would spend on. Sorting it prettily here would make the
    console disagree with the tool.
    """
    try:
        import discovered_leads
    except Exception as e:                              # pragma: no cover - import guard
        return {"ok": False, "error": str(e)[:200], "leads": []}
    try:
        leads = discovered_leads.load_leads()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "leads": []}

    counts = {"new": 0, "processed": 0, "not_a_lead": 0,
              "hub_same_domain": 0, "hub_off_domain": 0, "names": 0}
    queue = []
    for l in leads:
        status = l.get("status") or discovered_leads.STATUS_NEW
        if status == discovered_leads.STATUS_DONE:
            counts["processed"] += 1
            continue
        if status == discovered_leads.STATUS_NOT_A_LEAD:
            counts["not_a_lead"] += 1
            continue
        counts["new"] += 1
        kind = l.get("kind")
        scope = discovered_leads.lead_scope(l)
        if kind == discovered_leads.KIND_HUB:
            counts["hub_same_domain" if scope == discovered_leads.SCOPE_SAME_DOMAIN
                   else "hub_off_domain"] += 1
        elif kind == discovered_leads.KIND_NAMES:
            counts["names"] += 1
        if len(queue) < limit:
            queue.append({"url": l.get("url"), "kind": kind, "scope": scope,
                          "signal": l.get("signal"), "angle": l.get("angle"),
                          "seed_id": l.get("seed_id"), "first_seen": l.get("first_seen")})
    return {"ok": True, "counts": counts, "leads": queue,
            "truncated": max(0, counts["new"] - len(queue)),
            "path": os.path.basename(discovered_leads.LEADS_PATH)}


def list_recent_merges(limit=50):
    """Rows the scraper auto-merged into (Phase 3), newest first — the console's merge audit.

    A merge stamps `merged <date>: <what changed>` into the survivor's quality_flags, so this
    is just the rows carrying such a flag. Audit only (not an approval queue); every entry is
    hand-reversible from quality_flags. Returns [] cleanly if nothing has merged yet."""
    rows = _supabase_request("opportunities", params={
        "select": "id,name,url,is_active,quality_flags,updated_at",
        "quality_flags": "not.is.null", "order": "updated_at.desc", "limit": "300"})
    if rows is None:
        return {"ok": False, "merges": []}
    out = []
    for r in rows:
        notes = [str(f) for f in (r.get("quality_flags") or []) if str(f).startswith("merged ")]
        if notes:
            out.append({"id": r["id"], "name": r.get("name"), "url": r.get("url"),
                        "is_active": r.get("is_active"), "updated_at": r.get("updated_at"),
                        "notes": notes})
        if len(out) >= limit:
            break
    return {"ok": True, "merges": out}


def seed_yield_state(rows):
    """Why the yield columns are empty, when they are.

    A grid of zeros reads as a broken feature. It usually isn't: record_seed_result() is
    deliberately skipped on dry runs (nothing was added, so nothing should be credited),
    and the hardcoded fallback seeds have no id and so cannot be credited at all. Both are
    correct, and both are invisible unless the console says so.
    """
    credited = sum(1 for r in rows if (r.get("total_runs") or 0) > 0)
    if credited:
        return {"credited_seeds": credited, "ever_credited": True, "reason": None}
    live_runs = dry_runs = 0
    for r in recent_runs():
        if r.get("agent") != "scraper" or not r.get("finished_at"):
            continue
        if str(r.get("mode") or "").endswith("-dryrun"):
            dry_runs += 1
        else:
            live_runs += 1
    if dry_runs and not live_runs:
        reason = (f"No angle has been credited yet: all {dry_runs} scraper run(s) on record "
                  f"were dry runs, and a dry run adds nothing so it credits nothing. "
                  f"Figures appear after the first live run.")
    elif live_runs:
        reason = (f"No angle has been credited yet. {live_runs} live run(s) exist, but they "
                  f"predate this table and used the hardcoded fallback angles, which have "
                  f"no id and cannot be credited.")
    else:
        reason = "No angle has been credited yet: no scraper run has completed."
    return {"credited_seeds": 0, "ever_credited": False, "reason": reason,
            "live_runs": live_runs, "dry_runs": dry_runs}


# ---------- What each angle actually SEARCHED (query telemetry) ----------
#
# An angle is a prompt fragment; the queries are Gemini's own. They come back as telemetry and
# `scrape_opportunities.main()` writes one `scraper_<stamp>_seed<id>.json` per angle per run.
# Nothing read them back until this view existed, so nobody could see whether an angle turned
# into BROAD searches (which can surface a program nobody has named) or into NAMED ones (whose
# ceiling is one program the model already had in mind). The judgement lives in the pure
# `query_telemetry` module; this half is only the disk read.
_SEED_LOG_RE = re.compile(r"^scraper_(\d{8}-\d{6}|\d{8})_seed(\d+)\.json$")


def _seed_log_files():
    """(stamp, seed_id, path) for every per-seed scraper log on disk, newest run first.

    Both filename shapes are accepted for the same reason `dryrun_common` accepts both: logs
    written before 2026-08-22 carry a date-only stamp. A date-only stamp sorts before any
    timestamped one from the same day, which is the right order — it IS earlier or unknown.
    """
    out = []
    try:
        names = os.listdir(AGENT_LOG_DIR)
    except OSError:
        return out
    for name in names:
        m = _SEED_LOG_RE.match(name)
        if m:
            out.append((m.group(1), int(m.group(2)), os.path.join(AGENT_LOG_DIR, name)))
    out.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return out


def _scraper_runs_without_logs(run_stamps):
    """Live scraper runs in `agent_runs` that have no per-seed log on disk.

    These files are LOCAL and untracked: a run executed in a git worktree writes them into that
    worktree, so removing it takes the telemetry with it. That is exactly what happened to the
    2026-08-27 runs. Reporting the gap is the whole point — a query view that silently shows
    the newest run it happens to hold reads as "this is the latest run", which is the one thing
    it must never imply.

    Only runs NEWER than the newest log on disk count as missing. Per-seed logging landed with
    the two-phase scraper on 2026-08-23; every earlier run predates the telemetry entirely, so
    listing those would bury the two runs that really did lose their logs under eleven that
    never had any.
    """
    days = {s[:8] for s in run_stamps}
    newest_day = max(days) if days else ""
    missing = []
    for r in recent_runs():
        if r.get("agent") != "scraper" or not r.get("started_at"):
            continue
        if str(r.get("mode") or "").endswith("-dryrun"):
            continue
        day = str(r.get("started_at"))[:10].replace("-", "")
        if day and day not in days and day > newest_day:
            missing.append({"run_id": r.get("id"), "started_at": r.get("started_at"),
                            "items": r.get("items_processed"), "cost_usd": r.get("cost_usd")})
    return missing


def list_seed_query_runs(run=None, limit_runs=12):
    """Per-angle search-query telemetry for one scraper run (the newest by default).

    Returns `{runs: [...stamps...], run: <stamp>, summary: {...}, seeds: [...]}`. Each seed row
    carries its angle, search/attempt counts, and every query it issued classified broad /
    named / metadata. Angle text comes from the LOG, not from `scraper_seeds`: the log records
    the phrase that was actually sent, and an angle edited in the console since would otherwise
    be shown as if it were what ran.
    """
    files = _seed_log_files()
    stamps = []
    for stamp, _sid, _path in files:
        if stamp not in stamps:
            stamps.append(stamp)
    if not stamps:
        return {"runs": [], "run": None, "summary": None, "seeds": [],
                "missing_runs": _scraper_runs_without_logs([]),
                "note": "No per-seed query logs on disk. They are written to agent_logs/ by a "
                        "live scraper run and are local, untracked files."}
    chosen = run if run in stamps else stamps[0]
    seeds = []
    for stamp, seed_id, path in files:
        if stamp != chosen:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                entry = json.load(fh)
        except (OSError, ValueError) as e:
            # A half-written or unreadable log must cost one row, never the whole view.
            seeds.append({"seed_id": seed_id, "angle": "", "error": str(e)[:120],
                          "queries": [], "total": 0, "counts": {}, "breadth": None})
            continue
        seeds.append({"seed_id": seed_id, **query_telemetry.summarize_seed(entry)})
    seeds.sort(key=lambda s: ((s.get("breadth") if s.get("breadth") is not None else 2),
                              s.get("seed_id") or 0))
    return {
        "runs": stamps[:limit_runs],
        "run": chosen,
        "summary": query_telemetry.summarize_run(seeds),
        "seeds": seeds,
        "missing_runs": _scraper_runs_without_logs(stamps),
    }


def create_seed(payload):
    row = {k: payload[k] for k in SEED_FIELDS if k in payload}
    if not row.get("mode") or not row.get("angle"):
        return None, "mode and angle are both required"
    # Satisfies the surviving not-null column; see SEED_CATEGORY_PLACEHOLDER.
    row["category"] = SEED_CATEGORY_PLACEHOLDER
    if row["mode"] not in ("national", "seattle"):
        return None, "mode must be 'national' or 'seattle'"
    row.setdefault("is_enabled", True)
    if row.get("sort_order") is None:
        existing = _supabase_request("scraper_seeds", params={
            "select": "sort_order", "mode": f"eq.{row['mode']}",
            "order": "sort_order.desc", "limit": "1"}) or []
        row["sort_order"] = ((existing[0].get("sort_order") or 0) + 1) if existing else 0
    created = _supabase_request("scraper_seeds", method="POST", data=[row],
                                 extra_headers={"Prefer": "return=representation"})
    if created is None:
        return None, "Supabase insert failed"
    return (created[0] if created else row), None


def update_seed(seed_id, payload):
    patch = {k: payload[k] for k in SEED_FIELDS if k in payload}
    if not patch:
        return None, "No editable fields supplied"
    # Re-enabling an angle (the manual toggle and the auto-disabled "Re-enable" button both
    # land here) clears the auto-disable reason, so the badge disappears and the angle reads
    # as a clean producer again. Manual disabling leaves disabled_reason NULL, which is how a
    # hand-disabled angle is told apart from an auto-retired one.
    if patch.get("is_enabled") is True:
        patch["disabled_reason"] = None
        patch["disabled_at"] = None

    def _patch(body):
        return _supabase_request("scraper_seeds", method="PATCH",
                                 params={"id": f"eq.{seed_id}"}, data=body,
                                 extra_headers={"Prefer": "return=representation"})

    updated = _patch(patch)
    if updated is None and ("disabled_reason" in patch or "disabled_at" in patch):
        # Those columns may not exist yet (scraper_seeds_schema.sql pending) — retry the
        # toggle without them so enabling/disabling still works.
        updated = _patch({k: v for k, v in patch.items()
                          if k not in ("disabled_reason", "disabled_at")})
    if updated is None:
        return None, "Supabase update failed"
    return (updated[0] if updated else {}), None


def delete_seed(seed_id):
    """Hard delete. The console prefers toggling is_enabled, since deleting also destroys
    the angle's accumulated yield history."""
    result = _supabase_request("scraper_seeds", method="DELETE",
                                params={"id": f"eq.{seed_id}"})
    return (result is not None), None if result is not None else "Supabase delete failed"


# ---------------- Lifecycle email (review side) ----------------
#
# Thin wrappers over app/services/email.py rather than a second implementation. The console
# must render exactly what a student receives — a preview built from its own copy of the
# templates is a preview of something that is not being sent.

def get_email_overview(limit=100):
    from app.services import email as email_service
    return email_service.email_status(limit=limit)


def preview_lifecycle_email(kind, userid=None):
    from app.services import email as email_service
    from app.services.email_templates import EMAIL_KINDS
    if kind not in EMAIL_KINDS:
        return {"ok": False, "error": f"Unknown email kind: {kind!r}. "
                                      f"Known: {', '.join(EMAIL_KINDS)}"}
    return email_service.preview(kind, userid=(userid or "").strip() or None)


def send_test_lifecycle_email(kind, to_email, userid=None):
    """Mimic the email a real user would receive, sent to an operator address on demand.

    When a userid is given the FULL account (data included) is loaded via email_service —
    get_user_account omits the `data` blob, and the deadline-alert digest is rendered from
    the tracker that lives there, so mimicking that kind needs the whole row. No userid falls
    back to the sample record inside send_test.
    """
    from app.services import email as email_service
    record = email_service.load_full_record(userid) if userid else None
    return email_service.send_test(kind, to_email, record=record)


def run_lifecycle_sweep(dry_run=False):
    from app.services import email as email_service
    return email_service.run_trial_sweep(dry_run=dry_run)
