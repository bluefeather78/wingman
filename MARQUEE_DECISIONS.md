# Marquee decisions — do not change without Shama's approval

These are load-bearing design decisions that must **never** be changed, reverted, or quietly
softened without an explicit, extensive discussion with the operator (Shama) in chat and a clear
"yes, change it."

This file exists because one of them was violated silently. `refresh_opportunities.py` was built to
fill each opportunity's fields from a **live web fetch** (`use_web_search=True`, prompt: *"YOU MUST
use web_search and web_fetch to verify CURRENT information"*). In an **unrelated commit** titled
*"Implement resume/LinkedIn profile import feature"* (`9efd4c3`) it was flipped to
`use_web_search=False` with a one-line justification (*"metadata extraction doesn't need live
verification, avoids quota contention"*). A later cleanup then rewrote the prompt to *"YOU HAVE NO
WEB ACCESS"* to match the broken state instead of restoring it. The result: the agent answered from
the model's memory and returned blanks for exactly the new/small/local programs it was meant to
enrich — the opposite of the instruction — and nobody re-checked it against the original intent.

## The rules (these bind every Claude session — they are also stated in CLAUDE.md)

1. **Before editing code or config that a marquee entry protects, STOP.** State which entry it
   touches, what you'd change, and why, and get an explicit "yes" from Shama in chat first. Do not
   proceed on a default, an inference, or a plausible improvement.
2. **A marquee change is always its own dedicated commit** whose message names the entry (e.g.
   "MARQUEE M1: …"). It is **never** bundled into an unrelated feature commit — that is precisely how
   the original violation hid.
3. **Each protected code site carries a sentinel comment** (`# MARQUEE M<n>: …`). Seeing that comment
   while editing is the trigger to stop and check this file.
4. **Adding or removing a marquee entry is itself a marquee action** — only Shama decides what is on
   this list. Claude may *propose* entries (marked "proposed" below) but may not treat a proposed
   entry as ratified until Shama says so.
5. When in doubt about whether a change is "fundamental," treat it as marquee and ask. The cost of
   asking is one message; the cost of a silent reversal is what this file documents.

---

## Ratified

### M1 — `refresh_opportunities.py` fills metadata by READING THE LIVE PAGE, never from memory
*Ratified 2026-08-28 by Shama.*

The metadata agent must gather each opportunity's fields (summary, eligibility, cost, price, grade
range, season, location, subject tags, …) by **fetching and reading the program's own page**, not by
recalling from the model's training data. "Fill fresh information for each opportunity" was and is the
instruction. A fetch failure means **skip the row** (leave curated values, don't stamp, retry next
run) — it does **not** mean fall back to memory.

- **Why:** memory-based fill returns blank for new/small/local programs — the exact rows the catalog
  most needs — and silently overwrites curated values with plausible inventions on the rest.
- **Protected sites:** `refresh_opportunities.py` `check_one()` / `build_system()` — the fetch and the
  prompt. Any change back toward `use_web_search=False` / "no web access" / memory-only requires
  re-approval.

---

### M2 — No opportunity is ever auto-activated
*Ratified 2026-08-28 by Shama.*

A person activates every catalog row from the console; no code path sets `is_active = true`
automatically. The one narrow, documented exception is `url_repair --repair-flagged`, which restores
rows a human originally vetted and a machine removed over a link. Do not add another auto-activation
path.

### M3 — No paid agent runs without fresh explicit approval in chat
*Ratified 2026-08-28 by Shama.*

Building UI, a console button, or a script for a run is **not** authorization to trigger it. Each live
run of a money-spending agent needs a fresh "yes" in chat. Traces to a real ~$30 overspend. (This is
about *running*; M9 governs *changing the code that spends*.)

### M4 — The URL of record is never a model-typed or remembered URL
*Ratified 2026-08-28 by Shama.*

A stored opportunity URL must be grounding-resolved and title-proven (`url_validate` / `url_repair`),
never a URL the model typed or recalled. This is the fix for the scraper's measured 26% dead-link
rate, and it is why `refresh_opportunities.py` does not write `url` even under M1.

