#!/usr/bin/env python3
"""Deprecated entrypoint — kept so `python server.py` still boots the local dev server.

Phase 1 of the rearchitecture (docs/archive/PLAN_1_decompose.md) replaced the old 6,956-line
http.server monolith that lived here with a FastAPI application under app/ (public web
service) plus a local-only ops console under ops/. The whole former contents of this
file were sliced verbatim into:

    app/config.py            env + shared constants
    app/core.py              Supabase plumbing, accounts, cost/activity accounting
    app/services/*.py        opportunities, deadlines, ai (mocks), mailing_list,
                             google_oauth, resume
    app/routes/*.py          the FastAPI routers (one per domain)
    app/main.py              the FastAPI app (uvicorn app.main:app)
    ops/core.py, ops/admin.py    agent orchestration, metrics, seeds, review queue
    ops/admin_console.html       (moved from the repo root)

The offline agents and their shared libs (gemini_common, claude_common, agent_common,
supabase_common, subscription_common, mailing_list_common, url_dedupe, dryrun_common,
check_deadlines, ...) stay at the repo root as the shared layer both app/ and ops/ import.

Running this launches uvicorn with the ops console ENABLED (local dev). Production/Render
runs `uvicorn app.main:app --host 0.0.0.0 --port $PORT` and never enables ops, so the
shipped service exposes no /api/agents/*, /api/seeds, or /admin route.
"""
import os
import secrets

# Local dev convenience: expose the admin console at /admin, as the old server did. Guarded
# so it is NEVER enabled on Render (which sets RENDER=true) — even if the service's start
# command is still `python server.py`, the public deploy must not expose /admin or the
# money-spending /api/agents/* routes. As of S1-8 app.main REFUSES the mount when RENDER is
# set regardless of this flag, so the guarantee no longer rests on this line alone.
if not os.environ.get("RENDER"):
    os.environ.setdefault("WINGMAN_ENABLE_OPS", "1")

    # The ops routes fail CLOSED without WINGMAN_OPS_TOKEN (S1-8), which would make
    # `python server.py` useless out of the box. .env is loaded by app.config, not yet — so
    # read it here before deciding to mint one, or every boot would generate a token that
    # overrides the operator's configured one.
    if not os.environ.get("WINGMAN_OPS_TOKEN"):
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) as fh:
                for line in fh:
                    key, _, value = line.partition("=")
                    if key.strip() == "WINGMAN_OPS_TOKEN" and value.strip():
                        os.environ["WINGMAN_OPS_TOKEN"] = value.strip().strip("'\"")
                        break
        except OSError:
            pass
    if not os.environ.get("WINGMAN_OPS_TOKEN"):
        _minted = secrets.token_urlsafe(24)
        os.environ["WINGMAN_OPS_TOKEN"] = _minted
        print("\n[server.py] No WINGMAN_OPS_TOKEN set — minted one for this run:\n"
              f"    {_minted}\n"
              "  Paste it when /admin asks. Put WINGMAN_OPS_TOKEN=<value> in .env to keep it\n"
              "  stable across restarts (.env is gitignored).\n")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    print("[server.py] The web layer moved to app.main (FastAPI). Booting uvicorn — this "
          "shim keeps `python server.py` working. See docs/archive/PLAN_1_decompose.md.")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_level="info")
