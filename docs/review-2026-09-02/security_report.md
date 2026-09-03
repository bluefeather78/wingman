# Highschool Wingman — backend application-security review

Scope: `app/**` (all files), `ops/admin.py` + request-facing `ops/core.py`, `server.py`, `supabase_common.py`, `subscription_common.py`, `mailing_list_common.py`, every `*_schema.sql`, `frontend/src/api/httpClient.ts`, `frontend/src/api/tokenStore.ts`, `render.yaml`, `.github/workflows/*.yml`, plus the fetch paths the shipped routes call into (`gemini_common.py`, `check_deadlines.py`, `sitemap_common.py`, `page_text.py`, `source_capture.py`, `dryrun_common.py`). Static review only — no server run, no network calls, no files modified. Line numbers are from the working tree on 2026-09-02.

The user base is largely minors, so PII exposure is rated one step higher than it otherwise would be.

## Summary

| id | severity | title | file |
|---|---|---|---|
| C1 | Critical | Unauthenticated, unmetered, attacker-prompted paid LLM proxy (Gemini + Anthropic, web search on) | app/routes/ai.py:148-175, app/deps.py:106-108 |
| H1 | High | Google sign-in links to an existing account by e-mail without checking `email_verified` → account takeover | app/routes/google_oauth.py:174-182 |
| H2 | High | `app_redirect` allowlist is a string-prefix match → open redirect that leaks the one-time sign-in token → account takeover | app/routes/google_oauth.py:86-92, 199-204 |
| H3 | High | Login/register rate limiter keys on the socket peer; behind Render's proxy that is one shared bucket (global lockout with 10 requests) or, if forwarded headers are trusted, trivially spoofable | app/auth/ratelimit.py:302-350, app/routes/account.py:88,164, render.yaml:18 |
| H4 | High | Authenticated users have no per-user spend cap; forced deadline re-checks, `/api/match`, action-item generation and both AI proxies are unlimited | app/routes/opportunities.py:91-113, app/routes/matching.py:281-349 |
| H5 | High | Catch-all static route serves any non-denylisted file under the repo root (admin console HTML, logic map, catalog snapshots, eval data, fixtures, node_modules, lockfiles) | app/main.py:128-129, 175-190, 208-230 |
| M1 | Medium | Blind SSRF: any subscriber can make the server GET `robots.txt`/sitemap paths on a host they chose via an unauthenticated opportunity submission | app/routes/resume.py:94-153, app/routes/opportunities.py:77, check_deadlines.py:882-887, sitemap_common.py:92-106 |
| M2 | Medium | Refresh tokens are 30-day bearer tokens with no rotation/reuse detection; a stolen one keeps working after the victim "logs out" | app/auth/tokens.py:407-431, app/routes/auth.py:26-53 |
| M3 | Medium | Access JWT travels in a URL query string (calendar connect) → access logs, browser history | app/routes/google_oauth.py:296-303, frontend/src/api/httpClient.ts:659-664 |
| M4 | Medium | No request-body size limit anywhere; Anthropic proxy `urlopen` has no timeout; events `context` unbounded → memory/threadpool exhaustion | app/deps.py:58-61, app/routes/ai.py:122, app/core.py:1078-1118 |
| M5 | Medium | Process-wide 5 s sleep before every Gemini call in the web server (inherited from the batch library) → any caller can stall every AI feature for every user | gemini_common.py:163,183,221-234,447; app/routes/ai.py:77, app/routes/matching.py:333 |
| M6 | Medium | Promo-code redemption is read-check-write with no atomicity → double grant via concurrent requests | app/routes/subscription.py:143-161 |
| M7 | Medium | Account enumeration: distinct messages for unknown userid vs wrong password; register reveals whether an e-mail is taken | app/routes/account.py:113-121, 174-182 |
| M8 | Medium | Ops console is protected only by "peer is 127.0.0.1"; defeated by any localhost tunnel; `server.py` enables it unless `RENDER` is set; admin HTML is publicly served anyway (H5) | ops/admin.py:18-30, server.py:32-33 |
| M9 | Medium | PII of minors (userids, e-mail addresses, chat answers + client IP) written to stdout and to a `conversations` table that has no schema/RLS file | app/core.py:129-151, 545-571; app/services/email.py:382,398,612,659; app/services/mailing_list.py:160 |
| M10 | Medium | Unauthenticated write into the `opportunities` table (review-queue spam, arbitrary URLs later fetched by operator agents, 1.4k-row read per request) | app/routes/resume.py:94-96, app/services/resume.py:169-258 |
| M11 | Medium | No security headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy); CORS `*` in prod; OAuth state cookies lack `Secure` | app/main.py:37-47, 64-87; app/routes/google_oauth.py:124-125, 336-337 |
| L1 | Low | Legacy rows store the bare client SHA-256 (password-equivalent at rest) until the user next logs in; no password length/strength rule; no reset flow | app/auth/passwords.py:262-295, app/routes/account.py:95-97 |
| L2 | Low | `hmac.compare_digest` raises `TypeError` on non-ASCII input → unhandled 500 on the cron and unsubscribe endpoints | app/routes/email.py:201-202, app/services/email.py:105-109 |
| L3 | Low | Hard-coded promo codes in source (`BETAUSER` grants 7 days of access to anyone who reads the repo) | subscription_common.py:203-212 |
| L4 | Low | Stripe: no webhook route (signature verifier exists but is dead code); client-supplied `success_url`/`cancel_url`; customer lookup lists only 100 customers; checkout stacks a second 7-day trial | subscription_common.py:273, 283-315, 374; app/routes/subscription.py:42-62 |
| L5 | Low | Verbose error bodies: Supabase/Gemini/Anthropic error text and `str(e)` returned to clients | app/routes/ai.py:84,127; app/routes/resume.py:62,91; app/routes/account.py:117,142 |
| L6 | Low | Calendar sync: client `googleEventId` interpolated raw into the Google API path; malformed `dateISO` → unhandled 500 | app/routes/google_oauth.py:626-627, 646 |
| L7 | Low | Web tokens in `localStorage` with no CSP → any XSS is a 30-day session theft | frontend/src/api/tokenStore.ts:15-36 |
| L8 | Low | Dependency hygiene: `PyPDF2` 3.0.1 is end-of-life; every pin is `>=`; `numpy` is imported but not in `requirements.txt`; git remote URL embeds a PAT | requirements.txt, app/services/matching.py, app/services/recall_query.py:307 |
| L9 | Low | `update_user_data` read-modify-write on the whole `data` jsonb → concurrent saves silently lose one key | app/core.py:845-854 |
| L10 | Low | Full `users` rows (`select=*`, incl. `password_hash` and `google_calendar_refresh_token`) flow through many code paths that only need identity | app/core.py:651-654, 828-834, 857-864; app/routes/google_oauth.py:587 |