### M5 — The scraper's phase 1 uses PROSE output with real search; it is never collapsed to one JSON call
*Ratified 2026-08-28 by Shama.*

Measured: prose 4/4 calls searched, JSON 0/4. Do not merge the two phases, and do not make phase 1
return JSON — either change silently collapses the search rate. (Related to M8: these are prompt
strings; changing them is doubly gated.)

### M6 — The 5-second minimum delay between Gemini calls is a floor
*Ratified 2026-08-28 by Shama.*

The fix for this pipeline's repeated HTTP 429s. `--min-delay` may raise it; nothing may lower the
5-second floor.

### M7 — Calendar events go only to the app-created "Highschool Wingman" calendar (`calendar.app.created`)
*Ratified 2026-08-28 by Shama.*

The scope guarantees Wingman can never read or write a student's own calendars. Do not broaden the
scope or add a path that writes to any other calendar.

### M8 — Any prompt sent to a model is marquee
*Ratified 2026-08-28 by Shama.*

**Every prompt anywhere in the system** — system prompts, user-turn text, appended nudges, few-shot
examples, schema instructions — is a marquee surface. Changing the wording or behaviour of a prompt,
adding one, or removing one requires Shama's explicit approval first and its own dedicated commit.

- **Why:** the M1 reversal *was* a prompt change. Prompts are the actual behaviour of the system; a
  quiet reword can flip what an agent does (search vs. recall, extract vs. invent, broad vs. narrow)
  with no code diff that looks dangerous.
- **Scope:** any string that is sent to a model as instruction or context. The main homes today (not
  exhaustive — the rule is about the *kind* of string, not the file list): `scrape_opportunities.py`
  (`RESEARCH_SYSTEM`, `EXTRACT_SYSTEM`, `SEATTLE_ADDENDUM`, the user turns), `refresh_opportunities.py`
  (`build_system`), `check_deadlines.py`, `check_reviews.py`, `generate_action_items.py`,
  `find_mailing_lists.py`, `harvest_names.py` (`_NAME_SYSTEM`), `mine_hub_pages.py`, `source_capture.py`,
  `gemini_common.py` / `claude_common.py` (the appended forced-search and budget instructions),
  `app/services/ai.py` and the interactive proxies in `server.py`, and every prompt in
  `frontend/src/lib/*` (profile chat, ranking, tracker extraction, tags).
- **Not gated:** fixing a typo that cannot change meaning, or changing text that never reaches a model
  (log lines, comments, UI copy). When unsure whether an edit changes meaning, treat it as gated.

### M9 — Any code path that makes a paid API call is marquee
*Ratified 2026-08-28 by Shama.*

**Adding, removing, or changing any call to an API that costs money** is marquee — requires approval
first and its own dedicated commit. This governs the *code that spends*; M3 governs *running* a paid
agent.

- **Why:** the M1 reversal was exactly this — a paid call (`use_web_search=True`) was silently turned
  off. The inverse (turning a paid feature on, raising a search budget, adding a per-row model call)
  is just as consequential and just as easy to bury.
- **Covered, specifically:** flipping `use_web_search` / `max_searches` (Gemini) or `max_uses` /
  `web_fetch` (Anthropic); changing a model pin to a more expensive model; adding a new model call to a
  loop that runs per-row or per-request; changing which provider serves a feature; removing a free
  fast-path (regex/cache/page-in-hand) that currently avoids a call; raising a token ceiling in a way
  that increases spend. The money seams are `gemini_common.call_gemini`, `claude_common.call_claude`,
  `check_deadlines.call_claude`, `source_capture`, `contact_email_common`, the six paid catalog agents,
  and the interactive proxies in `server.py` / `app/services`.
- **Not gated:** a change that only *reduces* spend and cannot change correctness (e.g. tightening a
  cache) is still worth flagging in the commit, but does not need pre-approval. When unsure, ask.

---

## Proposed — awaiting Shama's ratification

None currently. (Claude adds here when it spots something that reads as marquee; Shama ratifies.)

---

## How to add an entry (for Shama)

Tell Claude "make this marquee: …". Claude adds it under Ratified with the date, writes the sentinel
comment at the protected site(s), and never touches those sites again without your explicit yes.
