# Wingman offline data pipeline — static review of the repo-root Python layer

Scope: the 69 `*.py` files at `C:\Users\shama\Documents\wingman\` (not `app/`, not `ops/`), read statically on 2026-09-02. Nothing was run or modified. Line numbers are from the files as read; `scrape_opportunities.py` is 1556 lines, `mine_hub_pages.py` 1007, `check_deadlines.py` 1429, `check_links.py` 766, `generate_action_items.py` 674, `url_repair.py` 404, `url_dedupe.py` 387.

---

## 1. Import graph and dead code

### 1.1 The graph (root modules, plus what `app/`, `ops/`, `tests/` pull from the root)

Built with `ast` over every `*.py` in root, `app/`, `ops/`, `tests/`, `eval/`. Reverse-import counts (who imports each root module):

| Module | Importers | Role |
|---|---|---|
| `supabase_common` | 40 (37 root, `ops/core`, 1 test) | the Supabase seam for the offline layer |
| `gemini_common` | 24 (18 root, `app/core`, `app/routes/ai`, `app/routes/matching`, `app/services/embeddings`, 4 tests) | Gemini calls + embeddings + JSON + cost |
| `url_dedupe` | 19 (incl. `app/services/resume`, `ops/core`) | URL/name matching |
| `url_validate` | 16 | liveness, grounding resolve, offsite test |
| `page_text` | 15 (incl. `app/services/eligibility`) | fetch + html→text + claim verifiers |
| `agent_common` | 14 (incl. `ops/core`) | CLI flags, preview, snapshot stamp |
| `scrape_opportunities` | 14 (7 root scripts import its functions, 7 tests) | **de-facto library**, not just an entry point |
| `url_repair` | 12 | title proof, repair, primary-link |
| `discovered_leads` | 11 (incl. `ops/core`, `db_health_check`) | lead JSONL queue |
| `embed_common`, `claude_common` | 10 each | |
| `dedupe_embed_store` | 9 (incl. `ops/core`) | |
| `classify_page` | 8 | |
| `aggregators_common`, `queue_flags` | 7 each | |
| `mailing_list_common`, `subscription_common` | 6 each (`app/`, `ops/`) | |
| `combined_reader`, `dedupe_confidence`, `mine_hub_pages` | 5 each | |
| `check_deadlines` | 3 (`app/routes/opportunities`, `generate_action_items`, 1 test) | agent that is also a library for the app |
| `generate_action_items` | 3 (`app/routes/opportunities`, `app/services/action_items`, 1 test) | same |
| `dryrun_common` | 3 (`ops/admin`, `ops/core`, 1 test) | |
| `db_health_check`, `find_catalog_dups` | 2 each (`ops/core` + test) | |

**Import cycles (all "work" only because one side imports lazily inside a function):**

1. `scrape_opportunities` → `harvest_names` (top-level, line 66-79 region: no — `harvest_names` is imported lazily at `scrape_opportunities.py:834` inside `resolve_missing_url`) and `harvest_names` → `scrape_opportunities` (top-level, `harvest_names.py:60`).
2. `scrape_opportunities` → `discovered_leads` (top-level) → `mine_hub_pages` (lazy, `discovered_leads.py:146/198/225`) → `scrape_opportunities` (top-level, `mine_hub_pages.py:61`).
3. `refresh_opportunities` → `dedupe_embed_store` (lazy, `refresh_opportunities.py:176`) → `combined_reader` (lazy, `dedupe_embed_store.py:378`) → `refresh_opportunities` (top-level, `combined_reader.py:191`).
4. `walk_up_hubs` → `mine_hub_pages` → `scrape_opportunities` → `discovered_leads` → (lazy) `mine_hub_pages`.

**Layering violation:** `dedupe_embed_store.py:353` does `from app.services.embeddings import should_recompute_embedding`. The offline root layer now imports from the shipped `app/` package (which imports FastAPI at package import time via `app/config`?). `dedupe_queue.py:421-423` explicitly documents the opposite rule ("ops/ imports repo-root modules, never the other way around"). `refresh_opportunities.py:175` and `ops/core.py:2299` also import `app.services.embeddings`. This means `scrape_opportunities.py` (via `dedupe_embed_store`) and `refresh_opportunities.py` now transitively depend on `app/` being importable — the "stdlib-only agents at repo root" claim in CLAUDE.md is no longer strictly true for those two.

**Latent import bug:** `scrape_opportunities.py:64` imports only `urllib.error`, yet lines 601 and 972 call `urllib.parse.urlsplit`. It works today only because `supabase_common`/`url_validate` import `urllib.parse` first and populate the package attribute. Any reordering of imports would raise `AttributeError` mid-run, after money has been spent.

### 1.2 Classification of every root script

Legend: **(a)** shared library, **(b)** agent/CLI entry point wired into `ops/core.py` (`AGENT_CONFIGS_SCHEMA` lines 89-180 or `MAINTENANCE_TOOLS` lines 3632-3850) or the console, **(c)** one-off migration/backfill, **(d)** evaluation/grading tool, **(e)** orphan.

| File | Class | Referenced by | Verdict |
|---|---|---|---|
| `agent_common.py` | a | 14 importers | keep |
| `aggregators_common.py` | a | 7 | keep |
| `backfill_match_vectors.py` | c (re-runnable) | `ops/core` MAINTENANCE? no — referenced in `dedupe_embed_store`, `logic_map.html`, `../plans/RECALL_GRID_MERGE_PLAN.md`, `ops/core.py` (text), test | keep (still the match_vector backfill), move to `scripts/backfill/` |
| `backfill_seed_attribution.py` | c | test + `../archive/SCRAPER_IMPROVEMENT_PLAN.md` | one-off done 2026-08-26; move to `scripts/one-off/` |
| `backfill_subject_tags.py` | c (paid, Gemini) | CLAUDE.md only | done; move to `scripts/one-off/` |
| `backfill_summaries_from_xlsx.py` | c | `merge_description_into_summary.py` comment only | done; depends on `Opportunities.xlsx`; move to `scripts/one-off/` |
| `build_catalog_embeddings.py` | b (console tool `embedindex`) | `ops/core` | keep |
| `build_fixture.py` | d | `../archive/SCRAPER_IMPROVEMENT_PLAN.md` | keep under `eval/` or `scripts/grading/` |
| `build_legal.py` | b (console tool `legal`) | `ops/core`, test | keep |
| `check_deadlines.py` | b + a | `ops/core`, `app/routes/opportunities`, `generate_action_items` | keep |
| `check_links.py` | b | `ops/core` | keep |
| `check_opp_data.py` | b (console tool `inspect`) | `ops/core` | keep |
| `check_refresh_progress.py` | b (console tool `refreshprog`) | `ops/core` | keep |
| `check_reviews.py` | b | `ops/core` | keep |
| `classify_page.py` | a | 8 | keep |
| `classify_queue.py` | b (console tool `classifyqueue`) | `ops/core` | keep |
| `claude_common.py` | a | 10 | keep (docstring in `gemini_common.py:8-9` claiming it is "unused" is stale) |
| `clear_deadline_cache.py` | b-ish (CLAUDE.md documents it; console has its own endpoint) | `ops/admin`, `ops/core`, `check_opp_data` | keep |
| `combined_reader.py` | a | 5 | keep |
| `contact_email_common.py` | a | 4 | keep |
| `db_health_check.py` | a + b (console Health tab) | `ops/core` | keep |
| `dedupe_confidence.py` | a | 5 | keep |
| `dedupe_embed_store.py` | a | 9 | keep |
| `dedupe_eval.py` | d (paid) | test, `../archive/SCRAPER_IMPROVEMENT_PLAN.md` | move to `eval/` |
| `dedupe_queue.py` | b (console tool `dedupequeue`) | `ops/core` | keep |
| `dev_test_account.py` | dev utility | `../plans/DEADLINE_AND_TASK_PLAN.md` | keep, move to `scripts/dev/` |
| `discovered_leads.py` | a + CLI | 11 | keep |
| `dryrun_common.py` | a | `ops/*` | keep |
| `embed_common.py` | a | 10 | keep |
| `export_json.py` | b (console tool `export`) | `ops/core` | keep |
| `find_catalog_dups.py` | a + b (console Duplicates scan) | `ops/core` | keep |
| `find_contact_emails.py` | b (console tool `contactemail`) | `ops/core` | keep |
| `find_mailing_lists.py` | b | `ops/core` | keep |
| `gemini_common.py` | a | 24 | keep |
| `generate_action_items.py` | b + a | `ops/core`, `app/` | keep |
| `grade_mailing_lists.py` | d (console tool `mlgrader`) | `ops/core` | keep |
| `grade_scraper_batch.py` | d | test, `build_fixture`, console HTML text | move to `eval/` |
| `grade_url_truth.py` | d | **nothing** (0 references anywhere) | **orphan** — delete or move to `eval/` |
| `harvest_names.py` | b (console tool `harvestnames`) + a | `ops/core`, `scrape_opportunities` | keep |
| `mailing_list_common.py` | a | 6 | keep |
| `matching_eval.py` | d | test, `../plans/RECALL_GRID_MERGE_PLAN.md` | move to `eval/` |
| `merge_description_into_summary.py` | c | only the other merge script's comment | done; move to `scripts/one-off/` |
| `merge_opens_date_into_important_dates.py` | c | `check_opp_data` comment | done; move to `scripts/one-off/` |
| `migrate_seeds_to_supabase.py` | c | plans, console HTML | done; move |
| `migrate_to_supabase.py` | c | comments in 8 files (as history) | done; move |
| `migrate_users_to_supabase.py` | c | CLAUDE.md, `app/config` comment | done; move |
| `mine_hub_pages.py` | b (console tool `minehub`) + a | `ops/core`, 5 importers | keep |
| `page_text.py` | a | 15 | keep |
| `propose_angles.py` | b (console tool `proposeangles`) | `ops/core` | keep |
| `query_telemetry.py` | a | `ops/core` | keep |
| `queue_flags.py` | a | 7 | keep |
| `refind_dead_links.py` | b (console tool `refind`) | `ops/core` | keep |
| `refresh_opportunities.py` | b + a | `ops/core`, `combined_reader` | keep |
| `repair_survivor_names.py` | c | **only itself** | one-off (2026-08-26 pair resolutions, reads a fixture file); **orphan** — move to `scripts/one-off/` |
| `scrape_opportunities.py` | b + a | `ops/core`, 7 root importers | keep; its library half (`build_row`, `insert_rows`, `next_id_generator`, `research_seed`, `collapse_intra_run_twins`, `gate_dup_candidates`, FLAG_*) should be split into a `scrape_common.py` so `harvest_names`/`mine_hub_pages`/`refind_dead_links` stop importing a 1556-line entry point with two prompts in it |
| `seed_ledger.py` | a | `ops/core`, scraper | keep |
| `seeds_common.py` | a | scraper | keep |
| `send_lifecycle_emails.py` | b (local runner) | CLAUDE.md | keep |
| `server.py` | b (dev shim) | | keep |
| `sitemap_common.py` | a | 4 | keep |
| `sitemap_hub.py` | a | `mine_hub_pages` | keep |
| `source_capture.py` | a | `check_deadlines` | keep |
| `subscription_common.py` | a | `app/`, `ops/` | keep |
| `supabase_common.py` | a | 40 | keep |
| `triage_queue.py` | b (console tool `triagequeue`) | `ops/core` | keep |
| `url_dedupe.py` | a | 19 | keep |
| `url_repair.py` | a | 12 | keep |
| `url_validate.py` | a | 16 | keep |
| `walk_up_hubs.py` | b (CLI, not in console) + a | `classify_queue`, `mine_hub_pages`, `discovered_leads`, `logic_map.html` | keep |

Orphans (imported by nothing, referenced by nothing outside their own file): **`grade_url_truth.py`**, **`repair_survivor_names.py`**. Dead-weight one-offs still at root: 9 (`backfill_*` ×4, `merge_*` ×2, `migrate_*` ×3).

### 1.3 Tracked non-code at the repo root

| File | Size | Referenced by | Verdict |
|---|---|---|---|
| `server_debug.txt` | 122 B | nothing | delete |
| `server_output.txt` | 122 B | nothing | delete |
| `server_full_output.log` | 0 B | nothing | delete |
| `server_stderr.log` | 0 B | nothing | delete |
| `refresh_run.log` | 18 KB | nothing; **listed in `.gitignore` yet tracked** | `git rm --cached` |
| `find_contact_emails_full_run.log` | 103 KB | nothing | delete |
| `review_check_dry_run_20260818.json` / `..._20260819.json` | 4 KB / 232 KB | nothing; matches `*_dry_run_*.json` in `.gitignore` yet tracked | `git rm --cached` (they are still committable via the console, but they are 2 weeks stale: `dryrun_common.STALE_DAYS=7`) |
| `scrape_review_national_20260818.json`, `...run1-backup.json`, `..._20260820.json`, `scrape_review_seattle_20260818.json` | 1.5 KB–141 KB | `_20260820` cited in `url_validate.py:7` and `../archive/SCRAPER_PLAN.md` as a measurement; the others by nothing; all match ignored glob `scrape_review_*.json` yet tracked | `git rm --cached`; keep `_20260820` under `tests/fixtures/` if the measurement matters |
| `test_resume.docx` | 37 KB | nothing (no test imports it; `tests/unit/test_resume_multipart.py` builds its own) | delete or move to `tests/fixtures/` |
| `Opportunities.xlsx` | 401 KB | `backfill_summaries_from_xlsx.py` only | move with that script |
| `.tmp_landing_zip/` (17 tracked files, design-canvas export) | — | nothing | delete from tracking |
| `test_server_debug.sh` | 512 B | nothing | delete |
| `hub_pilot_national.json`, `hubs_seattle.json` | <1 KB | `discovered_leads.py`, `harvest_names.py` docstrings; superseded by `discovered_leads.jsonl` | keep as sample input or move to `eval/` |
| `../archive/duplicate_cleanup_2026-08-28.md` | 6 KB | nothing | move to a `notes/` dir |
| `opportunities.json` | 1.2 MB | 10 files (documented backup snapshot) | keep |
| `logic_map.html` | 55 KB | `ops/` serves it | keep |
| `agent_settings.json`, `discovered_leads.jsonl` (208 KB), `agent_logs/` (123 files), 33 untracked `*_review_*/*_dry_run_*` snapshots | — | local state (see §5) | not tracked, correct |

`.gitignore` also contains a literal `.gitignore` entry (harmless but wrong).

---

## 2. Duplication

### 2.1 `normalize_url` — CLAUDE.md is stale, and the copies that remain disagree with what the scraper actually uses

- `dryrun_common.py:83-85`: `(url or "").strip().rstrip("/").lower()`, docstring: "Same normalization scrape_opportunities.py dedupes with — kept identical on purpose."
- `migrate_to_supabase.py:69-70`: identical body.
- **`scrape_opportunities.py` no longer defines `normalize_url` at all** (grep: zero hits). Its dedupe is `url_dedupe.find_duplicates` → `match_key()` (`url_dedupe.py:212-223`), which strips scheme, `www.`, default port, fragment, trailing slash, `index.html`-style filenames, tracking params, sorts remaining query params, and lowercases host+path.

Concrete drift: `https://www.Example.edu/Program/index.html?utm_source=x` → scraper key `example.edu/program`; `dryrun_common.normalize_url` → `https://www.example.edu/program/index.html?utm_source=x`. So **`ops/core._existing_opportunity_urls()` (line 1502-1516) and `dryrun_common.commit_snapshot()` (line 291-320) dedupe a committed scraper snapshot with a weaker key than the scraper itself used**, and a snapshot re-committed after a `www.`/scheme change, or one whose stored URL carries a tracking param, inserts a duplicate. The user-submission path (`app/services/resume.py`) uses `url_dedupe`, so it agrees with the scraper, not with the commit path.

