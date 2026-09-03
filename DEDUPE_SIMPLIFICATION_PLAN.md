# Dedupe Simplification Plan — one verdict per row

**Status:** proposal, awaiting Shama's sign-off. Nothing here is built.
**Companion:** the *current-state* map is the "Opportunity Dedupe Flow" artifact
(`e143179b`). This document is the *target state*.

**Operator directive that motivates it (2026-09-02):** "I get two duplicate logics dumping
all of their brains in my queue … I need one simple solution that gives me one label — how
confident is it — and if it is confident, its best guess at the duplicate, so I can look at
the suggested duplicate and judge." Priority stated: **catching duplicates matters more than
avoiding siblings**; duplicates are expected to grow faster than siblings.

---

## 1. The problem is surfacing, not detection

The detection is good. `dedupe_confidence.py` already fuses every signal — URL proof, name
relation, hard-field conflict, shared acronym, same-institution guard, embedding cosine — into
**one tier per pair** (`proof / confident / adjudicate / sibling / hint / none`). The single
label the operator is asking for is *already computed*.

What's broken is that **four writers each dump raw hints into the queue**, and the console
shows the pile instead of the fused verdict:

| # | Writer | When | Writes |
|---|---|---|---|
| 1 | `url_dedupe.find_duplicates` (Track A) | insert | `dup_candidates` entries (rungs 1–5) |
| 2 | `combined_reader.dedup_hint` (Track B) | insert (page fetched) | `dup_candidates` entries `via: content-embedding` |
| 3 | `dedupe_queue.py --write` | offline rescan of **pending+flagged** | more `dup_candidates` back-links |
| 4 | `find_catalog_dups.py` → console "Scan" | catalog-wide | `moderation_status = suspected_duplicate` |

Three storage fields carry dedupe state (`dup_candidates` list, `quality_flags`,
`moderation_status`), two offline agents overlap, and the console renders **both** a
per-candidate back-link list (`dupeBackLinks()`) **and** a separate `suspected_duplicate`
slice. A reviewer opens one row and reads N hints from up to 4 engines. That is the mess.

There is also a real coverage hole the mess hides: **no offline pass runs embedding
candidate-generation over the whole ACTIVE catalog.** `dedupe_queue.py` only re-embeds
*pending/flagged* rows; `find_catalog_dups.py` scans all rows but with *heuristic* candidate
generation only (exact-URL, same-domain name ratio, ≥2 shared tokens). A same-program pair
whose names barely overlap — e.g. `SYCCL (Stony Brook)` vs `Summer Youth Camp for
Computational Linguistics (SYCCL)`, name-similarity **0.143**, one shared token, Jaccard
0.125 — becomes a candidate in *neither*, so the confidence engine never judges it and two
live rows for the same program both surface to students. (Confirmed live 2026-09-02.)

---

## 2. The single principle

> **Every queue row carries exactly one resolved dedupe verdict:
> `{ confidence, best_guess_duplicate_of }` — one label, one suggested survivor.**

The three logics still run. They stop being four *writers to the queue* and become **candidate
sources feeding one resolver**. The reviewer sees one line, looks at the one suggested
duplicate, and decides.

---

## 3. Target architecture — five collapses

| Today | Target |
|---|---|
| 4 writers append raw hints | **1 resolver** picks the single strongest pair and writes **one** verdict |
| 3 fields (`dup_candidates`, `quality_flags` dupe entries, `suspected_duplicate`) | **1 field**: `dup_verdict` |
| 2 offline agents (`dedupe_queue` + `find_catalog_dups`) | **1 agent** (`dedupe.py`) over pending **and** active rows |
| console: back-link list + separate flagged slice | **1 line**: confidence chip + "Suspected duplicate of ‹name ↗›" + actions |
| 6 internal tiers leak to the reviewer | **3 labels**: Certain / Likely / Possible |

### 3.1 The one resolver — `resolve_dup_verdict(row, catalog, cosine_lookup) -> Verdict | None`

Pure, free (uses stored vectors; no new embedding). One place, reused by insert-time and the
offline agent so they cannot drift — the same role `deadline_write_decision` and
`action_items_write_decision` already play elsewhere in the repo.

1. **Gather candidate PAIRS for `row`** from all three sources (union, deduped):
   - Track A: `url_dedupe.find_duplicates(..., include_weak=False)` (exact-URL, apply-URL,
     sub-page, same-domain name≥0.82).
   - Track B: embedding nearest-neighbours from the stored `dedupe_vector` index above a floor.
     Over the **whole active catalog** — this is the SYCCL fix, and it is a *lookup*, not a
     re-embed, so it stays free.
2. **Judge each pair** with `dedupe_confidence.classify_rows(row, other, cosine)` — the
   existing fused engine, unchanged.
