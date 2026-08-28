# Wingman — Matching Flow UX Requirements

*A brief for Claude Design. Self-contained. Describes the interaction and visual
requirements for Wingman's opportunity-matching flow — the experience only. Backend scoring,
prompts, and data pipelines are out of scope and referenced only where they shape the UI.*

---

## Product in one paragraph

Wingman helps high schoolers find and track extracurricular opportunities (summer programs,
internships, research/academic competitions, conferences, journals, volunteering). The
matching flow's job: turn a big catalog (~1,300 real opportunities) into a **small, curated
shortlist that feels chosen for this specific student** — and help them discover kinds of
opportunities they wouldn't have searched for. The audience is teenagers; many are minors.

## The one idea that governs every screen

**This is CURATION, not search.** The end state is a **shortlist of ≤10, each one a
fantastic fit the student can actually do** — never a long, filterable results page the
student wades through. Every screen should feel like a knowledgeable mentor handing over a
short, considered list, not a database returning rows.

## Design system to honor

Wingman has an existing design system — **"BENTO & POP"** (bold, rounded, playful-but-clean
cards with soft shadows and a confident pop of color; branded, warm, teen-facing). Match it.
Reuse its tokens, type, card language, and the branded tab names — **Home Base, My Vibe,
Fresh Finds, Quest Log** (never Home/Profile/Search/Tracker). The matching flow lives under
**Fresh Finds**. Mobile-first: the app is Expo React Native for web + iOS + Android, so every
layout must work on a phone first and scale up.

---

## Core UX principles (apply to every screen)

1. **Curation feel over search feel.** ≤10 results, each visibly "for you." No infinite
   scroll, no "1,247 results," no raw filter sidebar as the primary interaction.
2. **Ask only what narrows THIS student's pool.** Questions are adaptive — the student is
   asked a thing only when it actually discriminates among their real remaining matches.
   Never a fixed questionnaire.
3. **Two kinds of questions, felt differently:**
   - **Narrowing questions** (constraints — grade, location, cost, availability) *cut* the
     pool. The student should feel the list get shorter and more relevant.
   - **Refining questions** (preferences — subjects, activity type, work style) *reorder*
     the list. They never remove anything, only bring better fits to the top.
   The student never needs the words "filter" or "preference," but the two must **feel**
   different (one shrinks, one re-sorts).
4. **Never a dead end.** Every narrowing step shows a live count and an easy way to relax or
   go back. A choice that would empty the list offers to loosen it instead of showing zero.
5. **Nothing new is ever locked out.** New opportunities and off-the-beaten-path great fits
   always have a path to the student. Serendipity is designed in, labeled, and intentional.
6. **Honest, never alarming.** Where a fact is uncertain or a caveat applies (estimated
   dates, "confirm eligibility," "local program"), say so plainly and calmly — a small,
   neutral flag, never a scary warning.
7. **Transparent + reversible.** The student sees *why* something is shown, can disagree
   ("not interested"), and can see/edit what we remembered about them.