### 2.2 Supabase request builders — 8 independent copies

`urllib.request.Request(..., headers={"apikey": ...})` is built in: `supabase_common.py` (4 functions), `app/core.py` (3 sites), `app/services/deadlines.py` (4), `app/services/action_items.py` (2), `app/services/opportunities.py` (1), `db_health_check.py:131` (a count-only request with `Range: 0-0`, `Prefer: count=exact`), `migrate_to_supabase.py:170`, `migrate_users_to_supabase.py:91`. `ops/core._commit_patch/_commit_insert` (1478-1499) build their own too via `app.core._supabase_headers()`.

Drift: timeouts differ (30 s in `supabase_common`, 10 s in `app/services/opportunities`, 20/60 s in `ops/core._commit_*`); `app/core._supabase_request` swallows errors and returns `None` where `supabase_common` raises; the commit path uses `limit/offset` pagination while `supabase_common` uses `Range` headers.

### 2.3 Pagination-past-1000 loops — 5 copies, two mechanisms

`supabase_common.supabase_get` (Range), `app/services/opportunities._paginated_catalog_fetch:36-59` (Range), `app/services/email.py:551` (Range), `app/services/resume.catalog_dedupe_rows:262-280` (limit/offset), `ops/core._existing_opportunity_urls:1502-1516` (limit/offset). Every one of them terminates on `len(page) < page_size`.

