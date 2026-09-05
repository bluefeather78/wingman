#!/usr/bin/env python3
"""Dumps the current Supabase `opportunities` table back to opportunities.json,
as a human-diffable snapshot for backup/review — run manually after editing the DB,
before committing. Supabase itself is the runtime source of truth (see server.py's
/api/opportunities); this file is not fetched at runtime.

Exports EVERY column (`select=*`), not the 12-field shape the frontend once fetched.
Two reasons the widening matters: (1) a before/after snapshot is only as diffable as
the columns it carries — a agents/refresh_opportunities.py pass writes eligibility / grade /
cost / contact_email / category, none of which the old 12-field export captured, so a
diff could not see the fields that changed most; (2) `*` auto-includes any column added
to the table later, so this never silently drops a new field again. The cost is a noisier
git diff: agent-managed columns that move on every pass (updated_at, link_checked_at,
dates_last_checked_at, important_dates, review_*) now appear in the snapshot too.

USAGE:
    python -m agents.export_json
"""
import json
import os

from wingman.supabase_common import load_dotenv, supabase_get
from wingman import REPO_ROOT   # the repo root, defined once (see wingman/__init__.py)

OUT_PATH = os.path.join(REPO_ROOT, "data", "opportunities.json")
FIELDS = "*"


def main():
    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon_key:
        raise SystemExit("[ERROR] SUPABASE_URL / SUPABASE_ANON_KEY not set in .env.")

    # Paginated (via supabase_common.supabase_get) — a single unpaginated request
    # silently truncates at PostgREST's 1000-row default cap, which undercounted
    # this snapshot (1207 active rows) until this fix.
    data = supabase_get(supabase_url, "opportunities",
                         {"select": FIELDS, "is_active": "eq.true", "order": "id"}, anon_key)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[OK] Wrote {len(data)} opportunities to {OUT_PATH}")


if __name__ == "__main__":
    main()