3. **Return the single winner**: highest tier, then highest cosine. Surface only
   `proof / confident / adjudicate / hint`. **`sibling` and `none` return `None`** — the row gets
   *no* `dup_verdict`, so it never appears in the Duplicate queue at all. That suppression is the
   main noise cut.

`Verdict = { confidence, duplicate_of, name, url, reasons[], cosine, computed_at }`.

### 3.2 The one field — `dup_verdict` (jsonb, nullable)

Holds the single resolved `Verdict` (or null). Replaces the dedupe role of `dup_candidates`,
`quality_flags` dupe entries, and `suspected_duplicate` as a *pointer* status.
`duplicate_of` (the **confirmed** survivor, set only by a human) is unchanged.
See §6 for the DDL-vs-no-DDL decision.

### 3.3 The one agent — `dedupe.py` (and the two-queue boundary)

**There are TWO queues, and the same `resolve_dup_verdict` fusion feeds both — but from
different triggers over different populations. They must not cross.**

| Queue | Population | Trigger | Cosine source |
|---|---|---|---|
| **Review queue** | pending, `is_active=false` | **insert-time**, per new row (Phase 4) | the new row is embedded **live** (paid) — it has no stored vector yet |
| **Duplicate queue** | active, `is_active=true` | the **offline/ad-hoc** agent (batch) | **lookup** of stored `dedupe_vector`s — free |

The offline agent (this section) merges `dedupe_queue.py` + `find_catalog_dups.py`, reads
**ACTIVE rows only**, resolves each against the active catalog, and publishes to the **Duplicate
queue**. It must **never** write a verdict onto a pending review-queue row — that is deduped at
insert (Phase 4), and an offline scanner reaching into the review queue is the cross-queue
contamination this revamp removes. Read-only without `--write`. Free — string logic + one **bulk
matmul** over stored vectors (measured 2026-09-02: 1,690 active rows in **33 s**, ~31 s of it the
read). `--preview` prints the tier histogram and would-be writes. `dedupe_resolve.py` is the
Phase-1 seed of this agent.

### 3.4 The one console surface

Per queue row, exactly one line:

> `[Likely]` **Suspected duplicate of** *Summer Youth Camp for Computational Linguistics ↗*
> &nbsp; `[Confirm duplicate]` `[Not a duplicate]`

- **Confirm** → `moderate(status="duplicate", duplicate_of=verdict.duplicate_of)` (existing
  path; the best guess pre-fills the target, so the reviewer usually never opens the search
  modal).
- **Not a duplicate** → clears `dup_verdict` **and records a dismissal** so the next rescan
  does not resurface the same pair (see §5, risk 2).
- Delete `dupeBackLinks()` (the per-candidate list) and the separate `suspected_duplicate`
  tab/slice. A "has a suspected duplicate" filter replaces the tab.

### 3.5 The confidence mapping

| Engine tier | Operator label | Meaning |
|---|---|---|
| `proof` | **Certain** | redirect/canonical equal — could auto-merge (§6) |
| `confident` | **Likely** | high cosine + name same + no field conflict + same institution |
| `adjudicate` / `hint` | **Possible** | a qualifier/field discrepancy, or moderate cosine |
| `sibling` / `none` | *(no dupe line)* | different program, or not similar enough |

---

## 4. What each current piece becomes

- `dedupe_confidence.py` — **kept as-is.** It is the fused engine; the whole plan leans on it.
- `url_dedupe.py` — **kept**, still the exact-URL hard-reject at insert and a Track-A candidate
  source. Stops being a *queue writer*; its hints feed the resolver instead of `dup_candidates`.
- `combined_reader.dedup_hint` — **kept** as a Track-B candidate source; stops writing
  `dup_candidates` directly.
- `dedupe_queue.py`, `find_catalog_dups.py` — **merged into `dedupe.py`**, then deleted.
- `queue_flags.dedupe_candidate` / `merge_candidates` — **retired** (no more multi-entry list).
- `dup_candidates` field, `suspected_duplicate` status — **migrated out** (§5 phase 5).
- The paid LLM adjudicator — **out of scope**, unchanged. `adjudicate` maps to "Possible" and
  stays a human call; wiring it to the judge is a separate, paid (M8/M9) decision.

---

## 5. Phased delivery (each phase shippable, low-risk)

1. **Resolver + field, shadow mode.** Add `resolve_dup_verdict` and `dup_verdict`; the offline
   agent writes it **alongside** the existing surfaces. Nothing read yet. Verify against the
   live queue that the one verdict matches what a human would pick. *No behavior change.*
2. **Console reads `dup_verdict`.** Render the single line; hide `dupeBackLinks` and the flagged
   slice behind a flag. Reviewer now sees one label + best guess. Old data still present.
3. **Merge the agents** into `dedupe.py`; cover active rows (closes the SYCCL hole); deprecate
   the two old agents. Console "Scan" calls the new one.