**Shared latent bug:** none of these pass an `ORDER BY` unless the caller does, and PostgREST offset pagination over an unordered query is not stable in Postgres — a heap row updated mid-scan (which the scraper's own `apply_merge` PATCH does, mid-run, on the same table it is paging) can be returned twice or skipped. Of the 45 `supabase_get(... "opportunities" ...)` call sites at the root, roughly 39 pass no `order` (the exceptions: `check_reviews` main, `check_links.select_rows`, `generate_action_items` main, `walk_up_hubs.fetch_trusted_rows`, `discovered_leads.fetch_rejected_rows`, `seeds_common`). The scraper's dedupe set (`scrape_opportunities.py:1096`) and the hub miner's (`mine_hub_pages.py:691`) are among the unordered ones.

### 2.4 Cost estimation — 4 definitions, 2 pricing tables for the same embedding model

- `gemini_common.estimate_cost:497` and `claude_common.estimate_cost:169` (different providers — legitimate).
- `gemini_common.estimate_embed_cost:538` (`usage["input_tokens"] * 0.15/1M`, tokens approximated in `call_gemini_embed` as `len(t)//4`) vs `embed_common.estimate_embed_cost:60` (`approx_tokens(text) * 0.15/1M` with `MAX_EMBED_CHARS=14_000` truncation). Same price today, two constants (`EMBED_INPUT_PRICE_PER_TOKEN` vs `EMBED_PRICE_PER_TOKEN`), two token approximations.

### 2.5 Model-call wrappers — three Anthropic POST builders, two Gemini embedding clients

**Anthropic:**
1. `claude_common.call_claude:116` — `web_search` only, `max_tokens` param, `max_uses` param, no caching, uses module rate-limiter.
2. `check_deadlines.call_claude:224` — adds `web_fetch_20250910` (`max_uses 5`, `max_content_tokens 4000`), `cache_control: ephemeral` on the system block, `return_captured/return_sources`, **fixed** `CLAUDE_MAX_TOKENS=1200` (no param), its own `_enforce_rate_limit`/`set_min_delay`/`set_default_timeout` (lines 175-199) duplicating `claude_common`'s.
3. `source_capture._capture_call:146` — imports `ANTHROPIC_URL`, `MODEL`, `_enforce_rate_limit`, `_default_timeout_secs` from `claude_common` but builds a third body (`max_tokens 600`, `MAX_SEARCH=2`, `MAX_FETCH=4`, `FETCH_MAX_CONTENT_TOKENS=6000`).

Drift already visible: `check_deadlines` caps a fetched page at 4000 tokens, `source_capture` at 6000; `check_deadlines` uses prompt caching, `source_capture` does not although `generate_action_items` calls it in a loop with a byte-identical system prompt.

**Gemini embeddings:** `gemini_common.call_gemini_embed:544` (batchEmbedContents, `EMBED_DIM=768`, batch 100, 429 retry, rate-limited) vs `embed_common.embed_text/embed_batch:664-690` (embedContent/batchEmbedContents, **no `outputDimensionality` → 3072-dim**, batch 50, 429 retry with no rate-limit call, own `_post`). The two produce vectors of different dimensionality for the two vector columns (`match_vector` 768 via `app/services/embeddings`, `dedupe_vector` 3072 via `dedupe_embed_store`). This is by design per `dedupe_embed_store.py:41-49`, but the design is enforced by nothing — it is two copies of the same HTTP code with one parameter silently different.

### 2.6 Rate limiter / retry — 3 copies

`_enforce_rate_limit` + `set_min_delay` + `set_default_timeout` + `_env_number` exist in `gemini_common.py:169-234`, `claude_common.py:50-101`, `check_deadlines.py:175-199`. The 429-retry-once block appears in `gemini_common.py:457-466` (with `_enforce_rate_limit` before retry), `gemini_common.py:582-590` (embed), `embed_common.py:156-161` (plain `time.sleep(5)`, no rate-limit call). `claude_common.call_claude` and `check_deadlines.call_claude` have **no 429 handling at all**.

### 2.7 HTML-to-text / title / user agent

- Tag strippers: `page_text.html_to_text:288` (the only chrome-aware one), `url_repair._text:108`, `contact_email_common._TAG_RE:98`, `mailing_list_common._TAG_RE:118`. Four regexes, one real implementation.
- Page fetchers: `page_text._fetch_urllib:165` (200-only, content-type check, `PAGE_BYTES=600_000`), `url_repair._fetch:93` (`<400`, `PAGE_BYTES=400_000`), `url_validate.check_url:235` (GET, status classification), `mailing_list_common.fetch_page:74`, `sitemap_common.default_fetch:320` (`WingmanBot/1.0` UA, 5 MB cap). Five urllib fetchers with different UA, byte caps and success rules; `url_repair._fetch` accepts 3xx-resolved 2xx while `page_text` rejects anything but 200 — so the title-proof and the classifier can disagree about whether the same URL "loads".
- `USER_AGENT` literal duplicated in `url_validate.py:48` and `mailing_list_common.py:58`.
- `extract_json` duplicated verbatim in `gemini_common.py:602` and `claude_common.py:187` (documented as deliberate).
- `load_dotenv` ×4: `supabase_common:20`, `app/config:13`, `migrate_to_supabase:53`, `migrate_users_to_supabase:50`.
- Model-pin literals: `claude-haiku-4-5-20251001` in `claude_common.py:28`, `check_deadlines.py:147`, `generate_action_items.py:90` (+ `app/config`); `gemini-3.5-flash-lite` in `combined_reader.py:37`, `contact_email_common.py:47`, `backfill_subject_tags.py:35`, **inline at `refresh_opportunities.py:281`** (+ `app/config.MESSAGES_MODEL`). `combined_reader` says "keep in step with refresh" — nothing enforces it.

### 2.8 CLI plumbing

`agent_common.add_agent_args` (`--preview/--min-delay/--timeout`) is used by 9 scripts. Seven paid or semi-paid CLIs re-declare their own: `mine_hub_pages` (`--min-delay` as `int`, `--timeout` default 40, `--preview`), `harvest_names` (same plus `--max-searches`), `refind_dead_links` (`--min-delay`, `--timeout` 280, `--preview`; **no `--dry-run`**), `classify_queue` (`--preview`, no timing flags), `dedupe_queue` (`--preview`), `propose_angles` (`--preview`), `walk_up_hubs` (`--timeout`). None of the seven emit the `PREVIEW_JSON:` contract line, so `ops/core.preview_agent` cannot price them; the console runs them as "maintenance tools" with no cost estimate (`ops/core.py:3624-3631` says so).

### 2.9 Snapshot writing

Ten snapshot filename patterns are written: `scrape_review_<mode>_`, `hub_review_<mode>_`, `names_review_<mode>_`, `refresh_opportunities_dry_run_`, `review_check_dry_run_`, `deadline_check_dry_run_`, `find_contact_emails_dry_run_`, `mailing_list_dry_run_`, `action_items_dry_run_`, `link_check_dry_run_`. `dryrun_common.SNAPSHOT_SPECS` (lines 47-78) knows **five**. A paid `mine_hub_pages --dry-run` or `harvest_names --dry-run` writes a snapshot in the scraper's `{"inserted": [...], "rejected": [...]}` shape but under a glob the commit path does not match — so it cannot be committed and must be re-paid, which is exactly what `dryrun_common`'s docstring says the module exists to prevent. `action_items_dry_run_*` and `mailing_list_dry_run_*` likewise.

### 2.10 Same decision implemented twice (cross-reference for §3)

| Decision | Copy 1 | Copy 2 | Drift |
|---|---|---|---|
| "is this program discontinued → `status=not_running`" | `check_deadlines.verify_status_evidence:986` requires a verbatim quote found on a fetched page, else downgrades to `unknown` | `check_links.discontinued_phrase:175` regex over the row's own **summary** (model-written text, not the page) and writes `not_running` with no page evidence | different evidence bars for the same column; see §4.9 |
| direct-child containment | `mine_hub_pages.contained_children:566` | `sitemap_hub._drop_contained:122` (docstring: "mirrors … without importing it") | identical today; two copies |
| same-domain hub link filter / off-domain classifier | `discovered_leads.classify_page:217` | `discovered_leads.classify_confirmed_roundup:184` | second skips the title test on purpose |
| "which rows are the review queue" | `classify_queue._fetch_queue:61` (`not is_active and moderation_status in (None,"",pending_review)`) | `dedupe_queue.select_rows:446` (same) | `ops/core.list_pending_opportunities` also counts `approved`-but-inactive as queue (line 1612) — the two CLIs do **not** |
| flag prefix ownership | `check_links._OWNED_PREFIXES:152` strips its own flags | `queue_flags.upsert_flag:433` replaces one `classify:` entry | fine, but `check_links.merge_flags` will not strip a `classify:` flag and `upsert_flag` will not strip a dead-link flag — expected |
| candidate URL flags (bare/low-value/offsite/no-type) | `scrape_opportunities.py:1282-1298` | `mine_hub_pages.py:892-899` (deliberately omits `domain_matches_org`) | `harvest_names._row_flags:448` re-adds `domain_matches_org` — three sets |
| in-run twin collapse | `scrape_opportunities.collapse_intra_run_twins` | reused by `mine_hub_pages` | one copy, good |
| id minting | `scrape_opportunities.next_id_generator:496` | `migrate_to_supabase.next_id_generator:73` | identical; but see §4.2 |

---

## 3. Control flow of the discovery/verification pipeline

Node lists are numbered per flow. "→ N" means go to node N. Every model call names provider/model/search state.

### (i) `python scrape_opportunities.py --mode national` → rows in Supabase

```
S1  main() parse args (--mode, --dry-run, --seed-ids, --max-searches=10, --no-verify-urls,
    --no-resolve, --resolve-per-angle=12, --resolve-per-run=150, --gate-observe, --preview,
    --min-delay=5, --timeout=280). apply_timing(gemini=True) pushes delay/timeout into
    gemini_common.                                                        [line 1005-1052]
S2  load_dotenv(); require SUPABASE_URL/SERVICE_KEY/GEMINI_API_KEY else exit(1).
S3  seeds_common.load_seeds(mode) — GET scraper_seeds (enabled, ordered).
      if table empty/unreachable → fallback to NATIONAL_SEEDS/SEATTLE_SEEDS literals
      (id=None → no yield tracking).  select_seeds(--seed-ids | --seed-indices).
S4  if --preview → emit_preview(count seeds) → EXIT (no API, no writes).
S5  today = local date; run_stamp = local YYYYMMDD-HHMMSS; source = "scraper-<mode>-<YYYYMMDD>".
S6  existing = GET opportunities select=id,name,url — WHOLE table (active+inactive+rejected),
    paginated, NO order.  mint_id = next_id_generator(max ec-id + 1).       [1096-1098]
S7  gate_index = dedupe_embed_store.fetch_dedupe_index() — GET id,dedupe_vector for
    is_active=true, page_size=200.  HTTP 400 (column missing) → [] silently; any other
    error → [] with a WARN.  gate_by_id = {id: row} from existing.         [1105-1106]
S8  INSERT agent_runs {agent:"scraper", mode:"<mode>[-dryrun]", started_at}.  (a dry run
    is logged too.)                                                         [1113-1118]
S9  lead_keys = keys of discovered_leads.jsonl (local file).
S10 resolve_budget_per_seed = min(--resolve-per-angle, --resolve-per-run // len(seeds)).
S11 FOR EACH seed:                                                          [1145]
S12   research_seed(angle, addendum, today, key, args)                      [780-816]
        system = DISCOVERY_SYSTEM.format(today, angle) (+ SEATTLE_ADDENDUM in seattle mode)
        MODEL CALL A: Gemini gemini-3.6-flash, googleSearch ON, max_tokens 6000,
          thinking "low", soft max_searches folded into prompt, return_grounding=True.
          Acquires .gemini_web_search.lock (file, repo root) on first search call.
        cost += estimate_cost(usage) per attempt.
        searches = len(groundingMetadata.webSearchQueries)
        if searches == 0 and attempt == 1 → RETRY identical call once (MODEL CALL A').
        if still 0 → return with searches=0 (silent).
S13   total_cost += phase1_cost (banked). silent = (searches == 0) → silent_search_count++.
S14   resolved = url_validate.resolve_grounding_chunks(grounding) — free HTTP, one redirect
      hop per groundingChunk (12 threads).  spans = support_urls_by_span(...).
S15   extract_candidates(notes, resolved_urls)                              [852-864]
        MODEL CALL B: Gemini gemini-3.6-flash, search OFF, EXTRACT_SYSTEM (strict JSON,
        running/running_reason, url must be copied from the SOURCE PAGES list).
        extract_json → list of candidate dicts.  cost banked AFTER parse (see §4.3).
S16   write agent_logs/scraper_<stamp>_seed<id>.json (notes, queries, resolved_urls,
      candidates) — local file.
S17   FOR EACH candidate (staging pass):                                     [1203]
S18     not a dict → rejected("not a JSON object") → next candidate.
S19     no name → rejected("no name") → next.
S20     queue_flags.is_not_running(candidate.running) i.e. running is False (JSON false only)
          → not_running_skipped++, rejected(not_running_reason) → next.     [1218]
S21     span_urls = spans_for_name(name, spans) (all name words in span text).
S22     url, flags = reconcile_url(model_url, resolved_urls, span_urls)     [537-582]
          if span_urls: model_url in span_urls → keep;  model_url in resolved → keep;
            same host as a span → span_urls[0] + FLAG_URL_REPLACED; no model_url → span[0];
            else span_urls[0] + FLAG_URL_REPLACED.
          elif no model_url → "" ;  model_url in resolved → keep;
          same-host retrieved page exists → that page + FLAG_URL_REPLACED;
          else model_url + FLAG_URL_UNSOURCED   (← a model-typed URL survives here)
S23     if url == "" (name only) — STAGE 1b:                                 [1225-1257]
          can_resolve = not --no-resolve and not --no-verify-urls and
                        run_count < --resolve-per-run and seed_count < budget_per_seed
          if can_resolve: resolve_missing_url(name, org, existing)          [819-849]
             free gate: harvest_names.name_is_resolvable (>=2 identity words) else skip
             free gate: harvest_names.is_known_name(existing) (exact identity set) else skip
             MODEL CALL C: Gemini gemini-3.6-flash, googleSearch ON, RESOLVE_SYSTEM,
               max_searches=1, retry-once-on-silent (research_seed again).
             url = harvest_names.best_resolved_url(resolved, name, org) — fetches up to 3
               grounding pages, requires url_repair.title_proves; bare domain accepted last.
             if found → url + FLAG_URL_RESOLVED.
          if still no url → names_dropped++, rejected("name found but no own-page URL") → next.
S24     if not --no-verify-urls: resolve_url_truth(candidate, url, flags, resolved_urls)
                                                                             [653-698]
          (1) is_content_mill(url) or not domain_matches_org(url, org, name):
                _rescue_offsite → (a) _first_proven_sibling among resolved_urls on org
                domain (≤3 fetches, title_proof_url) else (b) fetch the offsite page,
                url_repair.extract_primary_link (≤5 fetches, domain+title gates).
                rescued → url:=rescued + FLAG_URL_RESCUED; else keep url (flag phase
                adds FLAG_OFFSITE later).  → S25
          (2) is_low_value_path(url) → trade up to a proven canonical sibling if any.
          (3) title_proof_url(url): False → proven sibling if any else FLAG_TITLE_UNPROVEN;
              None (blocked/<2 identity words) → no flag.
S25     staged.append((candidate, url, flags)).
S26   checks = url_validate.check_urls(all staged urls) — concurrent GET, 12 threads,
      LIVE / DEAD(404,410,malformed,NXDOMAIN) / UNVERIFIED(403,429,TLS,timeout).
S27   FOR EACH staged (candidate, url, flags):                               [1286]
S28     flags += url_flags(check): DEAD→FLAG_DEAD_LINK; UNVERIFIED→FLAG_BLOCKED_LINK(code)
        or FLAG_UNREACHABLE.  + FLAG_BARE_DOMAIN, FLAG_LOW_VALUE, FLAG_OFFSITE (mill or
        !domain_matches_org), FLAG_NOT_SEARCHED if silent.
S29     exact, dup_candidates = url_dedupe.find_duplicates(url, name, existing,
        include_weak=False)                                                  [url_dedupe 290]
          exact := same match_key AND (name normalized-equal OR similarity ≥ 0.82) AND
                   neither name is a bare institution.
          candidates: same key/different name (strong), apply-url match (never fires —
          existing has no apply_url), same-domain prefix (strong), same-domain name ≥0.82
          (strong); weak tier dropped.
S30     action, target = classify_same_url(url, exact, dup_candidates)      [136-158]
          same_url = exact or a "identical URL" strong candidate
          same_url and not is_bare_domain(url) → "merge"
          exact                                  → "reject"
          same_url (bare domain, name differs)   → "flag"
          else                                   → "insert"
S31     "merge": duplicates_skipped++; if --dry-run or --no-verify-urls → record only;
          else apply_merge(): GET survivor, fetch page, merge_row() (name only if incumbent
          has <2 identity words AND title proves candidate's; fill empty org/summary/
          eligibility/grade/tags/email), PATCH survivor incl. ACTIVE rows, append
          "merged <day>: …" to its quality_flags, bump updated_at  → next candidate.
        "reject": rejected("exact duplicate of <id>") → next.
        "flag": flags += FLAG_SHARES_HOMEPAGE(id) → continue.
S32     row = build_row(candidate, next(mint_id), source, url, flags) — type outside
        VALID_TYPES parked as "Program"; is_active False; grade ints only; None → rejected.
S33     DISCOVERY GATE (MARQUEE M9): combined_reader.read_candidate_live(url, key,
        name_hint, org_hint, index=gate_index)                               [1344-1393]
          page_text.fetch_page_text_resolved(url) (plain HTTP, no browser).
          no text → route UNREADABLE, cost 0.
          MODEL CALL D: Gemini gemini-3.6-flash, search OFF, CLASSIFY_SYSTEM, max_tokens
            800 → class program|first_party_hub|third_party_hub|none; evidence quote must
            be on page else confidence:=low; stale := latest year on page ≤ today-3 (regex).
          route: program+stale→DROP_STALE; program→ROW; hubs→*_LEAD; none→FLAG_NONE.
          if ROW: MODEL CALL E: Gemini gemini-3.5-flash-lite, search OFF,
            refresh_opportunities.build_system (metadata JSON) → clean_update_dict.
          if ROW and gate_index: MODEL CALL F: Gemini embedding (embed_common.embed_text,
            gemini-embedding-001, 3072-dim) → nearest ≥0.93 top-3 → dup hints.
          any exception → gate=None, row inserted ungated (WARN).
S34     if gate: cost banked; gate_flag = classification.flag() ("classify: …").
          is_hub → captured_leads.append(hub lead, scope same/off-domain) [free, both modes]
          if not --gate-observe:
            route DROP_STALE → rejected("discovery gate: stale program") → next.
            is_hub → rejected("discovery gate: <class> -> fed to hub-mining queue") → next.
          route ROW → gate_metadata_overlay(row, metadata) (page truth overwrites phase-2
            fields except id/url/source/is_active/seed_id).
          dup_candidates = gate_dup_candidates(hints, gate_by_id, dup_candidates).
S35     if not --no-verify-urls: contact_email_common.resolve_contact_email(row) — regex
        scan of page(s); MODEL CALL G (Gemini gemini-3.5-flash-lite, search OFF, max 200
        tokens) ONLY when >1 candidate address.  Any exception swallowed.
S36     row.seed_id = seed.id; row_flags = flags ⊕ upsert classify flag; FLAG_NO_TYPE if
        candidate.type invalid and gate gave none.
S37     review_by_id[row.id] = {moderation_status:"pending_review", dup_candidates,
        quality_flags}; existing.append({id,name,url}); inserted_rows.append(row).
S38   (per-seed try/except: ANY exception in S12-S37 → errors++, print, continue to S39 —
      partial seed work already in inserted_rows is kept.)
S39   Phase 4F lead capture (free): discovered_leads.capture(resolved_urls, seed_used,
      existing, seed_id, angle, known_keys) — for each resolved page not used/known/
      ignorable, fetch and classify_page(): title must match _MANY_RE; ≥6 distinct
      off-domain registrable domains → KIND_HUB; ≥2000 chars + HS audience → KIND_NAMES.
      Exceptions swallowed.
S40   seeds_common.record_seed_result(seed, found, added(0 if dry-run), dupes, cost):
      re-GET seed row, PATCH total_* += n (read-modify-write, one round trip). WRITES ON
      DRY RUN. Fallback seeds (id None) → no-op.
S41 END loop.  collapse_intra_run_twins(inserted_rows) — same registrable domain and
    name_similarity ≥0.9 → keep the copy with better _url_rank; loser → rejected.
S42 discovered_leads.append_leads(captured_leads) → appends to discovered_leads.jsonl
    (WRITES ON DRY RUN; local file).
S43 write scrape_review_<mode>_<stamp>.json {"inserted","rejected","merged","leads"} (local).
S44 if --dry-run → skip insert.  else if inserted_rows: insert_rows()          [875-904]
      POST batches of 500 with ladder: full (row+review+attribution) → no-attribution →
      no-review → minimal.  ANY exception at a tier falls to the next tier; only the last
      tier's exception propagates (see §4.4).
S45 if not --dry-run: auto_disable_mined_seeds(seeds) — GET scraper_seeds + opportunities
    (seed_id in ids), seed_ledger.build_seed_funnels → diagnose; mined_out/thin with
    ≥10 found, ≥2 runs, ≥5 adjudicated → PATCH is_enabled=false, disabled_reason.
S46 PATCH agent_runs {finished_at, items_processed=len(seeds), items_added, errors,
    cost_usd, total_web_searches, silent_search_count, notes}.
S47 print [DONE].  (No unlock step: .gemini_web_search.lock removed by atexit.)
```

Writes to Supabase in a live run: S8, S31 (PATCH active rows), S40 (PATCH scraper_seeds), S44 (INSERT), S45 (PATCH seeds), S46. In a `--dry-run`: S8, S40, S46 still write; S42/S43 write local files.

### (ii) Hub mining: `mine_hub_pages.py` + `discovered_leads.py` + `walk_up_hubs.py` + `classify_page.py`

**Lead sources (free) feeding `discovered_leads.jsonl`:**

```
L1  scrape_opportunities S39 (router: classify_page in discovered_leads) → kind hub
    (scope off-domain default) or names.
L2  scrape_opportunities S34 (discovery gate hub verdict) → kind hub, scope from class.
L3  walk_up_hubs.py --commit: fetch_trusted_rows (is_active=true, id,name,org,url) →
    group_by_parent (parent_url = one dir up; None for bare/one-segment; drop mills and
    ignorable) → rank by #rows under parent → fetch each parent (12 threads) →
    verify_index: parent must LINK a child we walked up from, offer ≥3 other program links
    (filter_hub_links same-domain, cap 400) → lead {kind hub, scope same-domain}.
L4  classify_queue.py (--no-feed absent): any queue row classified *hub → lead.
L5  ops console: rejecting a row with reason "third-party-roundup" → _queue_roundup_leads_
    async → discovered_leads.from_rejected_rows(confirmed=True) → classify_confirmed_roundup
    (≥6 off-domain domains → hub else names).
L6  discovered_leads.py --from-rejects [--any-reason] --commit: same, batch.
```

**Mining a lead: `python mine_hub_pages.py --from-leads N | --hubs URL | --hubs-file`**

```
H1  parse args (--from-leads N=5, --off-domain, --preview, --give-up-after 6, --max-pages,
    --mode, --min-delay 5 (int), --timeout 40, --dry-run). safe_console().
H2  hubs = hubs-file entries + --hubs (off_domain flag) + pending(KIND_HUB, N) leads via
    hubs_from_leads (scope on the lead decides direction; missing scope → off-domain).
H3  existing = GET opportunities id,name,url (whole table; NO order) — using
    SUPABASE_SERVICE_KEY **or SUPABASE_ANON_KEY** (anon → RLS → active rows only, see §4.13).
    catalog_keys = {match_key(url)}; catalog_paths = catalog_paths_by_host(existing).
H4  if --preview:
      same-domain hub → sitemap_hub.program_candidates(hub, classify=None): enumerate
        (robots.txt/sitemap ∪ anchor links, same registrable domain) → scope_to_hub path
        prefix → print "to-classify" list.  FREE.
      off-domain hub → discover(hub, off_domain=True) (H7) → contained_children →
        fresh_candidates → print.  FREE.  → EXIT.
H5  require GEMINI_API_KEY.
H6  SELECT per hub:
      same-domain: MODEL CALL H1: sitemap_hub.make_gemini_classifier — Gemini
        gemini-3.6-flash, search OFF, CLASSIFY_SYSTEM (paths in → program home paths out),
        chunks of 150 paths, max_tokens 2000. Then _drop_contained.
        if it returns [] → fall back to discover(hub, off_domain=False) + contained_children.
      off-domain: discover(hub, off_domain=True) + contained_children.
H7  discover(hub, off_domain):                                              [397-480]
      fetch_html(hub) (url_repair._fetch) → harvest_links → filter_hub_links (drop wrong-
      audience anchors, bare domains, mills, is_nonprogram_link unless sub-hub anchor;
      keep same-domain XOR off-domain; sort hub-path-prefixed first; cap 25, report
      over_cap) → recurse ONE level into ≤3 sub-hubs → dedupe by match_key →
      STAGE 2: page_text.fetch_page_text_resolved each candidate; drop if text lacks
      HS-audience words (and anchor lacks them); drop if it redirects to the hub or above;
      drop same landing page twice.
H8  contained_children(urls, catalog_paths) again (drop direct child of a catalogued page).
H9  fresh_candidates(urls, catalog_keys, seen_this_run) — URL-ONLY exact-key dedupe
    against the whole catalog + this run.  all_new.append((hub, fresh)).
H10 if --max-pages: allocate_budget round-robin across hubs; capped hubs recorded.
H11 mint = next_id_generator(existing ids); INSERT agent_runs {agent:"hub_miner",
    mode:"hub[-dryrun]"}.
H12 gate_index = fetch_dedupe_index() (as S7). cost = select_cost (banked).
H13 FOR EACH hub, FOR EACH fresh url:
H14   if refused_in_a_row ≥ --give-up-after → SKIP rest of hub.
H15   extract_opportunity(url, key, index, timeout, min_delay)              [327-394]
        set_min_delay (per call!). page_text.fetch_page_text(url) → no text → (None, 0).
        MODEL CALL H2: Gemini gemini-3.6-flash, search OFF, _EXTRACT_SYSTEM (refusal +
          state + running/running_reason), max_tokens 1500. extract_json; name null →
          refusal (cost banked, returns None).
        MODEL CALL H3: classify_page.classify_from_text (CLASSIFY_SYSTEM, max 800) —
          advisory only.
        MODEL CALL H4: combined_reader.extract_metadata (gemini-3.5-flash-lite,
          refresh's build_system) → overlay onto cand (cost→cost_detail).
        MODEL CALL H5 (if index): embed_common.embed_text(default_representation) →
          dedup_hint ≥0.93.
        any exception → errors++, continue (cost of the failed call LOST — see §4.3).
H16   cand None → refused_in_a_row++ → next.
H17   is_not_running(cand.running) → rejected snapshot entry, refused_in_a_row++ → next.
H18   row = build_row(cand, next(mint), "hub-<domain>-<date>", url, []) ; None → refused++.
      refused_in_a_row = 0; row.found_via = hub.
H19   flags: FLAG_BARE_DOMAIN, FLAG_OFFSITE only if is_content_mill (NOT domain_matches_org),
      FLAG_LOW_VALUE, FLAG_NO_TYPE; upsert classify flag.
H20   _exact, dup_cands = url_dedupe.find_duplicates(url, name, existing, include_weak=False)
      — exact is NOT a reject here (should not occur after H9); it becomes a strong hint.
      dup_cands = gate_dup_candidates(dup_hints, gate_by_id, dup_cands).
H21   review_by_id[id] = {pending_review, dup_candidates, quality_flags}; existing.append.
H22 END loops. collapse_intra_run_twins. write hub_review_<mode>_<stamp>.json.
H23 if --dry-run → no insert. else insert_rows(rows, review_by_id) (same ladder as S44).
H24 PATCH agent_runs {items_processed=total candidates, items_added, errors, cost, notes}.
H25 if live (not dry, not preview): discovered_leads.mark_processed(all hubs mined except
    capped ones) — rewrites discovered_leads.jsonl in place.
```

**`harvest_names.py --from-leads N`** (names channel): fetch page → MODEL CALL N1 (Gemini gemini-3.6-flash, search OFF, `_NAME_SYSTEM`, max 2000) → `select_names` (free gates: name on page, ≥2 identity words, not already in catalog by identity-set; collapse variants; rank; `--min-score`; cap `--max-names`) → per name MODEL CALL N2 (Gemini, googleSearch ON, RESOLVE_SYSTEM, max_searches 1, retry on silent) → `best_resolved_url` (≤3 fetches, title_proves) → `find_duplicates` (exact → skip) → MODEL CALLS H2-H4 via `mine_hub_pages.extract_opportunity` (**without** `index`, so no H5 embedding hint) → `build_row` → `_row_flags` (adds FLAG_SELF_PROMOTED, and `domain_matches_org`-based FLAG_OFFSITE) → `names_review_*.json` → `insert_rows` → `agent_runs` → `mark_processed`.

**`classify_page.py`** (used by S33, H15 step 3, `classify_queue.py`): `fetch_page_text_resolved` → (none → readable=False, cost 0) → one Gemini call → `parse_classification` (class ∉ 4 → klass None; evidence must be ≥12 chars and a normalized substring of the page, else confidence forced low; `is_stale_page` regex) → `route_for`.

### (iii) How a row moves from `is_active=false` to `true` — every writer of `is_active` / `moderation_status`

```
A1  INSERT is_active=false, moderation_status="pending_review":
      scrape_opportunities.insert_rows (S44; hub miner H23; harvest_names) —
        NOTE: at the "no-review"/"minimal" ladder tiers moderation_status is NOT written
        and the row lands with NULL (still counted as queue by ops/core's NULL filter).
      refind_dead_links.py:182 supabase_insert_one({**row, moderation_status pending}).
      app/services/resume.py:229-266 (user-submitted; ladder drops review columns too).
      dryrun_common.commit_snapshot insert kind → is_active forced False; moderation_status
        comes from the snapshot's "review" dict (ops/core._commit_insert).
A2  is_active TRUE ← FALSE (deactivation) by code:
      check_links.build_update:453-463 — action "deactivate" (DEAD after 2 passes, no
        repair) → is_active=false, moderation_status="pending_review", flags; never rejects.
      ops/core.activate_opportunities(ids, active=False) — console button.
      ops/core.moderate_opportunities(status in rejected/duplicate/suspected_duplicate?) —
        _moderation_updates forces is_active=False for rejected/duplicate (line 1582,1907).
A3  is_active FALSE → TRUE:
      ops/core.activate_opportunities(ids, active=True)  [2347-2433]:
        PATCH ladder full → moderation-only → plain:
          {is_active:true, updated_at, moderation_status:"approved", reviewed_by:
           "admin-console", reviewed_at, activation_refresh_queued_at:now unless source
           startswith scraper-/hub-}.
        then bust /api/opportunities cache; _index_activated_rows → PAID embedding
        (dedupe_vector) per row; _embed_match_vectors → PAID embedding (match_vector).
        Both best-effort, swallow all errors, skip when no GEMINI_API_KEY.
      check_links.build_update:438-451 — action "repair" on an INACTIVE row (only reachable
        via --repair-flagged, which selects inactive rows carrying "dead link (" flags):
        is_active=true + url replaced + FLAG_REPAIRED. moderation_status NOT changed (stays
        pending_review from A2).  ← the one code path that activates.
A4  moderation_status changes without touching is_active:
      ops/core.moderate_opportunities(status="approved"|"pending_review") — approve does
        NOT activate; rejected/duplicate/suspected_duplicate force is_active=false;
        duplicate requires duplicate_of; reason stored as "code: note".
      triage_queue.py → POST /api/agents/pending/moderate rejected (hubs/none/stale by
        classify: flag).
      refresh_opportunities --awaiting-refresh → clears activation_refresh_queued_at only.
      check_links: appends flags; a "flag" action on an active row never changes status.
A5  Nothing in the pipeline moves pending_review → approved except the console button
    (activate or approve).  Nothing except A3 sets is_active=true.
```

### (iv) `check_links.py` + `url_repair.py` decision ladder

```
K1  parse args (--all|--sample N|--ids, --force, --flag-only, --repair-flagged (implies
    --repair), --no-repair, --dry-run, --workers 16, --preview, --timeout 20).
K2  select_rows: GET id,name,org,url,is_active,quality_flags,moderation_status,summary,status
    + link_* columns (fallback without them on 42703/PGRST204 → schema_ready=False),
    is_active = false if --repair-flagged else true, order id.
      --repair-flagged → keep rows with a "dead link (" flag → mode "flagged".
      --ids → subset.  else if schema_ready and not --force: drop rows with
      link_checked_at within 7 days.  --sample → random.
K3  --preview → emit_preview(free=True) → EXIT.
K4  INSERT agent_runs {agent:"link_checker", mode:"<mode>[-flagonly][-norepair][-dryrun]"}.
K5  sweep(rows): url_validate.check_urls (GET, --workers threads, mutates
    url_validate.MAX_WORKERS globally) → for every DEAD result re-check once; second
    pass result replaces first (both directions).
K6  if --repair: dead_rows = status DEAD → url_repair.repair_many(dead_rows, 8 threads):
      repair_url(url, name, org):
        R1 identity_words(name) - words(org); <2 words → {"url":None, why:"…fewer than two
           words"} STOP.
        R2 propose(): _variants (www/slash/case/index) as score 1.0; _deepest_live_ancestor
           (walk path up, first ancestor that returns HTML) → same-host <a> links, slug not
           GENERIC_SLUGS, not bare domain, score = max(name_similarity(label,name),
           name_similarity(slug words,name), name_similarity(slug, old_slug)); identity
           word in slug/label → ≥0.6; keep score>0.2; top 10 (+4 variants).
        R3 for each candidate: _fetch → skip if no HTML; skip if final is bare domain
           (soft-404); title_proves(title, name, org): every identity word in <title>
           → then keeps_identity(old_url, final, title, name, org): no identity word that
           the OLD path carried may be missing from new path+title.
           first pass → ACCEPT {url: final, how, title, why}.
           fail → rejected[]; first titled failure → suggestion.
        R4 none → url None, suggestion may be set.
K7  FOR EACH row:  result = results[url] (or UNVERIFIED "unchecked");
K8    classify(row, result, repair):
        DEAD  and repair.url            → ("repair", [FLAG_REPAIRED(code, old)])
        DEAD  else                      → ("deactivate", [FLAG_DEAD(code)] + FLAG_SUGGESTION?)
        UNVERIFIED, numeric code        → ("flag", [FLAG_BLOCKED(code)])
        UNVERIFIED, exception name      → ("flag", [FLAG_UNREACHABLE(code)])
        LIVE, url had a path, final_url is bare domain → ("flag", [FLAG_SOFT_404])
        LIVE                            → ("ok", [])
      "deactivate" and --flag-only → "flag".
K9    disc_phrase = DISCONTINUED_RX.search(row.summary); mark_not_running = disc_phrase and
      status blank → flags += FLAG_DISCONTINUED.
K10   --dry-run → snapshot only, continue.
K11   build_update(row, action, flags, result, now, schema_ready, repair, mark_not_running):
        status:="not_running" if mark_not_running (bumps updated_at);
        "repair" → url := repair.url, result := LIVE/200;
        schema_ready → link_status, link_status_code, link_checked_at=now,
          link_dead_since = first-seen (DEAD) else None;
        quality_flags = merge_flags (strip _OWNED_PREFIXES, keep others, dedupe);
        "repair" and row inactive → is_active=True (RESTORE);
        "deactivate" and row active → is_active=False, moderation_status="pending_review"
          (reviewed_by/at untouched);
        updated_at only if a non-link_* key changed.  None if nothing changed.
K12   apply_update: PATCH; on missing-column error retry without link_* (latches
      schema_ready False). Other errors → errors++.
K13 summary; full report to agent_logs/link_check_<stamp>.json (or link_check_dry_run_*.json
    at root for --dry-run); PATCH agent_runs {items_updated = deactivated+flagged+
    discontinued, cost 0}.
```

### (v) `check_deadlines.py` — `check_one` and `deadline_write_decision`

Two callers: batch `main()` (line 1209) and `app/routes/opportunities.handle_deadline_check` (interactive, 7-day cache, `refresh=1` forces).

```
D1  check_one(opp, key, retry_on_silent=True, want_requirements=False|True)  [931]
D2  find_program_sources(opp, key, want_dates=True, want_requirements)       [860]
      full=(dates and requirements) and opp.id in _shared_capture_cache (module dict,
      120 s TTL, never evicted) → return cached 7-tuple with cost 0.
D3  research_deadlines(opp, key, retry_on_silent, trusted_domains, discover=
    sitemap_common.discover_candidate_pages)                                  [746]
      trusted_domains ← aggregators_common.get_policy (Supabase trusted_aggregators,
        5-min cache; absent table → []).
      sitemap_urls ← free: robots.txt → sitemap(s) (≤25 children, ≤20k urls) → scope by
        stored path prefix/name tokens → rank slugs → top 5.
      FOR rung in RUNGS[:4] ("current cycle","prior cycle","subpages","trusted third-party"):
        rung 4 skipped when trusted_domains empty.
        _search_round(opp, key, focus, retry, candidate_urls=sitemap_urls|None):
          MODEL CALL D-1: Anthropic claude-haiku-4-5, tools web_search (max_uses=1) +
            web_fetch (max_uses=5, 4000 tok/page), system=build_system(opp) with
            cache_control ephemeral, max_tokens 1200, prose out ending with three
            SITE_REACHED/FOUND_CONFIRMED_DATES/FOUND_PRIOR_CYCLE_BASIS lines.
          cost banked per attempt; searches = usage.server_tool_use.web_search_requests.
          searches==0 and attempt 1 → retry identical (MODEL CALL D-1').
        _parse_signals(notes) strips the 3 lines (missing line → False).
        rung 4 → sources/captured filtered to trusted domains.
        site_reached |= SITE_REACHED. confirmed → break. idx≥1 and prior_basis → break.
      → (notes, cost, searches, union_sources, attempts, site_reached, union_captured)
D4  if want_requirements: source_capture.fetch_and_capture(opp, key, policy):
      MODEL CALL D-2: Anthropic Haiku, web_search max 2 + web_fetch max 4 (6000 tok/page),
        FETCH_SYSTEM, max_tokens 600, sitemap candidates injected. captured += parsed
        web_fetch_tool_result blocks (HTML text or PDF via PyPDF2); reason ok → site_reached
        True.  Re-tier every captured page (official/trusted/blocked/pending).
      cache full result.
D5  check_one: if searches == 0 → return ({}, cost, 0, attempts, site_reached).
D6  extract_deadlines(opp, notes, sources): MODEL CALL D-3: Anthropic Haiku, NO tools,
    build_extract_system() (strict JSON: status, important_dates[{type,label,date_iso,
    estimated,...}], was_estimated, important_date_note, status_evidence). extract_json →
    dict, or None on parse failure (propagates "None" deliberately).
D7  verify_status_evidence(info, captured): status == not_running → status_evidence quote
    must be quote_is_on_page of some captured page → keep + append marker to note; else
    status:="unknown" + caveat.
D8  verify_dates_against_capture(info, captured, today): per date: estimated → verified
    False; else date_is_on_page → verified True + source_url; not found and date == today
    → estimated:=True (today-anchoring demotion) + caveat; else unverified++.
D9  return (info, cost, searches, attempts, site_reached).

W1  deadline_write_decision(info, searches, existing_dates, site_reached)   [1151]
W2    searches == 0 → (write False, "unverified-fallback")
W3    info is None → (False, "unparsed-fallback")
W4    status,dates,was_estimated,note = normalize (status ∉ VALID_STATUS → "unknown";
      dates without date_iso dropped)
W5    if not dates and status ∉ {not_running, rolling}:
        not site_reached → (False, "unreachable-fallback")
        existing_dates non-empty → (False, "kept-existing")
W6    else → (True, "fresh, real search", status, dates, was_estimated, note)

Batch main(): GET active rows (select includes status,important_dates; ignores the TTL);
  --sample/--ids/--missing-opens; --preview exits; INSERT agent_runs; per row D1→W1;
  write → PATCH status, important_dates, was_estimated, important_date_note,
  dates_last_checked_at, updated_at (or dry-run snapshot entry with changed flag);
  no-write → counters only.  PATCH agent_runs.
Interactive: cache fresh and not refresh → cached payload.  No ANTHROPIC key → mock.
  D1(want_requirements=True) → W1 → no write: cached payload with decision.source, log
  deadline_check_log, record_user_cost (cost billed even when nothing written); write →
  PATCH (no updated_at) + log + user cost.  Any exception → "stale-fallback" cached payload,
  cost NOT recorded.
```

### (vi) `generate_action_items.py`

```
G1  parse args (--sample|--all|--ids|--missing, --force, --dry-run, --preview, --timeout
    120, --min-delay 5). apply_timing(claude=True) → claude_common (NOT check_deadlines'
    own limiter — see §4.16).
G2  GET is_active=true rows; --ids → in.(); --missing → action_items is.null; else
    staleness (checked_at null or < now-90d) unless --force.  400 mentioning action_items
    → "run ../../db/action_items_schema.sql" exit.
G3  --preview → emit_preview → EXIT.  INSERT agent_runs {agent:"action_item_generator"}.
G4  FOR EACH row: process_one(opp, key, timeout)                               [455]
G5    policy = aggregators_common.get_policy(...)
G6    check_deadlines.find_program_sources(opp, key, want_dates=False,
      want_requirements=True, policy) → D4 only (MODEL CALL D-2, Anthropic Haiku, search
      max 2 + fetch max 4).  No cache (not "full").  reason ok iff any source has text.
G7    combined = all captured text. text_ok = non-empty.
G8    if text_ok: MODEL CALL G-1: Anthropic Haiku via claude_common.call_claude, NO tools,
      SYSTEM (3-5 steps, basis page|generic, verbatim evidence), max_tokens 1400.
      cost banked immediately.  extract_json in its own try → raw items, model_ok.
G9    verify_items(raw, opp, sources):                                          [271]
        per item: claim_is_supported(task, combined, name, org) — every distinctive token
          of the task on some page, else DROP.
        basis page + evidence: quote_is_on_page(evidence, source.text) for some source →
          basis page, tier = source.tier; else DEMOTE to generic.
        basis page but tier blocked → DROP; tier ≠ official and is_eligibility_claim(task)
          → DROP (dropped_eligibility).
        keep ≤ MAX_ITEMS=5.
G10   existing = opp.action_items list or [].
G11   action_items_write_decision(kept, opp, text_ok, model_ok, existing)       [407]
        page_ok and model_ok:
          kept non-empty → shallow = all items furniture → WRITE top_up(kept), source
            "page-verified", stamp = not shallow.
          kept empty → WRITE generic_items, "page-empty", stamp True.
        page_ok and not model_ok: existing → NO WRITE "unparsed"; else WRITE generic,
          "unparsed", no stamp.
        page not ok: existing has a page-backed item → NO WRITE "generic-fallback";
          else WRITE generic, "generic-fallback", no stamp.
G12   decision.write False → counters, continue.  --dry-run → snapshot.  else PATCH
      {action_items, action_items_source, [action_items_checked_at if stamp]} (no
      updated_at).
G13 summary; dry-run snapshot action_items_dry_run_<stamp>.json; PATCH agent_runs.
Interactive: app/services/action_items.resolve(opp_id) reuses process_one-equivalent +
  action_items_write_decision; serve path withholds pending/blocked-tier items.
```

---

## 4. Accuracy and logic gaps (bugs found by reading, not restated from docs)

Ordered roughly by impact.

**4.1 A model-typed URL still reaches the catalog, in two places.**
- `reconcile_url` (`scrape_opportunities.py:537-582`): when no span matched and the model's URL is not among retrieved pages and no same-host page was retrieved, it returns `model_url + [FLAG_URL_UNSOURCED]`. The row is then staged, liveness-checked, and inserted (flagged). Under `--no-verify-urls` it is inserted without even the liveness check.
- `refind_dead_links.py:177-183`: inserts a row at `new_url` — this one is grounding-resolved and title-proven, fine — but its dedupe set is **`rows` = `is_active=eq.false` only** (line 128-130). A re-found URL that already exists as an ACTIVE row is inserted as a duplicate pending row. The active catalog is never consulted.

**4.2 Concurrent id minting collides and then silently strips review data.** `next_id_generator` (`scrape_opportunities.py:496`) mints `ec<max+1>` from an in-memory snapshot taken at run start; `mine_hub_pages.py:808`, `harvest_names.py:550`, `refind_dead_links.py:154` do the same. Only the search-using agents share `.gemini_web_search.lock`; `mine_hub_pages` (no search) can run concurrently with the scraper, and `ops/core.running_gemini_search_agent` only blocks console launches of `uses_gemini_search` agents. Two overlapping runs mint the same ids → PK violation on POST → and `insert_rows`' ladder (4.4) turns that into a silently narrower insert or a lost batch.

**4.3 Cost banked after an exception (money lost from every total).**
- `scrape_opportunities.research_seed:780-816`: `cost` is a local accumulated across the retry; if attempt 2 raises (timeout/429), attempt 1's cost is never returned. Same shape in `check_reviews.research_reviews:211-245` and `check_deadlines._search_round:688-716` (the latter's docstring claims per-attempt banking — it banks into a local that is lost on raise).
- `scrape_opportunities.extract_candidates:852-864` and `check_reviews.extract_review:248-262`: `extract_json(text)` runs **before** `estimate_cost(usage)`; a `ValueError`/`JSONDecodeError` discards the phase-2 cost and (because `check_one` re-raises) the phase-1 cost too. `combined_reader.extract_metadata` and `classify_page.classify_from_text` do it right; the two oldest agents do not.
- `mine_hub_pages.extract_opportunity:356-360`: `extract_json(out)` before the `cost` is returned to the caller; on parse failure the caller's `except Exception` (line 851) counts an error and the call's cost vanishes.
- `harvest_names.harvest_names:416-418`: `parse_names(extract_json(out))` evaluated inside the return tuple before `estimate_cost` — same loss.
- `app/routes/opportunities.py:171-179`: an exception inside `check_deadline_one` (after one or more paid rungs) lands in "stale-fallback" with **no `record_user_cost` and no cost in `deadline_check_log`**.

