"""Local-only ops console router: /api/agents/*, /api/seeds, /admin.

Translated from the admin half of server.py's Handler (PLAN_1_decompose.md). This
router is mounted ONLY when WINGMAN_ENABLE_OPS is set (see app.main), so the shipped
web service exposes none of these routes — they 404 there. Every route is additionally
gated to localhost by require_local, exactly like the old _require_local, because these
endpoints spend money and expose a roster of names/emails.
"""
import threading

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import dryrun_common
from ops import core
from app.deps import read_json_body, read_json_body_strict, json_response, json_error

_LOCAL_HOSTS = ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")


def require_local(request: Request):
    """Admin routes are localhost-only: the server binds all interfaces and these
    routes launch subprocesses that spend real money. Mirrors Handler._require_local."""
    client = request.client.host if request.client else ""
    if client not in _LOCAL_HOSTS:
        print(f"[WARN] Blocked non-local admin request from {client}: {request.url.path}")
        raise HTTPException(status_code=403, detail="Admin routes are restricted to localhost.")


router = APIRouter(dependencies=[Depends(require_local)])


def _qs_int(request, key, default, lo=None, hi=None):
    val = request.query_params.get(key)
    try:
        n = int(val)
    except (TypeError, ValueError):
        n = default
    if n is None:
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


# ---------------- Admin console page ----------------

@router.get("/admin")
def handle_admin_console():
    html = core.get_admin_console_html()
    return Response(content=html.encode(), media_type="text/html; charset=utf-8")


@router.get("/admin/logic-map")
def handle_logic_map():
    """The full Catalog Control Room map (repo-root logic_map.html), served with its SVGs.
    Localhost-only like every route on this router; the console's guide tab links here so the
    map has a single source and never needs re-porting into the console HTML."""
    html = core.get_logic_map_html()
    return Response(content=html.encode(), media_type="text/html; charset=utf-8")


# ---------------- Agents: read ----------------

@router.get("/api/agents/status")
def handle_agents_status():
    return json_response(200, core.get_all_agents_status(), default=str)


@router.get("/api/agents/history")
def handle_agents_history(request: Request):
    history = core.get_agent_history(
        agent=request.query_params.get("agent"),
        limit=_qs_int(request, "limit", 50),
        days=_qs_int(request, "days", None),
    )
    return json_response(200, history, default=str)


@router.get("/api/agents/summary")
def handle_agents_summary(request: Request):
    days = _qs_int(request, "days", 30) or 30
    summary = core.get_agents_summary(days=max(1, min(days, 365)))
    return json_response(200, summary, default=str)


@router.get("/api/agents/config")
def handle_agents_config():
    return json_response(200, {
        "agents": core.agents_config_payload(),
        "run_timeout_secs": core.AGENT_RUN_TIMEOUT_SECS,
        "setting_bounds": core.SETTING_BOUNDS,
        "recommended_min_delay": core.RECOMMENDED_MIN_DELAY,
    }, default=str)


@router.get("/api/agents/snapshots")
def handle_snapshots_list():
    return json_response(200, {
        "snapshots": core.annotate_committed_snapshots(dryrun_common.list_snapshots()),
    }, default=str)


@router.get("/api/agents/pending")
def handle_pending_list(request: Request):
    limit = _qs_int(request, "limit", 500) or 500
    status = request.query_params.get("status") or "queue"
    if status not in ("queue", "rejected", "all", "flagged"):
        return json_error(400, f"Unknown status filter: {status}")
    result = core.list_pending_opportunities(
        limit=limit, source=request.query_params.get("source"), status=status)
    return json_response(200 if result.get("ok") else 502, result, default=str)


@router.get("/api/agents/opportunities/search")
def handle_opportunity_search(request: Request):
    result = core.search_opportunities(
        request.query_params.get("q"), _qs_int(request, "limit", 25) or 25)
    return json_response(200, result, default=str)