8. **Respect the minor audience.** Sensitive questions (citizenship, gender/demographic) are
   optional, skippable, and clearly explained ("so we don't show you things you can't apply
   to"). Skipping never hides opportunities and never feels punitive.

---

## The full flow — screen by screen

### 0. Entry (under Fresh Finds)

- **First-time (enough profile):** the flow starts automatically toward a shortlist; the
  student shouldn't have to hunt for a "search" button.
- **First-time (thin/empty profile):** a warm prompt to add a few things first, with a clear
  CTA to the profile (My Vibe). Encouraging, not a wall.
- **Returning:** land the student back **on their shortlist**, not on a hero telling them it
  exists. Offer "refine" and "start fresh," and surface anything **new since last time** (see
  §4).

### 1. The progressive funnel (the centerpiece)

A short, delightful sequence of adaptive questions that narrows the pool to a shortlist.
Requirements:

- **One question per step, lightweight** — big tappable chips / cards, not form fields. Feels
  like a friendly conversation, not intake paperwork.
- **A live "matches" counter** that visibly updates as the student answers a narrowing
  question (e.g. "~60 matches" → "~30 matches"). This is the core feedback that makes the
  funnel feel alive. Preference/refining steps update the *order*, so their feedback is
  "we'll rank these for you" rather than a count drop.
- **Adaptive length, with a sense of progress** — typically 3–5 steps, never a fixed long
  quiz. Show progress without promising an exact number (the number of questions depends on
  the student). It must be able to end early ("You're all set — here's your shortlist").
- **The two question types feel different:**
  - *Narrowing:* a decisive choice that shrinks the list. Show the count move.
  - *Refining:* a "what matters more to you" choice that re-sorts. No count drop; reassure
    nothing is lost.
- **Skippable sensitive questions:** citizenship / demographic steps carry a plain "why we
  ask" line and an easy Skip. Skipping must feel safe and lose the student nothing.
- **Relax / back up:** every step is reversible. If a choice leaves very few matches, show
  "This leaves 4 — want to loosen something?" with the loosen action right there. Never show
  a hard zero.
- **Grade + location are the two always-asked** (unless already known) — quick, non-sensitive.
- **Ends by handing off to the shortlist** with a small "here's why these" moment.

Design the **empty/near-empty branch** explicitly — it's the failure mode to avoid.

### 2. "Saved to your profile" transparency

- When answers are captured, reassure the student they're remembered ("Added to your
  profile — we won't ask again"). Lightweight, non-blocking.
- A path to **review/edit** later (see §5). The student should trust that answering isn't a
  one-way trapdoor.

### 3. The curated shortlist (≤10)

The payoff. It must read as *chosen*, not *returned*.

- **≤10 cards, premium and scannable.** Each card: opportunity name, organization, a **short
  personalized "why you" reason written in second person** ("Great fit for your robotics
  build"), a type badge (Summer Program / Internship / Competition / …), and 2–3 key facts
  (cost, format, timing).
- **A fit signal per card** — e.g. a "strong match" vs "worth a look" distinction — shown as
  a calm badge, not a number/score.
- **Exploration picks are labeled, not hidden.** 2–3 slots may be deliberate stretch/adjacent
  fits ("A stretch pick," "Outside your usual — but worth a look"). Label them so serendipity
  reads as intentional and interesting, not as an error. This is a signature Wingman moment —
  make it feel like a mentor saying "have you considered…".
- **Honest flags, calm styling:** "Boston-area students" / "requires Algebra II — confirm on
  the site" / "dates estimated from last year." Small, neutral, informative.
- **Per-card actions:** Save / Track (into Quest Log), **Not interested** (see §6), and
  Apply / Learn more.
- **Ordering** communicates the curation: strongest + most-relevant first, exploration picks
  woven in or in their own labeled slot(s).
- Design for **1 great match** and **10 great matches** — the layout must feel right at both
  ends, and must never look sparse or like a failed search when short.

### 4. Surfacing new & noteworthy

- A clear way for **genuinely new opportunities** (newly added to the catalog) to reach the
  student even if they're outside expressed preferences — e.g. a "New for you" strip or a
  labeled section. Respects hard constraints (won't show a 9th grader a seniors-only program)
  but ignores mere preference-ranking, so students keep discovering.
- On return visits, gently flag "X new since you last looked."

### 5. Manage what we remembered (criteria)

- A place (likely in My Vibe) to **see and edit** the two kinds of remembered info, and the
  distinction should be legible to the student:
  - **Hard requirements** ("things that rule opportunities out" — grade, location, budget,
    eligibility) — editable, with a way to **relax** any of them.
  - **Preferences** ("things you're into" — subjects, the kinds of work you like) — editable,
    framed as steering, not gating.
- **Grade freshness:** because grade changes yearly, design a light, occasional
  "Still in 10th grade?" re-confirm — not nagging.

### 6. Disagree / "not interested"

- A **low-friction** control on each card. Optionally ask a quick "why?" (wrong subject / too
  advanced / not my thing / location) — one tap, never required.
- Reassure it **improves future matches** ("Got it — we'll show you fewer like this"). It must
  never feel like it deletes something permanently or scolds the student.

### 7. States to design

- **Curating / loading:** a single warm "finding your matches…" state (the app is doing one
  thing from the student's side — don't expose profile-load vs search as two spinners).
- **Near-empty / relax:** the funnel's "this leaves very few — loosen something?" moment.
- **Genuinely nothing (rare):** honest, encouraging, with a next step (broaden, or check back).
- **Error (catalog failed to load):** reassure it's on our side, their profile/Quest Log are
  safe, offer retry.
- **Profile too thin:** the "help me help you — add a bit more" state with a CTA to My Vibe.

---

## Content & tone

- Warm, direct, teen-appropriate, second person ("you"/"your"). A knowledgeable older friend,
  not a career counselor or a corporate portal.
- Every reason and label is **specific** — names a real detail of the student or the program,
  never generic filler.
- Buttons say exactly what happens ("Add to Quest Log," then a "Added" confirmation).
- Errors explain what happened and the way forward, no apologies-as-filler.

## Constraints & non-negotiables

- **Mobile-first**, works on phone → tablet → desktop (Expo RN web + native).
- **Honor BENTO & POP** and the branded tab names.
- **Accessibility:** legible contrast, visible keyboard focus, respects reduced-motion,
  tappable targets sized for thumbs.
- **Minor-safe:** sensitive questions optional + explained; nothing coerced.
- **No dark patterns:** no fake scarcity, no manipulative "only 2 left," no forced sensitive
  disclosure.

## Explicitly out of scope for this design

The matching math, the LLM prompts, the eligibility parsing, the catalog agents, and any
data schema. Design the **experience**: the funnel interaction, the shortlist, the surfacing
of new/exploratory picks, the manage-criteria and disagree affordances, and all the states.

## What we most want from the design

1. A funnel that feels like a **short, delightful conversation** with a live sense of the
   list narrowing to something great — not a quiz or a filter panel.
2. A shortlist that feels **curated and personal**, where each card earns its place and the
   **exploration picks are a highlight**, not noise.
3. A visual language that makes **narrowing vs. refining** feel different without jargon, and
   makes **honest caveats** calm rather than alarming.
