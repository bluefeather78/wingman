"""Resume/LinkedIn import + user-submitted opportunity routes. Translated from
server.py's handle_extract_from_resume / handle_extract_from_linkedin /
handle_user_submitted_opportunity (docs/archive/PLAN_1_decompose.md). The extraction/insert logic
lives in app.services.resume; these are the HTTP glue.
"""

from fastapi import APIRouter, Request, Depends

from app.config import (RESUME_MAX_BODY_BYTES, USER_SUBMISSION_MAX_NAME,
                        USER_SUBMISSION_MAX_TEXT, USER_SUBMISSION_MAX_URL,
                        USER_SUBMISSION_MAX_LIST)
from app.core import touch_user_activity
from app.deps import (json_body, json_response, json_error, subscription_block_reason,
                      require_subscription, capped_raw_body,
                      opaque_error)
from app.auth import get_current_user, AuthedUser
from app.auth.ratelimit import user_submission_limiter
from app.services import resume as resume_service
from wingman.url_guard import url_block_reason

router = APIRouter()

# The upload is parsed wholly in memory (extract_multipart_file, then PyPDF2/python-docx), so
# it gets its own ceiling rather than the AI proxies' — higher, because a real resume PDF is
# far larger than a JSON prompt, and separate so raising one never silently raises the other.
# S0-2 / finding M4.
resume_raw_body = capped_raw_body(RESUME_MAX_BODY_BYTES)


@router.post("/api/extract-from-resume")
def handle_extract_from_resume(request: Request, raw: bytes = Depends(resume_raw_body),
                               user: AuthedUser = Depends(get_current_user)):
    """Extract profile-relevant information from a resume (PDF or DOCX)."""
    # Identity is token-derived (was a query-string userid). Gate on subscription before
    # reading the file so a lapsed account can't make us parse a PDF or call Claude.
    resume_userid = user.id
    reason = subscription_block_reason(resume_userid)
    if reason:
        return json_error(402, reason)
    touch_user_activity(resume_userid, "resume_import")
    content_type = request.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        return json_error(400, "Request must be multipart/form-data with file field.")

    boundary = content_type.split("boundary=")[-1].strip().encode()

    try:
        file_data = resume_service.extract_multipart_file(raw, boundary)
        if not file_data:
            return json_error(400, "No file found in request.")

        filename, file_bytes = file_data

        if filename.lower().endswith(".pdf"):
            text = resume_service.extract_text_from_pdf(file_bytes)
        elif filename.lower().endswith(".docx"):
            text = resume_service.extract_text_from_docx(file_bytes)
        else:
            return json_error(400, "Unsupported file format. Use PDF or DOCX.")

        if not text or not text.strip():
            return json_response(200, {"extracted_text": "", "source": "resume", "filename": filename})

        extracted = resume_service.extract_profile_from_text(text, "resume", userid=resume_userid)
        if not isinstance(extracted, str):
            extracted = str(extracted) if extracted else ""

        return json_response(200, {
            "extracted_text": extracted,
            "source": "resume",
            "filename": filename,
        })
    except Exception as e:
        return opaque_error(500, "We could not read that resume. Try a different "
                                 "PDF or DOCX, or paste the text instead.",
                            e, op="resume.pdf")


@router.post("/api/extract-from-linkedin")
def handle_extract_from_linkedin(body: dict = Depends(json_body),
                                 user: AuthedUser = Depends(get_current_user)):
    """Extract profile-relevant information from LinkedIn profile (text paste only)."""
    # Paid Claude call that writes into a profile, so it is token-gated and subscription-
    # gated exactly like the resume path — identity comes from the token, not the body.
    reason = subscription_block_reason(user.id)
    if reason:
        return json_error(402, reason)
    linkedin_text = body.get("linkedin_text", "").strip()
    if not linkedin_text:
        return json_error(400, "Please paste your LinkedIn profile text. LinkedIn blocks "
                               "direct URL access, so text paste is the only supported method.")

    try:
        text = linkedin_text
        if not text or not text.strip():
            return json_response(200, {"extracted_text": "", "source": "linkedin"})

        extracted = resume_service.extract_profile_from_text(
            text, "linkedin", userid=user.id)
        if not isinstance(extracted, str):
            extracted = str(extracted) if extracted else ""

        return json_response(200, {"extracted_text": extracted, "source": "linkedin"})
    except Exception as e:
        return opaque_error(500, "We could not read that LinkedIn text. "
                                 "Please try again.", e, op="resume.linkedin")


def _clipped(body, key, limit):
    """One free-text field, trimmed and hard-truncated. S1-4: these are stored on a catalog
    row and rendered in the admin console, so an unbounded value is a storage lever and a
    thing a reviewer has to scroll past. Truncating rather than refusing keeps a fat-fingered
    paste from losing the student's submission — the row is review-queue data either way."""
    return (body.get(key) or "").strip()[:limit] if isinstance(body.get(key), str) else ""


