"""Resume/LinkedIn import + user-submitted opportunity routes. Translated from
server.py's handle_extract_from_resume / handle_extract_from_linkedin /
handle_user_submitted_opportunity (PLAN_1_decompose.md). The extraction/insert logic
lives in app.services.resume; these are the HTTP glue.
"""
import threading

from fastapi import APIRouter, Request, Depends

from app.core import touch_user_activity
from app.deps import read_json_body, json_response, json_error, subscription_block_reason
from app.auth import get_current_user, get_optional_user, AuthedUser
from app.services import resume as resume_service

router = APIRouter()


@router.post("/api/extract-from-resume")
async def handle_extract_from_resume(request: Request, user: AuthedUser = Depends(get_current_user)):
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
    raw = await request.body()

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
async def handle_extract_from_linkedin(request: Request, user: AuthedUser = Depends(get_current_user)):
    """Extract profile-relevant information from LinkedIn profile (text paste only)."""
    # Paid Claude call that writes into a profile, so it is token-gated and subscription-
    # gated exactly like the resume path — identity comes from the token, not the body.
    reason = subscription_block_reason(user.id)
    if reason:
        return json_error(402, reason)
    try:
        body = await read_json_body(request)
    except Exception:
        return json_error(400, "Malformed JSON.")

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
async def handle_user_submitted_opportunity(request: Request,
                                            user: AuthedUser = Depends(get_optional_user)):
    """Accept user-submitted opportunity data, dedupe by URL, and insert into the
    opportunities table with is_active=false. Runs asynchronously.

    Soft auth: the userid here is provenance for the review queue, not access control (the
    row is public-review-queue data, not owned data). Use the token's identity when signed
    in, else record it as an unattributed submission — same residual as before."""
    try:
        body = await read_json_body(request)
    except Exception:
        return json_error(400, "Malformed JSON.")

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

    def background_insert():
        try:
            resume_service.insert_user_opportunity(
                name, url, opp_type, section, meta, fit, note,
                important_dates, requirements, apply_url, category, userid
            )
        except Exception as e:
            print(f"[User Opportunity] Background insertion failed: {e}")

    threading.Thread(target=background_insert, daemon=True).start()

    return json_response(200, {
        "status": "queued",
        "message": "Opportunity queued for addition to database",
    })