Checked and found acceptable (details in the last section): JWT algorithm pinning / `none` / expiry / secret-unset behaviour; argon2 usage; PostgREST filter construction on every route; IDOR on every owned-data route; snapshot-filename traversal; agent log reading; subprocess argument handling; unsubscribe HMAC and cron-secret compare; HTML escaping in e-mail templates; OAuth `state` CSRF; `.env` handling and committed logs; docx XXE.

---

## Critical

### C1 — Unauthenticated, unmetered, attacker-prompted paid LLM proxy

**Where.** `app/routes/ai.py:148-161` and `:164-175`:

```python
@router.post("/api/messages")
def handle_messages(request: Request, raw_body: bytes = Depends(raw_body_dep),
                    user: AuthedUser = Depends(get_optional_user)):
    userid = user.id if user else None
    reason = subscription_block_reason(userid)
    if reason:
        return json_error(402, reason)
    ...
    if GEMINI_API_KEY:
        return _proxy_to_gemini(raw_body, ip, userid)
```

`get_optional_user` never 401s (`app/auth/dependencies.py:229-238`), and `subscription_block_reason("")` returns `None` at `app/deps.py:106-108` (`if not userid: return None`). So with no `Authorization` header at all the request reaches `_proxy_to_gemini`, which forwards whatever the caller sent:

```python
# app/routes/ai.py:72-82
system = payload.get("system", "") or ""
user_content = payload.get("userContent", "")
use_web_search = bool(payload.get("useWebSearch"))
text, usage = call_gemini(system, user_content, GEMINI_API_KEY,
                          use_web_search=use_web_search,
                          max_tokens=_clamped_gemini_max_tokens(payload.get("maxTokens")), ...)
```

and, for Anthropic (`ai.py:99-110`):

```python
body = {"model": CLAUDE_MODEL, "max_tokens": _clamped_max_tokens(...),
        "system": [{"type": "text", "text": system, ...}],
        "messages": [{"role": "user", "content": user_content}]}
if use_web_search:
    body["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
```

Note that on the Anthropic path there is no `max_uses` on the web-search tool (contrast `check_deadlines.py:256-260`, which caps it), and `urlopen(req)` at `ai.py:122` has no timeout.

**Exploit.** Anyone on the internet (no account, no trial) `POST`s `{"system": "<anything>", "userContent": "<anything>", "useWebSearch": true, "maxTokens": 8000}` to `https://highschoolwingman.com/api/messages` or `/api/messages-claude` and gets back a general-purpose Gemini/Claude completion with live web search, billed to the owner's keys. The only limits are the output ceilings (`MESSAGES_MAX_TOKENS_CEILING = 8000`, `CLAUDE_MAX_TOKENS_CEILING = 8000`, `app/config.py:70,90`); input size is unbounded (M4), so a single request can carry hundreds of thousands of input tokens. Per-search fees (`WEB_SEARCH_PRICE_PER_SEARCH = 14/1000`) apply on Gemini and Anthropic bills per search too. Cost accounting records this as "unattributed" but nothing stops it. A secondary consequence: every product prompt (profile chat rules, "never invent a date", eligibility guard) is bypassable by an account holder because the prompt is authored client-side, and the mock-mode classifier (`classify_feature`) is trivially gamed.

**Fix.** (1) Require a valid access token AND an active subscription on both proxies (`require_subscription`), and keep signed-out reachability only when the server is in mock mode (`if not GEMINI_API_KEY:` branch can stay open, the live branch cannot). (2) Move the system prompts server-side: accept a `feature` enum from the client, look up the prompt on the server, and refuse unknown features — this also makes `classify_feature` exact instead of a substring guess. (3) Put a hard per-user, per-day token/search/dollar budget in front of the call (the `user_costs` rollup already computes the number; read it back before calling). (4) Set `max_uses` on the Anthropic web-search tool and a `timeout` on the request.

**Tradeoff.** Moving prompts server-side is an M8 (marquee) change and means prompt edits ship with the backend rather than the bundle; the mock-mode click-through for signed-out demo use goes away unless explicitly gated on "no key configured". A per-user budget will occasionally stop a legitimate heavy user mid-session — pick a ceiling well above the measured per-user daily spend and surface it in the UI.

---

## High

### H1 — Google sign-in links to an existing account by e-mail without `email_verified`

**Where.** `app/routes/google_oauth.py:174-182`:

```python
record = get_user_by_google_id(google_id)
if not record:
    by_email = get_user_by_email(email)
    if by_email:
        # Google has verified this address, so link it to the existing account.
        record = get_user(by_email["userid"])
        _users_request("PATCH", query_patch, data={"google_id": google_id})
```

The comment asserts verification, but `profile.get("email_verified")` is never read (`:166-171` only take `sub`, `email`, `given_name`, `family_name`).

**Exploit.** Google issues `email_verified: false` for accounts whose address was never confirmed (non-Gmail addresses added to a Google account, some Workspace configurations). An attacker creates a Google account with the victim's e-mail address unverified, signs in with Google on Wingman, and the code links their `google_id` to the victim's password account and returns a full session for it. All of the victim's profile, tracker and calendar are now the attacker's, and the victim's password still works, so nothing looks wrong.

**Fix.** Refuse the link (and the pending-signup path) unless `profile.get("email_verified") is True`; when it is false, fall through to `json_error(400, ...)` rather than linking. Optionally require the user to be already signed in (or to enter their password once) before attaching a Google identity to an existing account.

**Tradeoff.** A handful of users with unverified Google addresses will be unable to sign in with Google and will have to use the password form; that is the correct outcome.

### H2 — `app_redirect` allowlist is a prefix match → sign-in token exfiltration