@router.get("/api/agents/signups")
def handle_signups_list(request: Request):
    status = request.query_params.get("status", "pending_review")
    limit = _qs_int(request, "limit", 200) or 200
    result = core.list_signup_recipes(status=status, limit=max(1, min(limit, 1000)))
    return json_response(200 if result.get("ok") else 502, result, default=str)


@router.get("/api/agents/user-costs")
def handle_agents_user_costs(request: Request):
    days = _qs_int(request, "days", 30) or 30
    limit = _qs_int(request, "limit", 200) or 200
    return json_response(200, core.get_user_costs(
        days=max(1, min(days, 365)), limit=max(1, min(limit, 1000))), default=str)


@router.get("/api/agents/metrics")
def handle_agents_metrics(request: Request):
    days = _qs_int(request, "days", 30) or 30
    limit = _qs_int(request, "limit", 500) or 500
    return json_response(200, core.get_user_metrics(
        days=max(1, min(days, 365)), limit=max(1, min(limit, 2000))), default=str)


@router.get("/api/agents/billed")
def handle_agents_billed(request: Request):
    days = _qs_int(request, "days", 30) or 30
    return json_response(200, core.get_billed_costs(max(1, min(days, 365))), default=str)


@router.get("/api/agents/log")
def handle_agents_log(request: Request):
    agent = request.query_params.get("agent")
    # Agents key by their bare name; maintenance tools key by "tool:<key>" (same ring buffer).
    is_tool = (isinstance(agent, str) and agent.startswith(core.TOOL_KEY_PREFIX)
               and agent[len(core.TOOL_KEY_PREFIX):] in core.MAINTENANCE_TOOLS)
    if agent not in core.AGENT_CONFIGS_SCHEMA and not is_tool:
        return json_error(400, f"Unknown agent: {agent}")
    since = _qs_int(request, "since", 0) or 0
    return json_response(200, core.get_agent_log(agent, since), default=str)


# ---------------- Agents: write ----------------

@router.post("/api/agents/snapshots/commit")
async def handle_snapshot_commit(request: Request):
    body = await read_json_body(request)
    file_name = (body.get("file") or "").strip()
    preview = bool(body.get("preview"))
    result = core.commit_dryrun_snapshot(file_name, dry=preview)
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.post("/api/agents/pending/activate")
async def handle_pending_activate(request: Request):
    body = await read_json_body(request)
    active = body.get("active", True)
    result = core.activate_opportunities(body.get("ids") or [], active=bool(active))
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.post("/api/agents/pending/moderate")
async def handle_pending_moderate(request: Request):
    body = await read_json_body(request)
    result = core.moderate_opportunities(
        body.get("ids") or [], (body.get("status") or "").strip(),
        duplicate_of=body.get("duplicate_of"), reason=body.get("reason"))
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.post("/api/agents/pending/update")
async def handle_pending_update(request: Request):
    body = await read_json_body(request)
    result = core.update_pending_opportunity(body.get("id"), body.get("fields") or {})
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.post("/api/agents/pending/clear-dup-verdict")
async def handle_clear_dup_verdict(request: Request):
    """Clear the resolved dup_verdict on the given rows — the Duplicate queue's "not a
    duplicate" action. Body: {ids: [...]}. Touches only dup_verdict."""
    body = await read_json_body(request)
    result = core.clear_dup_verdict(body.get("ids") or [])
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.get("/api/agents/metadata-refresh-queue")
def handle_metadata_refresh_queue(request: Request):
    """Read-only: rows activated but not yet run through refresh_opportunities.py. Backs the
    Core Details card. Localhost-gated like the rest of /api/agents/* (this list carries row
    names/urls)."""
    limit = _qs_int(request, "limit", 200) or 200
    result = core.metadata_refresh_queue(limit=limit)
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.get("/api/agents/health")
def handle_db_health():
    """Read-only, one-shot database health snapshot for the console's Health tab. FREE — reads
    Supabase and two local files, makes no model call and writes nothing. Localhost-gated like
    the rest of /api/agents/* (the payload carries queue depths and run history)."""
    return json_response(200, core.get_db_health(), default=str)


