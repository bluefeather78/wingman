"""Resume/LinkedIn import + user-submitted opportunity routes. Translated from
server.py's handle_extract_from_resume / handle_extract_from_linkedin /
handle_user_submitted_opportunity (docs/archive/PLAN_1_decompose.md). The extraction/insert logic
lives in app.services.resume; these are the HTTP glue.
"""

from fastapi import APIRouter, Request, Depends

from app.config import RESUME_MAX_BODY_BYTES
from app.core import touch_user_activity
from app.deps import (json_body, json_response, json_error, subscription_block_reason,
                      optional_subscribed_user, capped_raw_body)
from app.auth import get_current_user, AuthedUser
from app.services import resume as resume_service

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
        return json_error(500, f"Failed to extract resume: {str(e)}")


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
        return json_error(500, f"Failed to extract LinkedIn profile: {str(e)}")


@router.post("/api/user-submitted-opportunities")
def handle_user_submitted_opportunity(body: dict = Depends(json_body),
                                      user: AuthedUser = Depends(optional_subscribed_user)):
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

    Soft auth: the userid here is provenance for the review queue, not access control (the
    row is public-review-queue data, not owned data). Use the token's identity when signed
    in, else record it as an unattributed submission — same residual as before. A token
    belonging to a LAPSED account is still refused (402): adding to your Quest Log is
    using the app."""
    name = (body.get("name") or "").strip()
    # NOT lowercased — the stored URL must stay exactly as given (case-sensitive paths).
    url = (body.get("url") or "").strip()
    opp_type = (body.get("type") or "").strip()
    section = (body.get("section") or "").strip()
    meta = (body.get("meta") or "").strip()
    fit = (body.get("fit") or "").strip()
    note = (body.get("note") or "").strip()
    important_dates = body.get("important_dates") or []
    requirements = body.get("requirements") or []
    apply_url = (body.get("apply_url") or "").strip()
    category = (body.get("category") or "").strip()
    userid = user.id if user else None

    if not url or not name:
        return json_error(400, "URL and name are required.")

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