**4.4 `insert_rows` degrades on ANY exception, not just a missing column** (`scrape_opportunities.py:875-904`). A statement timeout, a PK collision (4.2), a 5xx, or a malformed jsonb value triggers "no-attribution" → "no-review" → "minimal" retries. Two consequences: a transient error silently inserts the batch **without `moderation_status`, `dup_candidates`, `quality_flags`, `seed_id`** (the whole review-queue payload); and because `supabase_post` batches at 500, a batch that half-succeeded is re-POSTed with duplicate ids at every tier. `check_links.apply_update` and `ops/core.activate_opportunities` check `_is_missing_column` before degrading; this one does not.

**4.5 Snapshot commit dedupes with a different key than the scraper** (§2.1) and replays stale field values with no staleness guard. `dryrun_common.commit_snapshot` only reads `STALE_DAYS` for display; `_patch_updates("deadline")` (line 251-261) writes `status/important_dates/…` **and stamps `dates_last_checked_at=now`**, overwriting any interactive check made since the dry run and re-arming the 7-day TTL on days-old data. Committing the `reviews` kind re-stamps `last_reviewed_at` the same way.

**4.6 The scraper PATCHes ACTIVE catalog rows automatically** (`apply_merge`, `scrape_opportunities.py:716-743`). `merge_row` fills empty `org/summary/eligibility/grade_min/grade_max/subject_tags/contact_email` from a phase-2 candidate whose fields came from search notes, not from the page (the M9 gate's page-metadata overlay happens later, at S34, only for inserted rows — merged candidates skip it). The audit trail is a `merged …` entry in the survivor's `quality_flags`, which the console never shows for active rows (queue lists inactive only) — so an active row can be silently edited by a scrape with no visible record except the local `scrape_review_*.json`. It also bumps `updated_at`, and `check_refresh_progress.py` will count it as a refresh.

**4.7 Dedupe only runs at insert; renames and URL repairs never re-dedupe.** `check_links` repair writes a new `url` (`build_update` line 420) without checking whether another row already sits at that URL → two rows with one `match_key`. `refresh_opportunities` can rewrite `name` (line 302-305) with no dedupe. `ops/core.update_pending_opportunity` lets an operator edit `url`/`name` on a queued row; nothing re-runs `find_duplicates`.

**4.8 `find_duplicates`' `apply_url` branch is dead.** Every caller passes rows selected as `id,name,url` (`scrape_opportunities.py:1096`, `mine_hub_pages.py:691`, `harvest_names.py:512`, `app/services/resume.catalog_dedupe_rows`). `apply_url` is not a column (`app/services/resume.py:270-273` says so). The "apply-url points at the same page" strong hint (`url_dedupe.py:352-357`) can never fire; the docstring still advertises it.

**4.9 Two writers of `status=not_running` with different evidence bars, and the free one has a false-positive path.** `check_deadlines.verify_status_evidence` demands a verbatim discontinuation quote on a fetched page (else downgrades to `unknown`). `check_links.DISCONTINUED_RX` (line 161-172) runs over `row.summary` — model-written prose — and fires on `has (ended|closed|…)`, `\bceased?\b`, `no longer … accept`. A summary such as "Applications for the 2026 cohort has closed; the program runs annually" matches `has closed` and marks the program `not_running`, hiding it from Fresh Finds and matching. It was measured at 3/1678 on 2026-09-01, but every scraper/refresh pass rewrites summaries and the regex has no page evidence and no verifier. `_status_blank` prevents overwriting a deadline verdict, but a not_running written here **blocks** the empty-`important_dates` carve-out logic in `deadline_write_decision` from ever being questioned: `EMPTY_IS_VALID_STATUS` treats not_running as authoritative.

**4.10 `is_not_running` false positives depend on JSON type, and the scraper's copy has a wider trigger than the miner's.** `queue_flags.is_not_running` only fires on Python `False`. The scraper's `EXTRACT_SYSTEM` (line 469-472) lets phase 2 set `running=false` from **phase-1 search notes** ("most recent cycle has already passed with no future one") — the notes, not a page — whereas the hub miner's `_EXTRACT_SYSTEM` reads the page. A silent (memory-only) phase 1 can therefore produce `running:false` from training data and the candidate is dropped before any reviewer sees it; `FLAG_NOT_SEARCHED` is added to rows, not to drops.

**4.11 Silent-search detection trusts a count that is derived, not billed.** `gemini_common.call_gemini:478-486` sets `web_search_requests = len(groundingMetadata.webSearchQueries)`. If Gemini returns grounding chunks but an empty `webSearchQueries` (observed shape variance is undocumented), the call is treated as silent, retried (paying twice), and flagged. Conversely the per-search fee is estimated from the same count. `check_deadlines.call_claude` reads `usage.server_tool_use` — missing block → `{}` → 0 searches → "silent" → retry; a real search whose usage block is absent is billed twice.

**4.12 `record_seed_result` writes on `--dry-run` and races with itself.** Documented exception, but note the PATCH (`seeds_common.py:145-154`) rewrites all five `total_*` counters from a re-read; two runs on the same seed (possible when one is a `--dry-run` launched by hand while the console runs the other — the search lock only covers agents that actually reached their first search call) lose one increment. `last_run_at` is UTC; `first_seen` in leads and `source` in rows are **local** dates (`datetime.date.today()`), so around midnight a run's `agent_runs.started_at` date and its `source` date differ.

**4.13 Anon-key fallback silently truncates the dedupe set.** `mine_hub_pages.py:689`, `harvest_names.py:510`, `walk_up_hubs.py:264`, `classify_queue.py:65`, `dedupe_queue.py:435`, `refind_dead_links.py:123`, `discovered_leads.py:532` all do `SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY`. Under the anon key RLS returns only `is_active=true` rows, so `existing`/`catalog_keys` omit every queued/rejected row; `fresh_candidates` then re-extracts (pays for) pages already in the queue, and the insert itself fails on RLS. The scraper (`scrape_opportunities.py:1055`) correctly requires the service key; the six newer scripts do not.

**4.14 Pagination without ORDER BY** (§2.3): ~39 of 45 `opportunities` reads page past 1000 rows unordered, including the scraper's dedupe set, the hub miner's, `check_deadlines.main`, `refresh_opportunities.main`, `fetch_dedupe_index` (page_size 200 → 9 pages over a table the scraper is PATCHing during the same run via `apply_merge`). A skipped row = a missed duplicate; a repeated row = harmless.

**4.15 Date/year handling.**
- `classify_page.is_stale_page` uses `today_year - 3` on any `19xx/20xx` token not adjacent to a digit; a phone number `555-2019` or a street address counts as a year (safe direction only if the page also carries a newer year). A copyright footer keeps a dead page alive (acknowledged).
- `sitemap_common.rank:587` hard-codes `>= 2025` for the lastmod recency bonus — stale after this year.
- `check_deadlines.build_system:331` computes `this_year/next_year` from local date; the interactive path runs in the server's local TZ. `verify_dates_against_capture:1062` compares `date_iso == datetime.date.today().isoformat()` (local) while the rest of the row is stamped UTC — a check at 11 pm Pacific compares against yesterday's UTC date.
- `check_links._parse_iso` and the staleness cutoffs are tz-aware (correct). `dryrun_common._run_date` parses a **local** stamp as UTC (documented).

**4.16 `generate_action_items` throttles the wrong limiter.** `apply_timing(args, claude=True)` (line 525) sets `claude_common`'s delay/timeout. Its capture call goes through `check_deadlines.find_program_sources` → `source_capture._capture_call`, which uses `claude_common._enforce_rate_limit` (so delay applies) but the deadline half, if ever enabled (`full_capture=True` in `process_one`), uses `check_deadlines`' own limiter which this script never sets (stays 0). Meanwhile `check_deadlines.main` sets only its own limiter, so the `source_capture` calls it makes via `find_program_sources(want_requirements=…)` are unthrottled there. Two limiters, each agent sets one.

**4.17 `check_deadlines._shared_capture_cache` grows without bound** in the FastAPI process (dict keyed by opp id, TTL checked only on read, never evicted). Each entry holds the full captured page text for the row.

**4.18 Exception handlers that swallow and continue, hiding partial work:**
- `scrape_opportunities.py:1440-1445` per-seed `except Exception` — a seed that fails at S33 (gate) after S31 already PATCHed a survivor keeps the PATCH; the seed's inserted rows so far are kept, the seed is still credited via `record_seed_result` with `added=seed_added` (rows counted as added but the loop aborted before `existing.append` for later candidates).
- `scrape_opportunities.py:1401-1410` contact-email `except Exception: pass`.
- `mine_hub_pages.py:851-854`, `harvest_names.py:601-604/617-620`, `refind_dead_links.py:172-174` (a failed refind still stamps `refind_attempted` so the row is never retried).
- `dedupe_embed_store.fetch_dedupe_index:473-480`: a statement timeout on the 200-row page → empty index → dedupe hint silently off for the whole run (announced by one WARN line in a 1400-line log).
- `_reembed_row` (`refresh_opportunities.py:187-188`) swallows a failed vector PATCH — the row's `dedupe_vector_hash` then disagrees with its content until the next backfill.

**4.19 Hard-coded values that will rot:** `next_id_generator` floor `18220`; `sitemap_common` `>= 2025`; `gemini_common.MODEL="gemini-3.6-flash"` with a comment that the previous pin 404'd; `WEB_SEARCH_PRICE_PER_SEARCH` "valid through Dec 31, 2026"; `harvest_names`/`mine_hub_pages` `--min-delay` typed `int` (a `2.5` from the console's timing table would fail to parse).

