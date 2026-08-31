"""Resume / LinkedIn profile extraction and user-submitted-opportunity insertion.

Converted from the former Handler methods in server.py (PLAN_1_decompose.md) into
plain module functions — the HTTP glue (multipart read, response) now lives in
app.routes.resume; the logic here is unchanged.
"""
import datetime
import io
import random
import re
import time

import url_dedupe
from claude_common import call_claude
from app.config import (
    SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY, CLAUDE_MODEL,
)
from app.core import _supabase_request, record_interactive_cost_async


def extract_multipart_file(raw, boundary):
    """Extract filename and file bytes from multipart form data."""
    parts = raw.split(b"--" + boundary)
    for part in parts:
        if b"filename=" in part:
            filename_match = re.search(rb'filename="([^"]*)"', part)
            if not filename_match:
                filename_match = re.search(rb"filename=([^;\r\n\s]+)", part)
            if not filename_match:
                continue
            filename = filename_match.group(1).decode("utf-8", errors="ignore").strip('"')

            file_start = part.find(b"\r\n\r\n")
            if file_start == -1:
                file_start = part.find(b"\n\n")
                if file_start == -1:
                    continue
                file_data = part[file_start + 2:]
            else:
                file_data = part[file_start + 4:]

            file_data = file_data.rstrip(b"\r\n").rstrip(b"\n").rstrip(b"\r")
            if file_data.endswith(b"--"):
                file_data = file_data[:-2].rstrip(b"\r\n")

            return (filename, file_data)
    return None


def extract_text_from_pdf(file_bytes):
    """Extract text from PDF bytes using PyPDF2 with fallback."""
    try:
        import PyPDF2
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in pdf_reader.pages:
            extracted = page.extract_text() or ""
            text += extracted + "\n"
        return text if text.strip() else fallback_extract_text(file_bytes, "pdf")
    except Exception:
        return fallback_extract_text(file_bytes, "pdf")


def extract_text_from_docx(file_bytes):
    """Extract text from DOCX bytes using python-docx with fallback."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        text = "\n".join([p.text for p in doc.paragraphs])
        return text if text.strip() else fallback_extract_text(file_bytes, "docx")
    except Exception:
        return fallback_extract_text(file_bytes, "docx")


def fallback_extract_text(file_bytes, filename):
    """Fallback text extraction when libraries aren't available."""
    try:
        text = file_bytes.decode('utf-8', errors='ignore')
        return text[:5000] if text else ""
    except Exception:
        return ""


def extract_profile_from_text(text, source, userid=None):
    """Use Claude to extract profile-relevant information from text.

    Costed like every other interactive call: this discarded its usage block entirely
    until per-user accounting went in, which meant resume/LinkedIn imports were real
    Anthropic spend that showed up in no figure on the console at all.
    """
    if not ANTHROPIC_API_KEY:
        return mock_extract_profile(source, text)

    system_prompt = f"""You are a friendly assistant helping a high school student build a detailed personal profile for finding extracurricular opportunities (research programs, internships, competitions, summer programs). Given the following {"resume" if source == "resume" else "LinkedIn profile"} text, pull out everything that would help understand this student for extracurricular and college-application matching.

Capture broadly — not just conventional achievements. Include, whenever the text states them:
- grade or year in school (e.g. "junior", "11th grade") and GPA if given (e.g. "3.4 GPA")
- where they live or their school's setting if mentioned (e.g. "large suburban high school")
- favorite and strongest subjects, AND subjects they find hard or dislike (e.g. "I have a harder time with math and tend to procrastinate")
- extracurriculars, clubs, sports/athletics, theater, arts, music
- projects, research, papers, or anything larger they personally drive
- part-time jobs, volunteering, family or community involvement, leadership roles
- skills, and how they describe their own personality (e.g. "outgoing and talkative, get bored when I'm not doing something")
- goals or what they're considering after high school, including uncertainty (e.g. "considering communications, journalism, or entertainment, still figuring it out")

Do NOT drop a detail just because it isn't an impressive accomplishment — a weakness, an uncertainty, a personality trait, or an ordinary job all matter for matching. Never invent or infer anything the text doesn't state: don't guess a GPA, a grade, a location, or a gender that isn't there.

Only ignore genuinely non-relevant material: contact information (phone, email, mailing address), salary figures, and company-specific jargon.

Output the extracted information as concise, first-person statements (e.g. "I'm a junior…", "I've worked on…", "I'm skilled in…", "I led…" — not third person or bullet points). Do NOT include markdown, quotes, or preamble."""

    user_content = f"""Extract relevant profile information from this {"resume" if source == "resume" else "LinkedIn profile"}:

{text[:2000]}"""

    try:
        result = call_claude(
            system=system_prompt,
            user_content=user_content,
            api_key=ANTHROPIC_API_KEY,
            use_web_search=False,
            # Matches the chat flow's profile-building budget (PROFILE_SYNTH_MAX_TOKENS = 4000).
            # The old 500 forced heavy compression that dropped grade/GPA/personality along
            # with the narrow allow-list; unused budget is free (billed on tokens produced).
            max_tokens=4000,
            timeout=30
        )

        if isinstance(result, tuple) and len(result) >= 1:
            extracted_text = result[0]
            usage = result[1] if len(result) > 1 and isinstance(result[1], dict) else None
        else:
            extracted_text = result
            usage = None

        if usage:
            # Same rollup row as every other interactive Claude call, so the console's
            # app-spend total and the per-user breakdown both pick it up.
            record_interactive_cost_async("interactive_claude", usage, CLAUDE_MODEL,
                                          userid=userid, system=system_prompt)

        if extracted_text and str(extracted_text).strip():
            return str(extracted_text).strip()
        return mock_extract_profile(source, text)
    except Exception:
        return mock_extract_profile(source, text)


