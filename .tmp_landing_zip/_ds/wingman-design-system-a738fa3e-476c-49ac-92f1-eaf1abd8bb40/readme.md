# Wingman Design System

Design system for **Wingman** ("Highschool Wingman") — a web app that helps high schoolers find and track extracurricular opportunities: summer programs, internships, research competitions, academic competitions, conferences, and journals. It builds a student profile (interests, grade, location, budget), matches it against a catalog of 1,300+ opportunities, and tracks application deadlines on a calendar.

Sole product surface: the Wingman web app (`index.html`). A separate internal `admin_console.html` manages the catalog's background scraping/review agents but is not a student-facing surface and is not recreated here.

## Sources

Built from the attached GitHub repository:
- https://github.com/bluefeather78/wingman (branch `main`)

No Figma file or slide deck was attached. Explore the repo further — especially `index.html`, `styles.css`, `script.js`, and `CLAUDE.md` — for implementation details and product logic beyond what's captured here.

## Naming: "Bento & Pop"

The repo's own `styles.css` header names this style **"BENTO & POP"** — soft white "bento" cards (`.card-soft`, rounded, shadowed, borderless) mixed with hard-edged "pop" elements (`.pop-card`, `.pop-btn` — thick navy borders + offset drop shadows, neobrutalist-adjacent but warm/rounded rather than harsh).

## Components

- `components/buttons/` — Button (primary/secondary/ghost, loading, sizes)
- `components/cards/` — SoftCard, PopCard
- `components/feedback/` — StatusPill, Badge, ProgressTrack, ProgressLegend
- `components/forms/` — TextField, TextArea, Select
- `components/navigation/` — TopNav
- `components/overlays/` — Modal
- `components/chat/` — ChatBubble, ChatStarterButton

**Intentional additions**: no component library or Figma file was provided, so these primitives were authored from the app's own CSS classes (`.pop-btn`, `.card-soft`, `.status-pill`, etc.) and repeated markup patterns in `index.html`/`script.js` — sized to what the app actually uses, not a generic kit.

## Contents

- `tokens/` — colors, typography, spacing, shadows/effects, fonts (all `@import`ed from root `styles.css`)
- `components/` — reusable UI primitives (buttons, pills/badges, cards, inputs, nav, modal, progress, chat bubbles)
- `ui_kits/wingman-app/` — click-through recreation of the Wingman app (login, home, finder, tracker, profile)
- `guidelines/` — foundation specimen cards for the Design System tab
- `assets/` — logo/favicon and inline icon SVGs pulled from the repo

See the CONTENT FUNDAMENTALS, VISUAL FOUNDATIONS, and ICONOGRAPHY sections below.

## Content Fundamentals

**Voice**: warm, direct, encouraging — like a friend who's organized, not a school counselor. Second person ("you"), casual contractions ("Don't have an account?", "Not sure? Take a quick quiz →").

**Playful, game-y labels for serious nav items.** Standard sections get quest/journey metaphors instead of literal names:
- "Home Base" (dashboard), "My Vibe" (profile), "Fresh Finds" (opportunity finder), "Quest Log" (tracker)
- Section headers: "What You're Chasing", "Your Next Moves", "Your Story So Far", "and beyond →"
- Empty/CTA copy leans conversational: "Hey there — ready?", "Here's what we know about you", "I don't have enough yet to match opportunities — help me help you by building your profile."

**Casing**: sentence case for headings and buttons — never Title Case, never all-caps except tiny uppercase eyebrow labels (`text-xs uppercase tracking-wide`, e.g. field labels like "GRADE LEVEL (OPTIONAL)").

**Emoji used sparingly, as functional glyphs, not decoration**: 🚧 (beta/in-progress notices), 📅/📋 (calendar/list view toggle), 📌 (actively tracked), ⭐ (saved for later), 🔬 (research section), ✉️ (email), 👤 (account). Never emoji strings or reaction-style use.

**Direct, honest about limitations**: the beta disclaimer is blunt — "This app is in beta — features are actively evolving and results may occasionally be incomplete or inaccurate." Cost/data caveats in the admin surface are similarly plain rather than glossed over.

**First-person founder voice on About**: the About page is written in first person singular ("I started looking for summer programs...") — a personal, parent-founder origin story, distinct from the product's second-person app voice.

## Visual Foundations