**4.20 State that exists only on the operator's machine** (so a second checkout or a Render box sees a different pipeline): `discovered_leads.jsonl` (the entire hub/names work-queue, 208 KB; `mark_processed` rewrites the whole file non-atomically), `agent_logs/*.json` (the only record of phase-1 notes/queries; `backfill_seed_attribution` and `query_telemetry` depend on them), the 10 snapshot families (commit path), `.gemini_web_search.lock`, `agent_settings.json` (console timing overrides), `NATIONAL_SEEDS`/`SEATTLE_SEEDS` fallback literals (used silently when Supabase is unreachable — a run on a bad network scrapes 2026-08-20's angle list and credits nothing), `tests/fixtures/pair_resolution_20260826.json` (read by `repair_survivor_names.py`), and the headless-Chromium install (`page_text._fetch_with_browser`).

**4.21 `check_reviews` staleness and the `--force`/`--sample` combination.** `random.sample` runs over `candidates` after the staleness filter; `mode` for `agent_runs` is `sample-force-dryrun` etc. Fine. But `check_reviews.main` selects `is_active=eq.true` **with no `order` when `--force`** is combined? No — `order` is always set (line 311). OK. The real gap: a silent row "stays due" forever if Gemini keeps declining to search for it — each pass re-pays two silent calls per such row with no cap or backoff (`silent_search_count` is reported but never used to skip).

