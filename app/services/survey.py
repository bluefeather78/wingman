"""The initial-impressions survey: one INSERT into survey_responses, no auth required.

This is deliberately NOT wired into any account or lifecycle-email machinery — it exists so
public/survey.html has somewhere to POST to. `userid` is a correlation hint for reading
responses later, not an identity check, so nothing here trusts it for access control — but
it IS verified (against the `t` HMAC from app.services.email.survey_invite_url) before being
stored, so a forwarded or hand-edited link cannot misattribute someone else's feedback to a
real student. An absent or failed token just means the response is stored anonymous, same as
if userid had never been sent at all.
"""
from app.config import SURVEY_SETUP_SQL
from app.core import _supabase_request_strict, _missing_table_error

TABLE = "survey_responses"

# Free-text fields are capped so a pasted essay (or an abusive payload) can't blow past a
# reasonable feedback comment. Not security-critical — JSON_MAX_BODY_BYTES already bounds the
# whole request — just keeps the column readable in the console.
_MAX_TEXT = 2000


def _clamp_text(value):
    text = str(value or "").strip()
    return text[:_MAX_TEXT] if text else None


def _clamp_int(value, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def submit_response(body):
    """Validate and insert one survey response. Returns (status, payload).

    first_impression (1-5) is the only required field, matching the survey page marking it
    as the one mandatory question — everything else is a respondent choosing to say more.
    """
    first_impression = _clamp_int((body or {}).get("first_impression"), 1, 5)
    if first_impression is None:
        return 400, {"error": "first_impression must be a number from 1 to 5"}

    from app.services.email import verify_survey_token
    userid = _clamp_text((body or {}).get("userid"))
    if userid and not verify_survey_token(userid, (body or {}).get("t")):
        userid = None

    row = {
        "userid": userid,
        "first_impression": first_impression,
        "recommend_score": _clamp_int((body or {}).get("recommend_score"), 0, 10),
        "most_useful": _clamp_text((body or {}).get("most_useful")),
        "missing": _clamp_text((body or {}).get("missing")),
    }

    try:
        _supabase_request_strict(TABLE, "POST", data=row,
                                 extra_headers={"Prefer": "return=minimal"})
    except Exception as e:
        if _missing_table_error(e):
            return 503, {"error": f"Survey isn't set up yet — run {SURVEY_SETUP_SQL}."}
        return 502, {"error": "Could not record your response. Please try again."}

    return 200, {"ok": True}