@router.post("/api/agents/duplicate-scan")
async def handle_duplicate_scan(request: Request):
    """Phase 3: run the unified ad-hoc duplicate detector over ACTIVE rows and stamp one
    dup_verdict per changed row. Backs the console's 'Scan for duplicates' button. FREE, but
    synchronous (~30-40s). Body: {write: bool} (default true)."""
    body = await read_json_body(request)
    write = body.get("write", True)
    # ~30-40s of blocking IO + numpy — offload so it doesn't freeze the ops event loop.
    result = await run_in_threadpool(core.scan_catalog_duplicates, write=bool(write))
    return json_response(200 if result.get("ok") else 502, result, default=str)


@router.get("/api/agents/duplicate-report")
def handle_duplicate_report(request: Request):
    """DEPRECATED (Phase 3 → removed in Phase 5): the old read-only pair report backing the
    two-step Scan→flag flow, superseded by /api/agents/duplicate-scan. Kept reachable so a
    stale bundle does not 404 mid-migration."""
    limit = _qs_int(request, "limit", 2000) or 2000
    result = core.duplicate_report_pairs(limit=limit)
    return json_response(200 if result.get("ok") else 502, result, default=str)


@router.post("/api/agents/duplicate-report/flag")
async def handle_duplicate_flag(request: Request):
    """Flag the reviewed pairs as suspected_duplicate (flag-in-place — both rows stay live).
    Body: {pairs: [{id, duplicate_of, reason}]}."""
    body = await read_json_body(request)
    result = core.flag_suspected_duplicate_pairs(body.get("pairs") or [])
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.post("/api/agents/deadline/clear-cache")
async def handle_deadline_clear_cache(request: Request):
    """Force a re-check by nulling dates_last_checked_at. `preview: true` counts without
    writing; `all: true` needs `confirm: true` because it queues a paid check per row."""
    body = await read_json_body(request)
    want_all = bool(body.get("all"))
    preview = bool(body.get("preview"))
    if want_all and not preview and not body.get("confirm"):
        return json_error(400, "Clearing every active row needs an explicit confirmation.")
    result = core.clear_deadline_cache(ids=body.get("ids") or [], want_all=want_all,
                                       dry=preview)
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.post("/api/agents/signups/moderate")
async def handle_signups_moderate(request: Request):
    body = await read_json_body(request)
    result = core.moderate_signup_recipes(
        body.get("ids") or [], (body.get("status") or "").strip())
    return json_response(200 if result.get("ok") else 400, result, default=str)


# ---------------- Sources: trusted-domain allowlist (P5) ----------------

@router.get("/api/agents/aggregators")
def handle_aggregators_list():
    return json_response(200, core.list_trusted_aggregators(), default=str)


@router.post("/api/agents/aggregators")
async def handle_aggregator_upsert(request: Request):
    body = await read_json_body(request)
    result = core.upsert_trusted_aggregator(
        (body.get("domain") or "").strip(),
        (body.get("status") or "").strip(),
        label=body.get("label"),
        notes=body.get("notes"))
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.post("/api/agents/aggregators/delete")
async def handle_aggregator_delete(request: Request):
    """POST, not DELETE /{domain}: a domain carries dots and slashes that make a path segment
    awkward, so the domain travels in the body."""
    body = await read_json_body(request)
    result = core.delete_trusted_aggregator((body.get("domain") or "").strip())
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.post("/api/agents/preview")
async def handle_agents_preview(request: Request):
    body = await read_json_body(request)
    agent_name = (body.get("agent") or "").strip()
    if agent_name not in core.AGENT_CONFIGS_SCHEMA:
        return json_error(400, f"Unknown agent: {agent_name}")
    result = core.preview_agent(agent_name, body.get("config") or {})
    return json_response(200 if result.get("ok") else 400, result, default=str)