**4.22 Prompts that ask for JSON in a search-enabled call:** none of the three search agents (checked `DISCOVERY_SYSTEM`, `RESOLVE_SYSTEM`, `check_reviews.build_research_system`, `check_deadlines.build_system`, `source_capture.FETCH_SYSTEM`). `build_system` asks for three machine lines at the end — a structured tail, not JSON; measured OK. `gemini_common.call_gemini` appends "You MUST call the googleSearch tool…" to every search-enabled system prompt (line 420-427), which the repo's THIRD finding says has no effect; harmless but it makes every phase-1 prompt differ from what is in the source file.

---

## 5. Operational risks

1. **Nothing is scheduled anywhere except a disarmed GitHub workflow.** `.github/workflows/lifecycle-emails.yml` is `workflow_dispatch` only; `ci.yml` runs tests. Every catalog agent runs from the operator's laptop by hand or from the localhost-only ops console (`WINGMAN_ENABLE_OPS`). The catalog's freshness (deadlines aside, which are on-demand) is a function of one person remembering.

2. **Run locks.** The only cross-process lock is `.gemini_web_search.lock` (`gemini_common.py:237-340`), acquired lazily on the first `use_web_search=True` call: (a) it does not cover `mine_hub_pages`, `refresh_opportunities`, `classify_queue`, `dedupe_queue`, `build_catalog_embeddings`, `generate_action_items`, `check_deadlines`, `check_links` — all of which write to `opportunities` and several of which mint ids (4.2); (b) it is shared with the **dev server**: `app/routes/ai.py` calls `call_gemini` with search, so an interactive `/api/messages` with `useWebSearch` raises `RuntimeError` while any scraper/review run is live on the same machine (and vice-versa: a server request thread holds the lock for the process lifetime once acquired — `_lock_acquired` is process-global and released only at exit — so after one interactive search the server "owns" the lock and every CLI agent fails fast until the server restarts); (c) `ops/core.is_agent_running` is in-memory per server process — a CLI run started from a terminal is invisible to the console and the console will happily start a second copy. No agent writes a "running" marker to `agent_runs` that another process could check.