def _clipped_list(body, key):
    """One array field, bounded. Both land in jsonb on the row, so an unbounded list is a
    storage lever; a non-list is dropped rather than coerced."""
    value = body.get(key)
    return value[:USER_SUBMISSION_MAX_LIST] if isinstance(value, list) else []


@router.post("/api/user-submitted-opportunities")
def handle_user_submitted_opportunity(body: dict = Depends(json_body),
                                      user: AuthedUser = Depends(require_subscription)):
    """Accept user-submitted opportunity data, dedupe by URL, and insert into the
    opportunities table with is_active=false.

    Runs INLINE and returns the resolved catalog id (2026-08-24). It used to hand the work
    to a background thread and answer {"status": "queued"} with no id, which meant a
    hand-added opportunity had nothing to link to: the Quest Log gave it a local slug,
    /api/opportunities/<slug>/deadline 404'd forever, and "Check for updates" skipped it
    while still telling the student "no changes found". With an id, a custom add uses the
    same shared, cached deadline check a catalog opportunity does.

    The row still lands is_active=false — this endpoint never puts anything in front of
    students. Activation stays the manual console step it has always been; the id is only
    what makes the row addressable.

    Failure is still never the student's problem: the item is already in their Quest Log by
    the time this is called, so an unresolvable submission comes back 200 with id=null and
    the client simply carries on unlinked.

    AUTHENTICATED, as of S1-4 (finding M10). This was `optional_subscribed_user` — soft auth,
    on the rationale that the userid was provenance for the review queue rather than access
    control. That rationale was wrong in one specific way: it made a WRITE to the shared
    catalog reachable with no token at all. Anyone could insert rows with attacker-controlled
    name/url/summary/important_dates/category, each call also reading the whole catalog
    (~1,400 rows, two pages) for dedupe — so a script could both amplify against a free-tier
    instance and bury real submissions under thousands of fakes, with the stored text
    rendering in the admin console. It also fed finding M1: the deadline check has no
    is_active filter by design, so an attacker-submitted URL could be fetched server-side.

    Losing the signed-out path costs nothing real — the client only calls this from the
    authed Quest Log, and a signed-out user cannot use the Quest Log either."""
    userid = user.id

    # Per-account daily ceiling. Checked before the catalog read, which is the expensive
    # half of this route.
    if not user_submission_limiter.allow(userid):
        resp = json_error(429, "You have added a lot of opportunities today. "
                               "Try again tomorrow.")
        resp.headers["Retry-After"] = str(user_submission_limiter.retry_after(userid))
        return resp

    name = _clipped(body, "name", USER_SUBMISSION_MAX_NAME)
    # NOT lowercased — the stored URL must stay exactly as given (case-sensitive paths).
    url = _clipped(body, "url", USER_SUBMISSION_MAX_URL)
    opp_type = _clipped(body, "type", USER_SUBMISSION_MAX_NAME)
    section = _clipped(body, "section", USER_SUBMISSION_MAX_NAME)
    meta = _clipped(body, "meta", USER_SUBMISSION_MAX_TEXT)
    fit = _clipped(body, "fit", USER_SUBMISSION_MAX_TEXT)
    note = _clipped(body, "note", USER_SUBMISSION_MAX_TEXT)
    apply_url = _clipped(body, "apply_url", USER_SUBMISSION_MAX_URL)
    category = _clipped(body, "category", USER_SUBMISSION_MAX_NAME)
    # isinstance-checked, not just sliced: a client sending a string here would otherwise
    # be truncated to a 40-character string and stored as if it were a list.
    important_dates = _clipped_list(body, "important_dates")
    requirements = _clipped_list(body, "requirements")

    if not url or not name:
        return json_error(400, "URL and name are required.")

    # S1-4 / finding M1: refuse a URL that is not on the public internet BEFORE it is
    # stored. The row would otherwise become a standing SSRF target — the deadline check
    # deliberately has no is_active filter, and the free agents later fetch these URLs from
    # the operator's own laptop. url_guard also guards every fetch site, so this is the
    # front door rather than the only door; refusing at submission time is what keeps the
    # bad row out of the reviewer's queue in the first place.
    blocked = url_block_reason(url)
    if blocked:
        return json_error(400, "That link does not point at a public web page. "
                               "Paste the program's own URL.")
    # apply_url is optional and is only kept in submission_payload, so a bad one is dropped
    # rather than failing the whole submission.
    if apply_url and url_block_reason(apply_url):
        apply_url = ""

    try:
        opp_id = resume_service.insert_user_opportunity(
            name, url, opp_type, section, meta, fit, note,
            important_dates, requirements, apply_url, category, userid
        )
    except Exception as e:
        # Never surfaced as an error: see the docstring. The client gets id=null and the
        # student's Quest Log entry is unaffected.
        print(f"[User Opportunity] Insertion failed: {e}")
        opp_id = None

    return json_response(200, {
        "status": "linked" if opp_id else "unlinked",
        "id": opp_id,
        "message": ("Opportunity added to the review queue"
                    if opp_id else "Opportunity could not be added to the review queue"),
    })