def mock_extract_profile(source, text):
    """Generate plausible mock extracted profile information."""
    if source == "resume":
        return """I have experience with Python and JavaScript programming. I've worked on several school projects including a machine learning application and a web application. I'm interested in STEM fields and have participated in coding competitions. I've interned with a local tech company where I worked on web development projects."""
    else:
        return """I'm passionate about computer science and artificial intelligence. I've led several club initiatives and participated in hackathons. My skills include web development, data analysis, and project management. I'm active in my school community and have volunteered with local nonprofits."""


def insert_user_opportunity(name, url, opp_type, section, meta, fit, note,
                            important_dates, requirements, apply_url, category,
                            userid=None):
    """Insert a user-submitted opportunity, deduped against the whole catalog.

    Returns the catalog id this submission resolves to, or None if nothing could be
    resolved. That return value is load-bearing as of 2026-08-24: the Quest Log stores it
    as the tracked item's id so a hand-added opportunity can use the SAME shared, cached
    deadline check a catalog opportunity does. Without an id the item carried a local slug,
    /api/opportunities/<slug>/deadline 404'd, and "Check for updates" silently skipped it
    while still reporting "no changes found".

    An EXACT duplicate returns the existing row's id rather than None — that row is the
    right thing to track, and it may already carry a verified, cached deadline answer.

    Dedupe is tiered, and only the top tier rejects (see url_dedupe for why the data
    forbids anything stronger):
      - exact match on the normalized URL -> skip the insert entirely;
      - anything weaker (sub-page of an existing entry, similar name, shared quiet
        domain, matching apply_url) -> insert anyway, with the candidate matches
        recorded on the row so the reviewer decides.
    The previous implementation compared a lowercased URL against un-normalized stored
    values with PostgREST `eq.`, which silently failed for the ~44% of catalog rows
    holding an uppercase character or a trailing slash.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("[User Opportunity] Supabase credentials not configured")
        return None

    existing = catalog_dedupe_rows()
    if existing is None:
        print("[User Opportunity] Could not read catalog for dedupe — refusing to "
              "insert blind (would risk a duplicate).")
        return None

    exact, candidates = url_dedupe.find_duplicates(
        url, name, existing, apply_url=apply_url or None)
    if exact:
        print(f"[User Opportunity] Skipped — already in catalog as "
              f"{exact.get('id')} ({exact.get('name')}): {url}")
        # Not a failure: this IS the row to track, so hand its id back to the caller.
        return exact.get("id")
    if candidates:
        print(f"[User Opportunity] {len(candidates)} possible duplicate(s) for {url!r}; "
              f"inserting for review anyway: "
              + "; ".join(f"{c['id']} ({c['confidence']}: {c['reason']})"
                          for c in candidates))

    # Map section to type if needed
    if not opp_type:
        section_to_type = {
            "summerPrograms": "Program",
            "internships": "Internship",
            "researchCompetitions": "Research",
            "pureCompetitions": "Competition",
            "conferences": "Conference",
            "journals": "Journal",
        }
        opp_type = section_to_type.get(section, "Program")

    # Generate unique ID. The random suffix matters: a bare millisecond timestamp
    # collides for two submissions landing in the same millisecond, and since this runs
    # on a background thread of a ThreadingHTTPServer that is not hypothetical — the
    # loser would fail the primary key and be dropped with only a log line.
    generated_id = f"us{int(time.time() * 1000)}{random.randint(0, 999):03d}"

    # Quality note for the reviewer: even a genuinely new opportunity should not be
    # catalogued under its FAQ/about/apply page. 35 existing rows already are.
    quality_flags = []
    if url_dedupe.is_low_value_path(url):
        quality_flags.append("submitted URL is a sub-page (faq/about/apply/etc), "
                             "not the opportunity's main page")

    # Only columns that actually exist on `opportunities`. This list was previously
    # wrong — it also set apply_url, apply_label, meta, requirements and description,
    # none of which are columns on that table. PostgREST rejects the WHOLE insert on one
    # unknown key, so every user submission 400'd and the feature never wrote a row.
    # Do not add a key here without confirming the column exists; the catalog schema is
    # narrower than the shape the AI extraction returns.
    row = {
        "id": generated_id,
        "name": name,
        "url": url,
        "type": opp_type,
        "summary": fit or meta or note,
        "is_active": False,
        "source": "user-submitted",
        "important_dates": important_dates if important_dates else None,
        "category": category or None,
    }
    # Columns from user_submissions_schema.sql. Split out so the insert can be retried
    # without them if that migration hasn't been run yet — see insert_opportunity_row.
    # submission_payload keeps the extracted detail the catalog has nowhere to put
    # (apply_url, requirements, meta, note) so a reviewer can still see it, without
    # widening the student-facing catalog schema to hold it.
    submission_payload = {k: v for k, v in {
        "apply_url": apply_url or url,
        "meta": meta or None,
        "note": note or None,
        "fit": fit or None,
        "requirements": requirements or None,
        "section": section or None,
    }.items() if v}
    review_fields = {
        "moderation_status": "pending_review",
        "submitted_by": userid,
        "submitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dup_candidates": (candidates or None),
        "quality_flags": (quality_flags or None),
        "submission_payload": (submission_payload or None),
    }

    if not insert_opportunity_row(row, review_fields, generated_id, name):
        return None
    return generated_id


def catalog_dedupe_rows():
    """Every catalog row's id/name/url/apply_url, for dedupe. None if unreadable.

    Paginated past PostgREST's 1000-row max-rows cap — the catalog is ~1330 rows, so a
    single unpaginated request silently drops the tail and lets duplicates through.
    Includes is_active=false rows: something already sitting in the review queue (or
    rejected) is still a match, and re-inserting it would put it in front of the
    reviewer twice.
    """
    rows, offset, page_size = [], 0, 1000
    while True:
        # id,name,url only — `apply_url` is NOT a column on this table, and selecting it
        # 400s the whole request. find_duplicates() treats a missing apply_url as absent,
        # so its apply-url cross-check simply doesn't fire against catalog rows.
        page = _supabase_request("opportunities", params={
            "select": "id,name,url",
            "limit": str(page_size), "offset": str(offset)})
        if page is None:
            return None
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def insert_opportunity_row(row, review_fields, generated_id, name):
    """POST the row, retrying without the review columns if the migration is pending.

    Same degrade-gracefully shape as the user_costs table: until
    user_submissions_schema.sql is run in the Supabase SQL editor, PostgREST rejects
    the whole insert with PGRST204 ("column not found") rather than ignoring the
    unknown keys. Losing the submission entirely over a missing review column would be
    worse than losing the review metadata, so we retry with the base row and say so.
    """
    present = {k: v for k, v in review_fields.items() if v is not None}
    # Degrade one step at a time rather than straight to the base row: submission_payload
    # was added to the migration after the other review columns, so a database that has
    # run the first version should still keep its moderation metadata.
    ladder = [
        dict(row, **present),
        dict(row, **{k: v for k, v in present.items() if k != "submission_payload"}),
        row,
    ]
    for attempt, payload in enumerate(ladder):
        try:
            result = _supabase_request("opportunities", method="POST", data=[payload],
                                       extra_headers={"Prefer": "return=minimal"})
            if result is None:
                raise RuntimeError("Supabase insert returned no response")
            if attempt == 0:
                print(f"[User Opportunity] Inserted: {generated_id} - {name} "
                      f"(pending_review)")
            elif attempt == 1:
                print(f"[User Opportunity] Inserted WITHOUT submission_payload: "
                      f"{generated_id} - {name}. Re-run user_submissions_schema.sql to "
                      f"add that column.")
            else:
                print(f"[User Opportunity] Inserted WITHOUT review metadata: "
                      f"{generated_id} - {name}. Run user_submissions_schema.sql in "
                      f"the Supabase SQL editor to enable the moderation queue.")
            return True
        except Exception as e:
            if attempt < len(ladder) - 1:
                print(f"[User Opportunity] Insert failed ({e}); retrying with fewer "
                      f"optional columns.")
                continue
            print(f"[User Opportunity] Insert failed: {e}")
    return False