3. **Idempotency.** Idempotent by design: `check_links` (7-day `link_checked_at`), `check_reviews` (30-day `last_reviewed_at`), `generate_action_items` (90-day), `refresh_opportunities` (none — every `--all` run re-pays every row; there is no `metadata_checked_at`), `find_mailing_lists` (skips rows with a recipe), `find_contact_emails` (skips rows with an email). NOT idempotent: the scraper (every run re-searches every enabled angle and relies on dedupe to discard; `record_seed_result` counts dupes but nothing raises the price of a re-run), `mine_hub_pages --hubs` (only `--from-leads` marks processed), `harvest_names` (same), `refind_dead_links` (stamps `refind_attempted` even on failure → never retried), `check_deadlines --all` (ignores the 7-day cache by design; ~$84/pass).

4. **`--preview` coverage.** Present on the seven console agents plus `find_contact_emails`, `find_mailing_lists`, `mine_hub_pages`, `harvest_names`, `refind_dead_links`, `classify_queue`, `dedupe_queue`, `propose_angles`. Absent on paid scripts `backfill_match_vectors` (has `--dry-run`+`--yes-really`), `build_catalog_embeddings` (same), `backfill_subject_tags` (`--dry-run` only, still pays), `dedupe_eval`, `matching_eval`. Only the seven emit `PREVIEW_JSON:`; the others cannot be priced by `ops/core.preview_agent`.