@router.post("/api/agents/run")
async def handle_agents_run(request: Request):
    body = await read_json_body(request)
    agent_name = (body.get("agent") or "").strip()
    if agent_name not in core.AGENT_CONFIGS_SCHEMA:
        return json_error(400, f"Unknown agent: {agent_name}")

    if core.is_agent_running(agent_name):
        return json_error(409, f"{agent_name} is already running.")

    if core.AGENT_CONFIGS_SCHEMA[agent_name].get("uses_gemini_search"):
        blocker = core.running_gemini_search_agent(exclude=agent_name)
        if blocker:
            name = core.AGENT_CONFIGS_SCHEMA[blocker]["name"]
            return json_error(
                409, f"{name} is running and holds the shared Gemini web-search lock. "
                     f"Only one search-enabled agent can run at a time.")

    config = body.get("config") or {}
    argv = core.build_agent_args(agent_name, config)
    threading.Thread(
        target=lambda: core.run_agent_subprocess(agent_name, config), daemon=True).start()

    return json_response(202, {
        "ok": True,
        "agent": agent_name,
        "dry_run": bool(config.get("dryRun")),
        "argv": argv[1:],
        "message": f"{core.AGENT_CONFIGS_SCHEMA[agent_name]['name']} started",
    }, default=str)


# ---------------- Maintenance tools ----------------

@router.get("/api/agents/tools")
def handle_tools_list():
    return json_response(200, {"tools": core.maintenance_tools_public()}, default=str)


@router.post("/api/agents/tools/run")
async def handle_tools_run(request: Request):
    body = await read_json_body(request)
    tool = (body.get("tool") or "").strip()
    cfg = core.MAINTENANCE_TOOLS.get(tool)
    if not cfg:
        return json_error(400, f"Unknown tool: {tool}")

    log_key = core.TOOL_KEY_PREFIX + tool
    if core.is_agent_running(log_key):
        return json_error(409, f"{cfg['name']} is already running.")

    params = body.get("params") or {}
    for p in cfg.get("params", []):
        if p.get("required") and not str(params.get(p["key"]) or "").strip():
            return json_error(400, f"{p['label']} is required.")

    argv = core.build_tool_args(tool, params)
    threading.Thread(
        target=lambda: core.run_tool_subprocess(tool, params), daemon=True).start()

    return json_response(202, {
        "ok": True,
        "tool": tool,
        "log_key": log_key,
        "argv": argv[1:],
        "message": f"{cfg['name']} started",
    }, default=str)


# ---------------- Lifecycle email ----------------
#
# The console owns REVIEW of lifecycle email — the send log, template previews, a test send
# to yourself, and who is currently due a trial reminder. It deliberately does NOT own the
# daily trial sweep: this router is localhost-gated and never mounted on Render, so a
# button here could only fire on days this machine is on. That trigger is
# POST /api/email/sweep on the shipped service (app/routes/email.py). The manual-run button
# below exists for a catch-up, not as the schedule.

@router.get("/api/agents/emails")
def handle_emails(request: Request):
    return json_response(200, core.get_email_overview(
        limit=_qs_int(request, "limit", 100, 1, 500)), default=str)


@router.get("/api/agents/emails/preview")
def handle_email_preview(request: Request):
    kind = (request.query_params.get("kind") or "").strip()
    result = core.preview_lifecycle_email(kind, request.query_params.get("userid"))
    # An empty digest (mimicking a user with nothing due) is informational, not a 400.
    ok = result.get("ok") or result.get("empty_digest")
    return json_response(200 if ok else 400, result, default=str)


