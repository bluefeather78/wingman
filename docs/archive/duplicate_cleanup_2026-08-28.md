# Duplicate cleanup worksheet — 2026-08-28

> **STATUS: Sections A + C APPLIED 2026-08-28.**
> - **A (19)** marked `duplicate` via `/api/agents/pending/moderate`, each with an `A1`/`A2`
>   `moderation_reason`; survivors verified still active.
> - **C (14)** parked for review: deactivated + set `pending_review` (endpoints) and a
>   `Section C: …` note appended to each `quality_flags` (direct PATCH — `quality_flags` has
>   no ops-endpoint writer). All 14 now show in the default review queue with the pill.
>   A1 survivors ec17695 / ec18692 were deliberately left active to anchor their duplicates.
> - **B** unchanged — genuine keep-all shared portals, nothing to do.


Source: `wingman/find_catalog_dups.py` run (1871 rows, 1323 active). Resolve via the admin console's
**Duplicate** / **Reject** actions (never SQL DELETE — deletes need tombstones for
`url_dedupe` to keep blocking re-submission). Rule used to pick the survivor: keep the
incumbent **active, lower-id** row, EXCEPT where its name is a bare-institution junk name and
the twin carries a real program name — then keep the real-named row.

Everything already sitting at `[duplicate]` / `[rejected]` in the report is **already
resolved — no action**. Only rows still visible to students (ACTIVE, or `pending_review`) are
listed here.

---

## A. Apply now — live duplicates, clear survivor

Mark each **loser** `duplicate` with `duplicate_of = survivor` (the Duplicate modal). The
loser goes inactive; the survivor is untouched.

### A1. Same-URL, same program (identical or renamed)

| Survivor (keep) | Loser (mark duplicate_of survivor) | URL / note |
|---|---|---|
| ec17751 Artificial Intelligence Academy | **ec18786** (identical) | summer.georgetown.edu/programs/SHS31/artificial-intelligence-academy |
| ec18663 Kenyon Review Young Writers Workshops | **ec18693** (identical) | kenyonreview.org/event/young-writers-summer-residential-workshops |
| ec17231 Summer Seminar | **ec18757** United States Naval Academy Summer Seminar | usna.edu/Admissions/Programs/NASS.php |
| ec17695 Accelerated Learning Program (ALP) | **ec18774** Badger Accelerated Learning Program (ALP) | precollege.wisc.edu/alp — see C-note: 18774 also collides with ec18761 |
| **ec18772** University of Wisconsin-Madison Engineering Summer Program | ec18049 University of Wisconsin-Madison (junk name) | engineering.wisc.edu/engineering-summer-program — survivor swapped to the real-named row |
| ec18113 Fleischer Scholars Summer Program | **ec18213** W. P. Carey School of Business (junk name) | wpcarey.asu.edu/high-school/fleischer-scholars-summer-program (ec18217 already duplicate) |
| ec18692 Immerse Education Academic Insights | **ec18782** Academic Insights Program | immerse.education/pathways/academic |

### A2. CMU pre-college — 12 `pending_review` rows from the pre-fix hub run

Each duplicates an existing ACTIVE CMU row at the identical URL (the run your notes record as
"12 of 14 were pages the catalog already held"). Mark each `duplicate_of` its active twin
(or Reject — either keeps the URL blocking re-submission).

| Survivor (active) | Loser (pending_review) | Program |
|---|---|---|
| ec17500 | ec18787 | AI Scholars |
| ec17501 | ec18799 | Summer Session |
| ec17502 | ec18788 | Architecture |
| ec17503 | ec18789 | Art |
| ec17504 | ec18790 | Computational Biology |
| ec17505 | ec18791 | CS Scholars |
| ec17506 | ec18792 | Design |
| ec17507 | ec18793 | Drama |
| ec17508 | ec18796 | Music |
| ec17509 | ec18797 | National High School Game Academy |
| ec17510 | ec18798 | Summer Academy for Math & Science (SAMS) |
| ec17511 | ec18800 | Writing & Culture |

**19 rows total in section A.**

---

## B. Leave alone — genuine shared portals (KEEP ALL)

One URL legitimately backing several distinct programs. Do **not** consolidate.

- `uta.edu/.../summer-camps/high-school` — 12 distinct UTA camps
- `stemacademy.oregonstate.edu/high-school-summer-camps` — 6 distinct camps
- `spicestanford.smapply.io` — 5 distinct SPICE programs
- `anderson.ucla.edu/.../high-school-summer-discovery` — 4 academies (differ by `#fragment`)
- `apply.ncsu.edu/register/precollege-program` — 3 distinct programs

Already-resolved same-URL groups (both rows `rejected`) need nothing: Girls Who Code,
ISYM flute, carlos.emory, scripps.

---

## C. Needs human judgment — same program at DIFFERENT URLs (Cut 2)

Cannot be auto-resolved: pick the canonical page, mark the other `duplicate_of` it, or keep
both if they are genuinely different offerings. These are exactly what **Fix 1** now attaches
as `dup_candidates` at scrape time going forward, but these existing pairs predate it.

| Both ACTIVE | Same-name | Decide |
|---|---|---|
| ec17559 / ec17561 | Science of Smart Cities (NYU) | two nyu.edu paths — keep the real program page |
| ec17299 / ec18565 | UC San Diego Sports Medicine Summer Academy | researchscholars vs extendedstudies |
| ec17697 / ec18762 | Badger Summer Scholars | precollege.wisc.edu two paths |
| ec18761 / ec18774 | Badger Accelerated Learning Program | +18774 already in A1 vs ec17695 — resolve the 3-way together |
| ec18646 / ec18692 | Immerse Education Academic Insights | /summer-schools vs /pathways/academic (18692 also survivor in A1) |
| ec18769 / ec18771 | Badger Summer Music Clinic: Mini Music Clinics | near-identical paths |
| ec17601 / ec18033 | Terp Young Scholars | 18033 is a sub-page (`/how-program-works`) of 17601 |
| ec17533 / ec17541 | Coding for Game Design (NYU) | steinhardt vs nyu.edu/admissions |

**NOT duplicates** (listed by the report but genuinely distinct — do nothing): Georgetown
1-Week vs 3-Week Medical Academy; the bare "University of X" / "OSU" / "UCSF" pairs (Tulane,
UTEP, UTSA, Utah, Tufts, Swarthmore, Bentley, NC State) — different programs sharing a junk
institution name. Fix 3 stops these from being flagged in future scrapes.

---

### Two 3-way tangles to resolve as a unit
- **precollege.wisc.edu ALP**: ec17695 (`/alp`), ec18774 (`/alp`), ec18761 (`/accelerated-learning-program`). Keep one, duplicate the other two into it.
- **immerse.education Academic Insights**: ec18692 (`/pathways/academic`), ec18782 (`/pathways/academic`), ec18646 (`/summer-schools`). Keep ec18692, duplicate 18782 and 18646.