5. **Cost caps.** Per-run hard ceilings exist only in `mine_hub_pages` (`--max-pages`, round-robin), `harvest_names` (`--max-names` per page, no run cap), the scraper's stage 1b (`--resolve-per-run 150`, `--resolve-per-angle`). The scraper's phase 1 has a *soft* `--max-searches 10` per seed folded into the prompt (Gemini does not enforce it) and no per-run dollar ceiling; the discovery gate adds 2-3 unbounded paid calls per candidate with no cap on candidates per seed. `check_deadlines`/`generate_action_items`/`source_capture` cap searches per call (`max_uses`), not per run. There is no global "stop when `total_cost > X`" anywhere; `ops/core.AGENT_RUN_TIMEOUT_SECS` is the only backstop and it kills the process (losing the closing `agent_runs` PATCH, which is why `cost_usd` NULL rows exist).

6. **Spend that appears in no total.** `refind_dead_links.py`, `classify_queue.py`, `dedupe_queue.py`, `build_catalog_embeddings.py`, `backfill_match_vectors.py`, `dedupe_eval.py`, `matching_eval.py`, `sitemap_hub` classifier calls (rolled into the miner's total — fine) write **no `agent_runs` row**; their cost is printed to the console and lost. Activation-time embeddings (`ops/core._index_activated_rows/_embed_match_vectors`) return a cost in the HTTP response and record nothing. `refresh_opportunities`' re-embed cost is folded into its run (fine).

7. **Supabase 1000-row cap / statement timeout mid-run.**
- `supabase_get` raises on any HTTP error; the scraper's `existing` fetch (S6) and `check_deadlines.main`'s catalog fetch are outside any try → the run aborts before spending, good. `fetch_dedupe_index` (S7/H12) catches everything and returns `[]` → the run proceeds with the embedding dedupe silently OFF.
- `insert_rows` (S44) after a 110-minute paid run: a 57014 timeout on a 500-row batch → ladder (4.4) → likely a "minimal" insert with no review columns, or a raise that leaves *everything* unwritten while `agent_runs` is never PATCHed (the exception propagates out of `main`) — the local `scrape_review_*.json` is then the only copy, and committing it re-dedupes with the wrong key (4.5).
- `apply_merge` PATCHes are per-row and unguarded (`except Exception` → logged, `result["changed"]=False`) — fine.
- `record_seed_result` and `auto_disable_mined_seeds` swallow errors (fine, documented).
- `check_links.sweep` holds 16 threads × 20 s; `select_rows` reads ~1700 rows with `quality_flags,summary` in one paginated select — no timeout risk observed, but `--repair-flagged` reads every inactive row (now larger than the active set).

8. **Local-only state makes a second machine a different pipeline** (4.20). In particular `discovered_leads.jsonl` is the *only* queue for hub mining and name harvesting; there is no table, no backup, and `mark_processed` truncates-and-rewrites it.

9. **The `.gitignore` says snapshots/logs are ignored while seven of them are tracked** (§1.3); a `git add -A` after a run will not add new ones, but the stale tracked copies are what a fresh clone's console lists as committable (2 weeks old, `stale: true`).

10. **Dry runs that cannot be committed** (§2.9): `hub_review_*`, `names_review_*`, `action_items_dry_run_*`, `mailing_list_dry_run_*` are paid and uncommittable.

---

## 6. Tests

`tests/unit/` has 84 test files (`pytest`, fixtures in `tests/fixtures/`: `scraper_grading_20260823.json`, `pair_resolution_20260826.json`, `tracker_data_deadline_alerts.json`, `sitemaps/`). Mapping of root modules to tests (from import analysis):

**Covered (root module → test file(s)):** `agent_common` (test_agent_common), `aggregators_common` (test_aggregators_common, test_source_capture), `backfill_match_vectors`, `backfill_seed_attribution` (test_backfill_attribution), `build_catalog_embeddings`, `build_legal`, `check_deadlines` (test_check_deadlines_helpers — helpers only), `check_links` (test_check_links), `classify_page` (4 files), `classify_queue`, `claude_common` (test_estimate_cost, test_extract_json, test_walk_up_hubs), `combined_reader`, `contact_email_common` (test_contact_email), `db_health_check`, `dedupe_confidence`, `dedupe_embed_store`, `dedupe_eval`, `dedupe_queue`, `discovered_leads` (5 files), `dryrun_common` (test_dryrun_commit), `embed_common`, `find_catalog_dups`, `gemini_common` (test_embeddings, test_estimate_cost, test_extract_json, test_walk_up_hubs), `generate_action_items` (test_action_items), `grade_scraper_batch`, `harvest_names` (test_name_harvest, test_stage_1b), `mailing_list_common`, `matching_eval`, `mine_hub_pages` (test_discovered_leads, test_hub_mining), `page_text` (5 files incl. test_page_text_browser), `propose_angles` (test_phase4_selection), `query_telemetry`, `queue_flags` (test_queue_flags, test_scrape_discovery_gate), `refind_dead_links` (test_name_harvest, test_phase4_selection), `refresh_opportunities` (test_refresh_page_reader, test_page_text_browser), `scrape_opportunities` (7 files: test_discovery_prompt, test_merge, test_scrape_discovery_gate, test_scrape_insert, test_scraper_urls, test_stage_1b, test_url_truth), `seed_ledger`, `seeds_common` (test_seeds), `sitemap_common`, `sitemap_hub`, `source_capture`, `subscription_common`, `supabase_common` (only via test_dedupe_embed_store), `triage_queue`, `url_dedupe`, `url_repair`, `url_validate`, `walk_up_hubs`.

**Paid-path modules with NO test importing them:**
- **`check_reviews.py`** — the two-phase review agent (`clean_sources`, `research_reviews`, `extract_review`, `check_one`, the staleness `or=` filter): zero tests.
- **`find_mailing_lists.py`** — zero (its library `mailing_list_common` is tested).
- **`find_contact_emails.py`** — zero (library tested).
- `backfill_subject_tags.py` (paid, one-off) — zero.
- `check_deadlines.py`: only `test_check_deadlines_helpers.py` (write decision / verifiers); `call_claude`, `_search_round`, `research_deadlines` rung loop, `find_program_sources` cache, `extract_deadlines` have no tests.
- `scrape_opportunities.main()` loop itself (the per-seed try/except, `insert_rows` ladder, `apply_merge` on live rows, stage-1b budget maths, `record_seed_result` on dry-run) — untested; `insert_rows` has `test_scrape_insert.py` but only the column-ladder success case, not the "any exception degrades" behaviour (4.4).
- `gemini_common.call_gemini` / lock / 429 path — untested (only `extract_json`/`estimate_cost`/embeddings).
- `supabase_common` — effectively untested (one indirect import); its pagination termination and `Range` semantics have no test.
- `seeds_common.record_seed_result` read-modify-write — `test_seeds.py` exists; verify it covers the re-read (not confirmed here).
- `dryrun_common.normalize_url` vs `url_dedupe.match_key` disagreement — no test asserts they agree (they don't).

---

### Appendix — quick numeric summary

- Root scripts: 69. Shared libs 30, console-wired entry points 26, one-off migrations/backfills 9, eval/grading 6, orphans 2 (`grade_url_truth.py`, `repair_survivor_names.py`).
- Tracked junk at root: 14 files + a 17-file `.tmp_landing_zip/` tree; 7 of them match `.gitignore` patterns.
- Supabase request builders: 8. Pagination loops: 5 (2 mechanisms). `normalize_url` copies: 2 (both differ from the scraper's real key). Anthropic call builders: 3. Gemini embedding clients: 2 (768 vs 3072 dims). HTML tag strippers: 4. Page fetchers: 5. `load_dotenv`: 4. Model-pin literals for Haiku: 3 (+app), for flash-lite: 4 (+app).
- Paid scripts logging no `agent_runs` row: 8.
- `opportunities` reads paginating without `order`: ~39 of 45.