**Where.** `app/routes/google_oauth.py:86-92`:

```python
_ALLOWED_APP_REDIRECTS = [p.strip() for p in os.environ.get("GOOGLE_APP_REDIRECTS", "").split(",") if p.strip()] or _DEFAULT_APP_REDIRECTS

def _is_allowed_app_redirect(uri: str) -> bool:
    return bool(uri) and any(uri.startswith(prefix) for prefix in _ALLOWED_APP_REDIRECTS)
```

and the callback sends the one-time login token there (`:199-204`):

```python
redirect_entry = g._google_login_redirects.pop(req_state, None)
if redirect_entry:
    dest = redirect_entry["app_redirect"]
    return RedirectResponse(f"{dest}{sep}google_token={urllib.parse.quote(token)}", status_code=302)
```

`render.yaml:48` says `GOOGLE_APP_REDIRECTS` is set in the dashboard to "the static site's origin", i.e. a value such as `https://highschoolwingman.com`.

**Exploit.** With prefix `https://highschoolwingman.com`, both `https://highschoolwingman.com.evil.tld/` and `https://highschoolwingman.com@evil.tld/` pass `startswith`. The attacker mails a student `https://highschoolwingman.com/api/auth/google/start?app_redirect=https://highschoolwingman.com.evil.tld/`. The student sees the real Google consent screen, signs in, and is 302'd to `evil.tld` with `?google_token=…`. The attacker calls `GET /api/auth/google/session?token=…` within 5 minutes and receives the student's access + refresh tokens (`:208-233`). The same prefix bug applies to the calendar `app_redirect` (`:319-322`, `:404-407`) but that only leaks `calendar_connected=1`. The defaults are affected too (`http://localhost:8081` matches `http://localhost:8081.evil.tld`), and `exp://` accepts any host.

**Fix.** Parse `app_redirect` with `urllib.parse.urlsplit` and compare `(scheme, host, port)` exactly against the allowlist; for custom schemes compare the scheme only when the value has no network location an attacker could point elsewhere. Reject values containing `@`. Better still: stop sending the token in a query string at all — put it in a URL fragment (`#google_token=`) so it never reaches a server log or Referer, and keep the exact-origin check.

**Tradeoff.** Exact-origin matching means every dev port and the production origin have to be listed explicitly; that is one line in the dashboard.

### H3 — Login/register rate limiter is keyed on the socket peer