**Two card languages, used deliberately:**
- `.card-soft` — white, no border, 22px radius, soft ambient shadow (`0 2px 18px rgba(15,23,42,.06)`). Used for primary content containers (main sections, modals). Hover state on Home/Profile cards adds a warm cream tint (`#FBF3E9`), not a shadow change.
- `.pop-card` / `.pop-btn` — 2–3px solid navy border (`#1d4e89`) + a hard offset drop shadow (no blur) in the same navy, `cubic-bezier(0.34,1.56,0.64,1)` easing. On hover the element lifts (translate -1/-2px, shadow grows); on press it slams down (translate +2px, shadow shrinks to 1px). Used for buttons, the account drawer, badges, quiz options — anything interactive/tappable.

**Color**: warm cream page background (`#FBF8F3`), white cards. Primary text/headings are navy blue (`#1d4e89`), not black. One dominant accent — orange (`#f4791d`/`#f79256`) — carries every primary CTA and the nav's active-state highlight. A secondary palette (teal `#00b2ca`, mint `#7dcfb6`, peach `#fbd1a2`, indigo `#6366F1`, purple `#6a63e8`, lime `#d7f542`, amber `#f4b400`) is used narrowly for status pills, progress segments, and chat accents — never as competing primary colors.

**Typography**: three-font system. Space Grotesk (bold/extrabold) for all headings via `.font-heading`. Plus Jakarta Sans is the default body font. Poppins shows up specifically inside form fields (textareas, selects) — a deliberately softer, rounder feel for input areas.

**Corner radii**: generous and consistent — pills (999px) for buttons/badges/nav, 22px for soft cards, 16px for form inputs/textareas, 12–16px for smaller pop elements. Nothing sharp-cornered.

**Shadows**: two systems only. Soft ambient (`card-soft`) vs. hard offset "pop" shadows — no shadow in between, no inner shadows, no glassmorphism/blur.

**Backgrounds**: flat color only. No photography, no gradients on page backgrounds, no textures/patterns. The only gradients in the whole product are two dark-navy CTA banners (`linear-gradient(90deg,#101c36,#182750)`) used for high-emphasis "deepen your story" prompts — a deliberately rare, high-contrast treatment reserved for the single most important CTA on a page.

**Animation**: minimal and physical, never decorative. Pop-card/pop-btn hover+press use the bouncy `cubic-bezier(0.34,1.56,0.64,1)` easing described above. A `urgentPulse` keyframe animation (pulsing shadow ring) marks time-sensitive items. Loading states are a simple spinning ring (`animate-spin`), never skeleton screens.

**Hover/press states**: hover = lift + bigger shadow (pop elements) or a cream background tint (soft cards) — never opacity fades. Press = the element visually "presses into" its shadow (translate down/right, shadow shrinks) — a literal physical metaphor, not a color darken.

**Borders**: two border colors only — navy brand (`#1d4e89`) on "pop" branded elements, near-black slate (`#0F172A`) on utility pop elements (chat, nav dropdowns, tracker intake panel). No thin 1px gray borders anywhere; borders are always 2–3px and load-bearing (part of the shape, not a hairline separator).

**Layout**: single-column, max-width-constrained (`max-w-4xl`) centered app shell with a sticky floating pill header. No sidebars. Modals are a single scrolling full-viewport overlay (not double-nested scroll containers) — see the code comment on `.modal-overlay` in the source `styles.css`.

**Transparency/blur**: essentially unused. The only translucency is the modal scrim (`rgba(15,23,42,.55)`) and a subtle badge background opacity (35% on the favicon's yellow accent circle). No frosted-glass panels.

## Iconography

No dedicated icon font or SVG sprite in the source repo. Icons are a mix of:
- **Inline hand-coded stroke SVGs** in the nav (home, profile/search, magnifier, calendar) — 24×24 viewBox, `stroke="currentColor"`, `stroke-width="2"`, rounded caps/joins — visually equivalent to Lucide/Feather-style icons, but authored inline rather than imported from a library.
- **A handful of small flat-color SVG icons** shipped as standalone files: `icon-gear.svg` (settings), `icon-profile.svg` (account), `icon-teacup.svg` (unused/decorative). Copied into `assets/icons/`.
- **Emoji as functional icons** (see Content Fundamentals) — this is the dominant icon system for status/section markers, not a stopgap.
- **No logo mark beyond the favicon**: `favicon.svg` (an orange bar-chart/rocket-trail glyph with a yellow accent circle) doubles as the app's only brand mark, shown at 32–64px next to the wordmark "Wingman" set in Space Grotesk. There is no separate wide logo lockup — copied to `assets/logo/favicon.svg`.

No icon substitution was needed — the source system's icons (inline SVG + emoji) are fully reproducible as-is.