4. **Insert-time producers stop appending hints.** Feeders insert clean; an inline
   `resolve_dup_verdict` call stamps the one verdict at insert (same resolver), so a fresh row
   is annotated immediately without a pile.
5. **Remove legacy.** Drop `dupeBackLinks`, the `suspected_duplicate` slice,
   `dedupe_candidate`/`merge_candidates`; migrate any live `dup_candidates` to a resolved
   `dup_verdict`; delete the two old agents.

Roll back at any phase by re-showing the old surface — the old fields are untouched until 5.

---

## 6. Decisions (locked 2026-09-02)

1. **Storage:** ✅ **new `dup_verdict jsonb` column** (one manual SQL DDL step, matching the
   repo's other schema files). `dup_verdict_schema.sql`.
2. **Siblings:** ✅ **config cosine floor** — hide `sibling` below `SIBLING_SHOW_FLOOR`, surface
   it above as a **Possible** line tagged "looks similar, fields differ." Default floor `0.93`.
3. **Auto-merge:** ✅ **off — manual.** `proof`/`confident` still just *label* (Certain/Likely);
   nothing auto-merges. Revisit once FP rates are measured against confirm/reject history.
4. **LLM adjudicator:** ✅ **off — manual.** `adjudicate` maps to a free **Possible** line the
   reviewer judges; the paid judge stays unwired. Keeps the whole pipeline free and non-marquee.
5. **Label calibration** (surfaced by Phase-1 shadow run, decided 2026-09-02): ✅ **leave as-is
   — conservative labels.** Shadow mode showed obvious duplicates whose two copies have *drifted
   metadata* (e.g. the two NYU GSTEM rows disagree on a hard field) get tiered `sibling` and so
   read **Possible** rather than **Likely**. We are NOT tuning the engine or the resolver for
   this: the sibling cosine floor (decision 2) already rescues these from being hidden, they
   still surface with the correct best-guess target, and a conservative label is the honest one.
   Revisit only if the queue proves it needs finer triage.

### Phase 1 — DONE (2026-09-02)

Built: `dup_verdict.py` (pure resolver, 8 tests green), `dup_verdict_schema.sql` (run),
`dedupe_resolve.py` (shadow runner, free, ~33s). Shadow result over 1,690 active rows: **58
rows carry one verdict** (0 certain / 2 likely / 56 possible, 30 sibling-surfaced); **SYCCL is
now caught** (`ec17185 ↔ ec18702`), the pair `find_catalog_dups` was structurally blind to.
Nothing wired to the app/console and no other column touched — verdicts written to `dup_verdict`
in shadow mode for inspection. Phase 2 (console reads it) is next and depends on nothing further.

### Phase 2 — DONE (2026-09-02)

Console **Duplicate queue** (the `flagged`/active-rows slice) now renders one `dup_verdict` line
per row (confidence pill + best-guess survivor link + reasons/cosine/sibling note) via
`dupVerdictLine()`. Backend: the `flagged` slice is driven by `dup_verdict` (OR legacy
`suspected_duplicate`); `clear_dup_verdict` + `POST /api/agents/pending/clear-dup-verdict` back the
**"not a duplicate"** action; **Confirm** takes the survivor from `dup_verdict.duplicate_of`. The
**review queue is untouched** (keeps legacy `dupeBackLinks` until Phase 4). Verified live: 58 rows
render, no console errors, 225 tests green.

### Phase 3 — DONE (2026-09-02)

The **one agent**. `dedupe_resolve.run()` is the unified ad-hoc detector (active rows only →
Duplicate queue), with **change-only writes** (a re-scan PATCHes only the rows whose verdict
differs — 9, not 1,686). The console **"Scan for duplicates"** button now runs it in one step
(`POST /api/agents/duplicate-scan` → `ops.core.scan_catalog_duplicates`, offloaded to a
threadpool), replacing the old two-step report+flag modal. `find_catalog_dups.py` and
`dedupe_queue.py` are marked **deprecated** (kept on disk for the Phase-5 cleanup; the SYCCL blind
spot they carried is closed by the resolver's embedding candidate-gen). Verified live: scan
returns `scanned 1686 / with_verdict 50 / changed 9 / wrote 9`; flagged slice reconciled to 50,
all verdict-backed; console functions load clean. Phases 4 (insert-time unification) and 5 (delete
legacy) remain.

---

## 7. Non-goals / guardrails

- **Not marquee as scoped:** no prompt changes, no new paid calls (embeddings are stored;
  cosines are lookups). Auto-merge (decision 3) and the LLM adjudicator (decision 4) would each
  need their own sign-off before enabling.
- **Discard almost nothing, explain everything** still holds: the resolver *suppresses a dupe
  line*, it never deletes a row. Sibling/None rows still enter the queue as normal pending
  items. The one hard reject (exact URL + agreeing name) is unchanged.
- **Best-guess never auto-acts.** Confirm/Not-a-duplicate stay human clicks; the verdict only
  pre-fills the target.
