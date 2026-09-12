"""The initial-impressions survey: one public, unauthenticated POST from public/survey.html."""
from fastapi import APIRouter, Depends

from app.deps import json_body, json_response
from app.services.survey import submit_response

router = APIRouter()


@router.post("/api/survey")
def handle_survey_submit(body: dict = Depends(json_body)):
    """POST /api/survey — {first_impression, recommend_score?, most_useful?, missing?, userid?}.

    No auth: the survey link in the invite email must work whether or not the recipient is
    signed in on the device that opens it.
    """
    status, payload = submit_response(body)
    return json_response(status, payload)