@router.post("/api/agents/emails/test")
async def handle_email_test(request: Request):
    """Send one template to an address the operator types. Not deduped and never recorded —
    see app/services/email.send_test on why a test must not consume a real user's send."""
    body = await read_json_body(request)
    result = core.send_test_lifecycle_email(
        (body.get("kind") or "").strip(),
        (body.get("to") or "").strip(),
        body.get("userid"))
    # 'skipped' is the honest "nothing to send" case (e.g. mimicking a user's deadline digest
    # when they have nothing due) — informational, not a failure.
    return json_response(200 if result.get("state") in ("sent", "mock", "skipped") else 400,
                         result, default=str)


@router.post("/api/agents/emails/sweep")
async def handle_email_sweep_manual(request: Request):
    """A manual catch-up run of the trial sweep, for when the scheduler was down. Safe to
    press twice — the email_sends claim is what prevents a re-send, not this button."""
    body = await read_json_body(request)
    result = core.run_lifecycle_sweep(dry_run=bool(body.get("dry_run")))
    return json_response(200 if result.get("ok") else 400, result, default=str)


# ---------------- Scraper seed CRUD ----------------

@router.get("/api/seeds")
def handle_seeds_list(request: Request):
    rows = core.list_seeds(mode=request.query_params.get("mode"))
    if rows is None:
        return json_error(502, "Could not read scraper_seeds. Has the table been created? "
                               "See scraper_seeds_schema.sql.")
    # Attach each angle's live verdict funnel (a GROUP BY over opportunities.seed_id). Absent
    # until scraper_attribution_schema.sql is run, in which case seed_ready is false and the
    # grid simply hides the funnel columns.
    yld = core.get_seed_yield(rows)
    if yld["seed_ready"]:
        for r in rows:
            r["funnel"] = yld["funnels"].get(r.get("id"))
    return json_response(200, {"ok": True, "seeds": rows, "seed_ready": yld["seed_ready"],
                               "yield": core.seed_yield_state(rows)}, default=str)


@router.get("/api/seeds/queries")
def handle_seed_queries(request: Request):
    """What each angle actually SEARCHED on one run. Reads local agent_logs/, never the model."""
    return json_response(200, {"ok": True, **core.list_seed_query_runs(
        run=request.query_params.get("run"))}, default=str)


@router.get("/api/agents/leads")
def handle_leads_list(request: Request):
    """The discovery lead queue. FREE — reads the local JSONL, never the model or Supabase."""
    return json_response(200, core.list_discovered_leads(
        limit=int(request.query_params.get("limit") or 60)), default=str)


@router.get("/api/agents/merges")
def handle_merges_list(request: Request):
    return json_response(200, core.list_recent_merges(
        limit=_qs_int(request, "limit", 50) or 50), default=str)


@router.post("/api/seeds")
async def handle_seed_create(request: Request):
    body, parse_error = await read_json_body_strict(request)
    if parse_error:
        return json_error(400, parse_error)
    row, error = core.create_seed(body or {})
    if error:
        return json_error(400, error)
    return json_response(201, {"ok": True, "seed": row}, default=str)


@router.patch("/api/seeds/{seed_id}")
async def handle_seed_update(seed_id: str, request: Request):
    body, parse_error = await read_json_body_strict(request)
    if parse_error:
        return json_error(400, parse_error)
    row, error = core.update_seed(seed_id, body or {})
    if error:
        return json_error(400, error)
    return json_response(200, {"ok": True, "seed": row}, default=str)


@router.delete("/api/seeds/{seed_id}")
def handle_seed_delete(seed_id: str):
    ok, error = core.delete_seed(seed_id)
    if not ok:
        return json_error(502, error or "Delete failed")
    return json_response(200, {"ok": True, "deleted": seed_id})


@router.patch("/api/agents/settings/{agent_name}")
async def handle_agent_settings(agent_name: str, request: Request):
    defaults, error = core.save_agent_settings(agent_name, await read_json_body(request) or {})
    if error:
        return json_error(400, error)
    return json_response(200, {
        "ok": True, "agent": agent_name, "defaults": defaults,
        "builtin_defaults": core.AGENT_CONFIGS_SCHEMA[agent_name].get("defaults") or {},
    }, default=str)
