"""Behavioral event capture — POST /api/events.

The append-only telemetry the matcher's revealed-preference loop will read (saves up,
dismisses down, "not interested" -> re-rank). Capture ships EARLY because an unlogged click
is unrecoverable; the consumer comes weeks later once data has accrued.

Two deliberate choices:
  * It uses get_optional_user, NOT require_subscription. Event capture is pure telemetry and
    must NEVER block the UI or throw a 402 — a lapsed account can't reach the finder anyway,
    and an unidentified caller is simply not attributed (dropped), the same residual the cost
    attribution reports. So this route never returns an error the client has to handle.
  * The write is buffered and flushed in the background (app.core.record_user_events), so the
    request returns immediately without waiting on Supabase.
"""
from fastapi import APIRouter, Depends

from app.core import record_user_events
from app.deps import json_body, json_response
from app.auth import get_optional_user

router = APIRouter()


@router.post("/api/events")
def handle_events(body: dict = Depends(json_body),
                  user=Depends(get_optional_user)):
    """{events: [{action, opportunity_id?, context?}, ...]} -> {ok, accepted}.

    The client batches a tick's worth of events into one call (emitEvent). A missing/invalid
    token or a signed-out caller is fine — nothing is recorded and the response is still 200,
    so capture can never surface as a UI error.
    """
    if user is None:
        return json_response(200, {"ok": True, "accepted": 0})
    events = body.get("events")
    if not isinstance(events, list):
        # Tolerate a single-event body too, so a caller that forgot to wrap doesn't 400.
        events = [body] if body.get("action") else []
    accepted = record_user_events(user.id, events)
    return json_response(200, {"ok": True, "accepted": accepted})