**Where.** `app/auth/ratelimit.py:302-313` (the docstring's own claim, "on Render the app sees the real client"), `app/deps.py:79-80` (`request.client.host`), `app/routes/account.py:88` and `:164`, and `render.yaml:18` (`uvicorn app.main:app --host 0.0.0.0 --port $PORT` with no `--forwarded-allow-ips`).

Installed uvicorn 0.52 defaults to `proxy_headers=True` and `forwarded_allow_ips=None` → `127.0.0.1` unless `FORWARDED_ALLOW_IPS` is set. Render's load balancer connects from the private network, not loopback, so `X-Forwarded-For` is ignored and `request.client.host` is the proxy's address for every visitor.

**Exploit.** Two branches, both bad. (a) As deployed: `login_limiter = RateLimiter(10, 5 * 60)` is one bucket for the whole user base — an attacker sends 10 `POST /api/login` bodies and every student is locked out of sign-in for 5 minutes, forever, for free; 10 registrations per hour is the site-wide signup capacity. (b) If someone "fixes" it by setting `FORWARDED_ALLOW_IPS=*`, the limiter keys on an attacker-supplied `X-Forwarded-For` and is bypassed. Either way, the brute-force protection on the password endpoint is not real in production.

**Fix.** Set `FORWARDED_ALLOW_IPS` to Render's proxy range (or `*` only if Render guarantees it overwrites the header — verify by logging `request.client.host` once), and key the limiter on `(client_ip, userid)` for login so one IP cannot lock out other users. Move the limiter to a shared store if a second worker is ever run (the file already documents the per-process limitation).

**Tradeoff.** Trusting forwarded headers is only safe when the platform strips client-supplied ones; confirm that with a single test request before enabling it.

### H4 — No per-user spend cap on any paid path

**Where.**
- `app/routes/opportunities.py:91-92`: `force = ... request.query_params.get("refresh") ... ; if not force and deadlines.deadline_cache_is_fresh(...)` — `refresh=1` bypasses the 7-day cache on every call, then `:112-113` runs `check_deadline_one(opp, ANTHROPIC_API_KEY, want_requirements=True)` (measured ≈ $0.07 per verified check per CLAUDE.md).
- `app/routes/matching.py:281-349`: `/api/match` runs an embedding call plus an eligibility Gemini call at `ELIGIBILITY_MAX_TOKENS = 8000` per request.
- `app/routes/opportunities.py:206-234`: `/api/opportunities/{id}/action-items` regenerates (Claude, web_fetch) whenever `action_items_checked_at` is stale or absent; user-submitted rows are never stamped, so every call on such a row pays.
- Both AI proxies (C1) for authenticated users.

`record_user_cost` / `record_interactive_cost` (`app/core.py:195-277`, `431-533`) only *record* — nothing reads the total back to refuse a call.

**Exploit.** One $9.99 trial account (or a 7-day free trial that costs nothing) loops `GET /api/opportunities/<id>/deadline?refresh=1` across the catalog: 1,300 rows × $0.07 ≈ $90 per pass, repeatable. `/api/match` at a few cents per call is unbounded too. The only throttle is the process-wide Gemini sleep (M5), which is a denial-of-service, not a budget.

**Fix.** A per-user daily budget enforced before each paid branch (deadline check, action items, match, proxies), backed by the existing `user_costs` rollup; a per-user cooldown on `refresh=1` per opportunity (e.g. one forced re-check per row per hour); and a global daily circuit breaker on total spend that flips the paid branches to cached/mock.

**Tradeoff.** Budgets need a UI message ("you've used today's AI allowance") and a way for the operator to raise them; the circuit breaker turns a billing incident into a degraded app, which is the intended failure direction.

### H5 — Catch-all static route serves the repo

**Where.** `app/main.py:128-129`:

```python
_DENY_EXT = {".py", ".pyc", ".pyo", ".log", ".sql", ".ps1", ".md", ".txt", ".sh"}
_DENY_NAMES = {".env", "agent_settings.json"}
```

`_resolve_static` (`:175-190`) rejects dot-segments and `agent_logs`, then serves any other file under `REPO_ROOT`; `serve_static` (`:208-230`) is mounted on `/{full_path:path}`.

**Exploit.** Tracked files that are servable today at `https://highschoolwingman.com/<path>`:
- `ops/admin_console.html` — the full operator console UI, naming every `/api/agents/*` endpoint and its parameters (its calls 404 on Render, but it is a map of the internal control plane).
- `logic_map.html` — internal architecture/decision documentation.
- `opportunities.json` (1,330 rows) and `Opportunities.xlsx` — the entire catalog as a bulk download (the product's core asset; the API pages this and strips fields).
- `review_check_dry_run_20260818.json`, `review_check_dry_run_20260819.json`, `scrape_review_*.json` — agent output including review verdicts and rejected rows.
- `eval/golden_profiles.json`, `eval/*.csv`, `eval/golden_scorecard.html`, `eval/*.mjs` — synthetic profiles (checked: not real students) and the LLM-judge scorecard; still internal.
- `tests/fixtures/*.json`, `tests/fixtures/sitemaps/*` — synthetic (checked).
- `frontend/package-lock.json`, `frontend/package.json`, `pyproject.toml`, `render.yaml`, `frontend/tsconfig.json`, `frontend/scripts/verify.ts`, `hub_pilot_national.json`, `hubs_seattle.json`, `test_resume.docx` (checked: placeholder text), `LICENSE`.
- On Render, `frontend/node_modules/**` exists after `npm ci` (`render.yaml:17`) and every non-dot, non-`.md` file in it is served (`/frontend/node_modules/expo/package.json` etc.) — harmless content, but an exact dependency inventory for targeting.
- Locally (`python server.py` binds `0.0.0.0`): `discovered_leads.jsonl`, any `*_dry_run_*.json` snapshot, and any JSON dropped in the root are served to the LAN.

Nothing here exposes credentials or student PII today — that is luck (a `users_db.json` at the root would be served; only `.env` and `agent_settings.json` are denied by name), and the deny-list has to be right for every future file.

**Fix.** Replace the deny-list with an allow-list: exactly `terms.html`, `privacy.html`, `about.html`, `walkthrough.html`, `styles.css`, `favicon.svg` (plus the dist bundle). Everything else 404s.

**Tradeoff.** Adding a new public static page becomes a one-line code change instead of a file drop — which is the point.

---

## Medium

### M1 — Blind SSRF through user-submitted opportunity URLs

**Where.**
- Anyone (no token) can insert a row: `app/routes/resume.py:94-96` uses `optional_subscribed_user`; `app/services/resume.py:223-233` stores `"url": url` verbatim and returns `generated_id`.
- Any subscriber can then trigger a check on that row: `app/routes/opportunities.py:77` (`get_opportunity_for_deadline_check(opp_id)` has no `is_active` filter — by design, documented) → `check_deadline_one(opp, …, want_requirements=True)`.
- That runs `research_deadlines(..., discover=sitemap_common.discover_candidate_pages)` (`check_deadlines.py:882-887`), and `sitemap_common.default_fetch` (`:92-106`) does a plain `urllib.request.urlopen` against `origin + "/robots.txt"` and the probed sitemap paths, where `origin` is derived from the stored URL's host (`:133-139`). No private-IP / loopback / link-local filter, redirects followed (urllib default), 2 MB read.
- The action-items endpoint (`app/routes/opportunities.py:206-234` → `generate_action_items.process_one:455-470` → `find_program_sources`) reaches the same discovery hook.
- The page *content* fetches go through Anthropic `web_fetch` (`source_capture.py:150-167`), which runs on Anthropic's network, not yours — so the direct-from-server surface is the sitemap/robots probe, and any redirect it follows.

**Exploit.** Submit `{"name":"x","url":"http://10.0.0.5:8080/"}` unauthenticated, then from a trial account call `GET /api/opportunities/us…/deadline?refresh=1`. The server GETs `http://10.0.0.5:8080/robots.txt`, `/sitemap.xml`, `/sitemap_index.xml`, … from inside Render's network. Responses are not echoed, but timing and the resulting `status`/dates can leak reachability, and a redirect target on the attacker's host can steer follow-up GETs. Also a cheap way to make the server hammer a third party's host.

**Fix.** A single `url_is_public(url)` gate (resolve the host; reject loopback, RFC1918, link-local, ULA, `0.0.0.0/8`, metadata ranges; re-check after each redirect by using a custom opener) applied at submission time in `insert_user_opportunity` AND at fetch time in `sitemap_common.default_fetch`, `page_text._fetch_urllib`, `url_repair._fetch`, `mailing_list_common.fetch_page`. Require authentication on the submission route (M10).

**Tradeoff.** Legitimate programs never live on private addresses, so the false-positive cost is nil; the resolver check adds one DNS lookup per fetch.

### M2 — Refresh tokens: no rotation, no reuse detection

**Where.** `app/auth/tokens.py:407-431` mints a 30-day refresh token; `app/routes/auth.py:26-53` accepts it, checks `ver` against `users.token_version`, and returns a *new* pair via `login_response(record)` while the presented refresh token stays valid until its own `exp`. Client `logout()` (`httpClient.ts:375-377`) only calls `forgetSession()` — no server call — and `logout-all` is a separate, opt-in route.

**Exploit.** A refresh token copied from `localStorage` on a shared school computer (L7), from a proxy log, or from a compromised device keeps minting fresh access tokens for 30 days, and continues to work after the student presses "log out". There is no signal that two parties are refreshing the same lineage.

**Fix.** Rotate on use: give each refresh token a random `jti`, store the current `jti` per user (one column, `users.refresh_jti`), accept only that one, and treat presentation of a superseded `jti` as theft → bump `token_version`. Make the client's `logout()` call `logout-all` (or a single-device revoke).

**Tradeoff.** One DB write per refresh (every ~45 min per active user) and one column migration; two devices on one account will each hold their own lineage only if you store a small set rather than one value.

### M3 — Access token in a URL

**Where.** `frontend/src/api/httpClient.ts:659-664` builds `/api/auth/google/calendar/start?token=<access JWT>`; `app/routes/google_oauth.py:301-303` reads it from `request.query_params`.

**Exploit.** The full 45-minute bearer token lands in Render's access logs, in the browser history, and in any corporate/school proxy log. Anyone who can read those replays it against every gated route.

**Fix.** Mint a single-use, 60-second handoff nonce server-side (`POST /api/auth/google/calendar/handoff` with the bearer header → `{nonce}`) and put the nonce in the URL instead, exactly as the sign-in flow already does with `_mint_google_token`.

**Tradeoff.** One extra round trip before the redirect.

### M4 — No request-size limits; Anthropic proxy has no upstream timeout

**Where.** `app/deps.py:58-61` (`raw_body`) and `:28-36` read the entire body into memory; nothing sets a limit. `app/routes/ai.py:122` `urllib.request.urlopen(req)` — no `timeout` (the Gemini path inherits 120 s from `gemini_common`). `app/routes/resume.py:36` parses the whole upload in memory; `app/core.py:1078-1118` accepts `context` dicts of any size into a 5,000-entry buffer. `/api/data/save` stores any-size `value` into the row's jsonb (`app/core.py:845-854`).

**Exploit.** Forty concurrent `POST /api/messages-claude` calls against a slow upstream pin every default threadpool worker forever (sync handlers run in the anyio threadpool, default capacity 40), and every sync route on the service stops answering. Separately, a few 500 MB bodies exhaust the free-tier instance's memory.

**Fix.** A body-size middleware (reject `Content-Length` > 1 MB for JSON, > 10 MB for the resume route), `timeout=` on every `urlopen`, and a cap on `context` size and `data` value size.

**Tradeoff.** Pick the JSON limit above the largest legitimate `hs-tracker-data` (CLAUDE.md cites 37 KB as ordinary).

### M5 — Process-wide 5-second Gemini sleep inside the web server

**Where.** `gemini_common.py:163` (`DEFAULT_MIN_DELAY_SECS = 5`), `:183` (only `GEMINI_MIN_DELAY_SECS` overrides it — not set in `.env`, not set in `render.yaml`), `:221-234` (`_enforce_rate_limit` sleeps under a module global), `:447` (called on every `call_gemini`). `app/` never calls `set_min_delay` (grep confirmed). Callers on the request path: `app/routes/ai.py:77`, `app/routes/matching.py:333`, and the embedding path.

**Exploit.** The whole server can start at most one Gemini call every 5 s. Because `/api/messages` is unauthenticated (C1), an attacker sending one request every second keeps the sleep permanently armed: every student's profile extraction, ranking, and match waits in a queue behind it, and the threadpool fills with sleeping workers. CLAUDE.md notes this exact hazard for `check_deadlines.py` ("a process-wide delay would make one user's request block on another's") but the Gemini path was not given the same treatment.

**Fix.** Set `GEMINI_MIN_DELAY_SECS=0` for the web service (and pass `timeout=` explicitly), or add a `rate_limit=False` parameter to `call_gemini` used by `app/`. Keep the 5 s floor for the batch scripts. Also note `_acquire_web_search_lock` (`:267-330`) writes a lockfile with the web server's PID on the first web-search call and holds it for the process lifetime — harmless on Render, but locally it makes any concurrently run offline agent fail.

**Tradeoff.** None for correctness: the 429 mitigation was for sustained batch throughput, which interactive traffic does not resemble; if 429s appear, add a retry with backoff rather than a global sleep.

### M6 — Promo redemption race

**Where.** `app/routes/subscription.py:143-161`:

```python
used = list(record.get("promo_codes_used") or [])
if code in used: return json_error(400, ...)
...
update_subscription(userid, {"subscription_status": status, "subscription_end_at": new_end, "promo_codes_used": used})
```

Two round trips with no conditional write.

**Exploit.** Fire N parallel `POST /api/subscription/redeem-promo {"promo_code":"BETAUSER"}`; all read `used == []`, each computes `extend_from(current_end, 7)` from the same stale row and writes — the last writer wins, but each request sees a fresh `record` afterwards, and interleavings where a later reader sees an earlier writer's `subscription_end_at` compound the grant (7 → 14 → …). Free access, indefinitely, from a 7-day code.

**Fix.** Make the PATCH conditional: `PATCH users?userid=eq.X&promo_codes_used=not.cs.{CODE}` (PostgREST `cs`/contains) and treat zero rows updated as "already used". Same pattern for `bump_token_version` if it ever matters.

**Tradeoff.** None; one filter on the existing request.

### M7 — Account enumeration

**Where.** `app/routes/account.py:174-175` returns `404 "No account found with that user ID."` and `:181-182` `401 "Incorrect password."`; `:113-114` and `:118-121` return distinct 409s for taken userid vs. taken e-mail; `/api/subscription/validate-promo` (`:189-195`) reveals whether *this* account used a code (token-gated, fine).

**Exploit.** Enumerate valid userids (and, via register, which e-mails belong to students) at 10 attempts per 5 minutes per bucket — and with H3 the bucket is global and trivially exhausted anyway. Given the population (minors) a list of "these e-mails have accounts" is itself sensitive.

**Fix.** One message for both login failures ("Incorrect user ID or password"). For registration, keep the userid conflict (it is chosen by the user and visible) but respond to a taken e-mail with the same 200 the success path returns and an e-mail saying "you already have an account" — or at minimum rate-limit per e-mail.

**Tradeoff.** Slightly worse UX on a mistyped userid; standard trade.

### M8 — Ops console gated only by "peer is loopback"

**Where.** `ops/admin.py:18-27`:

```python
_LOCAL_HOSTS = ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")
def require_local(request):
    client = request.client.host if request.client else ""
    if client not in _LOCAL_HOSTS: raise HTTPException(403, ...)
```

Mounted only when `WINGMAN_ENABLE_OPS` is set (`app/main.py:109-114`); `server.py:32-33` sets it whenever `RENDER` is absent.

**What happens if it is enabled on Render.** With uvicorn's default proxy handling, `request.client.host` is Render's proxy address → every request 403s (console unusable, not exposed). If `FORWARDED_ALLOW_IPS=*` is set (the plausible "fix" for H3), `client.host` becomes the attacker-controlled `X-Forwarded-For: 127.0.0.1` and the *entire* console — subprocess launches that spend money (`/api/agents/run`, `/api/agents/tools/run`), the roster with names/e-mails of minors (`/api/agents/metrics`, `/api/agents/user-costs`), catalog activation, e-mail test sends to arbitrary addresses (`/api/agents/emails/test`) — is open to the internet with no password. The same holds locally the moment the dev server is exposed through any localhost tunnel (ngrok, VS Code port forwarding, Cloudflare tunnel): the tunnel's peer is 127.0.0.1.

**Fix.** Add a second factor that does not depend on network position: a random `WINGMAN_OPS_TOKEN` checked in a header on every ops route (the pattern `EMAIL_CRON_SECRET` already uses), and refuse to mount ops at all when `RENDER` is set regardless of `WINGMAN_ENABLE_OPS`. Also drop `ops/admin_console.html` and `logic_map.html` from the public static route (H5).

**Tradeoff.** One token in `.env`; the console's fetch calls need to send it.

### M9 — PII of minors in stdout and in an undocumented `conversations` table

**Where.**
- `app/core.py:545-571` `log_conversation` writes `userid`, `client_ip`, the bot's question and the student's free-text answer to a `conversations` table whose only definition is a comment at `:142-151` — no `*_schema.sql`, no `enable row level security`, so it is the one user-data table that may be readable with the anon key if it was created from that comment.
- `app/core.py:568,570` print `userid=`; `app/services/email.py:382,398,612,659` print full e-mail addresses (`[MOCK EMAIL] {kind} -> {email}`, `Lifecycle email … -> {email} failed`); `app/services/mailing_list.py:160` prints `{userid}/{opp_id}`; `app/routes/account.py:126` prints the userid on hash-upgrade failure. Render retains stdout.
- The chat logging attributes "mock" and "live" turns alike and stores the answer verbatim (`log_conversation_async` at `:573-584`), so the most sensitive free text in the product (a minor describing themselves) is duplicated outside the RLS-protected `users` row.

**Exploit.** Anyone with log access (Render dashboard collaborators, a leaked log export) gets a timeline of which minors did what and where they connect from; if `conversations` has no RLS and the anon key is ever exposed, chat transcripts are readable.

**Fix.** Add `conversations_schema.sql` with RLS on / no policies (matching every other user table) and confirm the live table's RLS state in the dashboard; hash or drop `client_ip`; drop the userid/e-mail from log lines (log the `email_sends.id` instead); consider whether `conversations` is needed at all now that `user_activity`/`user_events` exist.

**Tradeoff.** Debugging a failed send needs a row id instead of an address — the row carries the address.

### M10 — Unauthenticated write into the catalog table

**Where.** `app/routes/resume.py:94-96` (`optional_subscribed_user`), `app/services/resume.py:169-258`. Each call reads the whole catalog for dedupe (`catalog_dedupe_rows`, `:261-284`, ~1,400 rows across two pages) and then inserts a row with attacker-controlled `name`, `url`, `summary`, `important_dates`, `category`, `submission_payload`.

**Exploit.** A script fills the review queue with thousands of rows (each also costs two 1,000-row Supabase reads — a cheap amplification against the free tier), buries real submissions, and seeds URLs that the operator's free agents (`check_links.py`, `url_repair.py`, `find_mailing_lists.py`) will later fetch from the operator's machine (an SSRF against the operator's LAN, and a way to get the operator to POST to arbitrary provider endpoints if a recipe is later verified carelessly). The stored `name`/`summary` are rendered in the admin console.

**Fix.** `require_subscription` on the route (the client only calls it from the authed Quest Log anyway), a per-user daily cap on submissions, length limits on the text fields, and the `url_is_public` gate from M1.

**Tradeoff.** The docstring's "soft auth for provenance" rationale is lost; a signed-out user cannot add opportunities — they cannot use the Quest Log signed out either.

### M11 — No security headers; CORS `*`; state cookies not `Secure`

**Where.** `app/main.py:37-47` (`allow_origins=["*"]`, `allow_headers=["*"]` unless `CORS_ALLOW_ORIGINS` is set — not set in `render.yaml`), `:64-87` (the only headers added are cache-control). `app/routes/google_oauth.py:124-125` and `:336-337` set `google_oauth_state` / `google_calendar_oauth_state` with `httponly=True` but no `secure=True` (SameSite is Starlette's default `lax`, which is adequate for the top-level redirect).

**Exploit.** No CSP means any XSS (e.g. via a future rich-text field) exfiltrates the `localStorage` tokens (L7); no `X-Frame-Options`/`frame-ancestors` allows clickjacking of the paywall/consent screens; no HSTS on the app origin means a first HTTP visit can be downgraded. CORS `*` is not itself exploitable (auth is a bearer header, `allow_credentials=False`) but is unnecessary in prod where app and API share an origin.

**Fix.** Add a headers middleware: `Strict-Transport-Security`, `Content-Security-Policy` (start with `default-src 'self'; frame-ancestors 'none'` plus what the Expo bundle needs), `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY` (except `/walkthrough.html`, which the landing page iframes — give it `frame-ancestors 'self'`). Set `CORS_ALLOW_ORIGINS` to the exact origins. Set `secure=True` on both cookies when the scheme is https.

**Tradeoff.** CSP needs one iteration against the exported bundle's inline scripts/fonts (`expo export` inlines `@font-face`); use `report-only` first.

---

## Low

### L1 — Password handling edges
- `app/auth/passwords.py:262-295`: rows that have not logged in since Phase 2 still hold the bare client SHA-256 (`_SHA256_HEX`), which is password-equivalent on the wire (`httpClient.ts:342` sends `sha256(password)` unsalted). A DB read of those rows lets an attacker log in as them without cracking anything. Fix: a one-off migration that wraps every legacy value in argon2 (`argon2(sha256hex)` verifies identically), and consider adding a server pepper.
- `app/routes/account.py:95-97`: no minimum password length or complexity; `passwordHash` is accepted as any string. Fix: validate on the client (length) and reject on the server if `passwordHash` is not 64 hex chars.
- No password-reset flow exists (grep for `reset`/`forgot` in `app/` is empty), so there is no reset-token issue — but a student who forgets a password has no recovery path except Google linking (H1).
- argon2 parameters are library defaults (argon2-cffi 23.1: t=3, m=64 MiB, p=4) — adequate.

### L2 — `compare_digest` on non-ASCII → 500
`app/routes/email.py:202` `hmac.compare_digest(supplied, EMAIL_CRON_SECRET)` and `app/services/email.py:109` `hmac.compare_digest(expected, str(token))`: Python raises `TypeError` for non-ASCII `str` operands (verified), so `X-Cron-Secret: é` or `?t=é` returns an unhandled 500 instead of 403/400. Fix: compare `.encode("utf-8")` bytes.

### L3 — Promo codes in source
`subscription_common.py:203-212` hard-codes `BETAUSER` (grants 7 days), `FREEMONTH`, `WELCOME10`. Anyone who can read the repository (it is on GitHub; visibility unknown) can extend access for free, and with M6, repeatedly. Fix: move codes to a table with per-code redemption counts and expiry.

### L4 — Stripe integration gaps (dormant today)
- No webhook route exists; `verify_stripe_webhook_signature` (`subscription_common.py:374-399`) is unused, so `subscription_status` can never become `active`/`past_due` from Stripe events — when Stripe is enabled this will be the first bug.
- `app/routes/subscription.py:42-43,61-62` pass client-supplied `success_url`/`cancel_url` to Stripe unchecked → a Stripe-hosted open redirect (only self-inflicted, since the route is token-gated).
- `get_or_create_customer` (`:273`) lists 100 customers and scans metadata; past 100 customers it silently creates duplicates.
- `create_checkout_session` (`:293`) sets `trial_period_days = TRIAL_DAYS` — a second free week at checkout on top of the in-app trial, and `FREEMONTH` adds 30 more days of trial rather than a coupon.
- `subscription_block_reason` fails open on any Supabase exception (`app/deps.py:109-112`) — documented and reasonable, but it means a Supabase outage is also a paywall outage; log it so it is visible.

### L5 — Verbose error bodies
`app/routes/ai.py:84,127` relay the upstream provider's error body and status verbatim (Gemini/Anthropic error JSON, quota messages, model names); `app/routes/resume.py:62,91` return `str(e)`; `app/routes/account.py:117,142` and many others return `f"Could not reach Supabase: {e}"`; `app/routes/matching.py:345` `f"Matching failed: {e}"`. None include keys, but they name infrastructure and library internals. Fix: log the detail, return a generic message with a correlation id.

### L6 — Calendar sync input handling
`app/routes/google_oauth.py:646` interpolates the client's `googleEventId` unquoted into the Google API path (`.../events/{google_event_id}`); a value with `/` or `?` re-targets the request within the user's own calendar-scoped token — no cross-user impact, but quote it. `:626-627` `year, month, day = date_iso.split("-")` raises on a malformed `dateISO` → unhandled 500 for the whole sync.

### L7 — Web tokens in `localStorage`
`frontend/src/api/tokenStore.ts:15-36` stores access, refresh and the cached identity in `localStorage` on web (SecureStore on native). Combined with no CSP (M11) and 30-day non-rotating refresh tokens (M2), any XSS is a month-long account theft. Fix priority follows M2/M11; an `httpOnly` refresh cookie is the stronger option if you accept a CSRF token.

### L8 — Dependency hygiene
- `requirements.txt` pins every package with `>=` only; a deploy picks up whatever is newest.
- `PyPDF2>=3.0` resolves to 3.0.1, the project's final release (renamed to `pypdf`); it receives no fixes. Switch to `pypdf` with a pinned version.
- `numpy` is imported at module top in `app/services/matching.py` and `app/services/recall_query.py:307` but absent from `requirements.txt` — a fresh Render build will fail to import `app.routes.matching`. Not security, but it is a deploy-time outage.
- Installed locally: fastapi 0.141.1, uvicorn 0.52.4, PyJWT 2.13.0, argon2-cffi 23.1.0, python-docx (lxml 6.1.1). Frontend: expo 57, react 19.2.3, react-native 0.86.2.
- `git remote -v` shows the origin URL carries an embedded GitHub PAT (local config, not in the tree — memory notes already flag rotating it).

### L9 — Lost updates on `users.data`
`app/core.py:845-854` reads the whole jsonb, sets one key, PATCHes the whole blob. Two saves from one student (profile refresh and a tracker edit within one RTT) drop one of them silently. Not an attacker issue, but it is data loss for minors' work. Fix: PostgREST `PATCH` with a jsonb concatenation via a small RPC, or per-key columns.

### L10 — Over-broad row reads
`get_user` (`app/core.py:651-654`, `select=*`) is used by `update_user_location`, `update_subscription`, `bump_token_version`, the calendar sync (`google_oauth.py:587`) and the mailing-list subscribe (`mailing_list.py:203`); each pulls `password_hash` and `google_calendar_refresh_token` into memory to answer questions about other columns, and `ops/core._fetch_all_accounts` (`:1051-1068`) does it for the whole roster. Nothing leaks today, but every new consumer of `record` is one `json.dumps` away from doing so. Fix: use `get_user_account`/narrow selects and never pass a full `record` to code that only needs `userid`.

---

## Checked — no finding

Each item below was verified in code; listed so the reader knows it was covered.

**1. JWT.** Algorithm pinned on both encode and decode (`app/auth/tokens.py:404` `algorithm=JWT_ALGORITHM`, `:439` `algorithms=[JWT_ALGORITHM]`, `JWT_ALGORITHM = "HS256"` at `config.py:209`), so `alg: none` and RS/HS confusion are rejected by PyJWT. Secret comes only from the environment with no default (`config.py:208`); unset → `AuthConfigError` → 503, never a guessable key (`tokens.py:391-395`, `dependencies.py:221-223`). `exp` is enforced by PyJWT with no leeway; `iat` set; `type` claim checked so a refresh token cannot be used as access (`:444-448`). Refresh checks `token_version` from the DB (`auth.py:46-48`); `logout-all` bumps it (`core.py:867-892`). No `iss`/`aud` claims — acceptable for a single-audience service. Tokens are not written to server logs except via M3.

**2. Passwords.** argon2 via `PasswordHasher()` defaults; `verify_password` uses `_ph.verify` and `check_needs_rehash`; legacy compare is `hmac.compare_digest` (`passwords.py:294`). Issues are in L1/M7/H3.

**5. PostgREST injection.** Every user-controlled value reaches a filter through `urllib.parse.urlencode` and only ever in the value position of `eq.` (`core.py:652,680,704,720,730`, `deadlines.py:277,349`, `mailing_list.py:183`, `email.py:817`), where commas/parens are literal. `in.(...)` sites sanitise: `_safe_ids` whitelist (`deadlines.py:295-307`, capped at 300), `re.sub(r"[(),*%]", "", …)` (`mailing_list.py:95,112,246`). The one `or=(...)` with `ilike` is ops-only and strips `,()*%` (`ops/core.py:1844-1849`). `EMAIL_RE` (`config.py:194`) forbids `, ( ) " '`. The `source` filter in `ops/core.py:1626-1627` is `eq.`-positioned and localhost-only. No injection found.

**7. Ops file/subprocess handling.** Snapshot commit: `dryrun_common.resolve` (`:207-223`) rejects anything whose basename differs from the input and requires a match against a fixed glob, then `os.path.join(REPO_DIR, file_name)` — traversal is blocked. Agent log read (`ops/core.py:3525-3545`) serves an in-memory ring buffer keyed by a name validated at `ops/admin.py:158-161`. Seed-query runs (`:4257`) only open files whose stamp was found on disk. Subprocesses use `Popen(args_list, cwd=root)` with no shell (`:3570-3573`, `:3966-3969`); user strings become single argv elements. A value beginning with `--` can smuggle a flag into the child's argparse (`build_tool_args` `inspect` ids at `:3864` split on whitespace), which can only change/abort that operator-initiated run.

**8. Google OAuth (beyond H1/H2).** `state` is `secrets.token_urlsafe(24)`, stored in an `HttpOnly` cookie and compared to the callback query (`google_oauth.py:103,124,132-135`); the calendar flow keeps a separate state map (`:314-323,344-349`). The redirect URI is derived from `Host` but Google validates it against the registered list, so a spoofed `Host` fails at Google. No `id_token` parsing — identity comes from the userinfo endpoint using an access token obtained with the client secret, which is sound. Handoff tokens are single-use, 5-minute, `secrets.token_urlsafe(32)` (`services/google_oauth.py:33-47`).

**10. Email.** Cron secret: header not query, `compare_digest`, fails closed when unset (`routes/email.py:196-203`). Unsubscribe: HMAC-SHA256 of `unsub:{userid}` under `JWT_SECRET`, `compare_digest`, per-user (`services/email.py:97-109`). Templates escape every interpolated value with `html.escape(..., quote=True)` (`email_templates.py:85-89`) including names, orgs, labels, URLs; tracked-item URLs are filtered to `http(s)` (`deadline_alerts.py:256-265`). Subjects are sent as JSON fields to Resend's API, so CRLF cannot become SMTP headers. The `_page` HTML in `routes/email.py:260-273` interpolates only constants.

**11. IDOR.** Every owned-data route takes identity from the verified token and ignores body userids: `/api/data/save|load`, `/api/account/location` (`user_data.py:222-280`), `/api/mailing-list/subscriptions` and `/subscribe` (`mailing_list.py:351-370` → scoped `userid=eq.`), `/api/calendar/sync` (`google_oauth.py:563-569`, token per userid), subscription routes, `/api/events` (`events.py:304-320`), resume/LinkedIn (`resume.py:18-27,65-73`). Service-role bypasses RLS everywhere, so the app-layer check is the only control — and it is present on each. Every user-data table's DDL enables RLS with no policies (`user_costs`, `user_activity`, `user_events`, `user_metrics_daily`, `scraper_seeds`; `users` per `migrate_users_to_supabase.py:31`); the exceptions are `conversations` (M9) and the tables without a schema file in the tree (`agent_runs`, `deadline_check_log` — RLS state unverified). The `opportunities` catalog is read with the anon key restricted to `is_active=true` per config comment; not verifiable from the repo.

**14. Secrets.** `.env` loaded by a stdlib parser (`config.py:13-36`); `.gitignore` covers `.env*`, `.claude/settings.local.json`, `users_db.json`, `agent_logs/`, `.test-account.json`. The tracked `server_debug.txt` / `server_output.txt` / `server_full_output.log` / `server_stderr.log` contain only the startup banner (no keys, no e-mails, no bodies). `find_contact_emails_full_run.log` holds 531 *program* contact addresses (not users) and is `.log`, so not served. `refresh_run.log` is clean. `frontend/app.json` and `.claude/launch.json` hold no secrets. No API key, JWT secret or password is hard-coded; the only baked-in credentials are the promo codes (L3).

**15. Resume upload.** python-docx's parser is `XMLParser(remove_blank_text=True, resolve_entities=False)` (verified in the installed package), so XXE is off; PyPDF2 and python-docx operate on `BytesIO`, no temp files; extension check on the filename only; the model receives at most 2,000 characters (`services/resume.py:104`). Remaining risks are size/zip-bomb DoS (M4) and the EOL parser (L8).

**16. Dependencies.** See L8.

**17. PII logging.** See M9. The `/api/agents/metrics` roster (`ops/core.py:1199-1203, 1292`) returns names and e-mails of every account; it is ops-only and gated as M8 describes — its sensitivity is why M8 matters.

**18. Concurrency.** Register: DB unique index on `lower(email)` (`users_email_unique_schema.sql:38`) and the `userid` PK close the check-then-insert race, and `create_user` maps `23505` to `DuplicateEmail` (`core.py:796-797`). Promo: M6. Refresh reuse: M2. `email_sends` claim-before-send with a three-column unique constraint is correctly designed. `record_interactive_cost` / `record_user_cost` read-modify-write under a process lock — a multi-worker deploy would under-count, not over-charge.

**13. CORS / cookies.** No session cookies exist; the only cookies are the two OAuth `state` values (M11 for `Secure`). CORS `*` with `allow_credentials=False` is not exploitable on its own.

**6. Static route traversal.** `_resolve_static` and `_resolve_dist` (`main.py:140-190`) normalise the path and require it to stay under the root; dot-segments (`..`, `.env`, `.git`) are rejected before touching the filesystem. The exposure (H5) is the deny-list policy, not traversal.
