"""Unit tests for the interactive-process throttle switch — Phase 2 item 1, finding M5.

wingman/gemini_common._enforce_rate_limit() sleeps up to DEFAULT_MIN_DELAY_SECS (5) between
calls, against a MODULE GLOBAL. In a batch agent that is correct and load-bearing: the agents
share one googleSearch quota and the delay is what fixed past HTTP 429s. In the web service it
was a process-wide 5-second sleep taken inside an anyio threadpool slot with a student waiting,
serialised across every concurrent caller. /api/match paid it TWICE per request — once to
embed the student's themes, once for the eligibility gate.

What these pin:
  * batch behaviour is UNCHANGED. This is the failure that would cost money rather than
    latency: an agent that stopped throttling would walk into the 429s the delay exists to
    prevent, on a long paid run.
  * the web process really is exempt, and says so in exactly one place.
  * an interactive 429 propagates instead of sleeping 5s to retry — the retry is the same
    pathology as the throttle, one layer down.
"""
import time
import urllib.error

import pytest

from wingman import gemini_common as g


@pytest.fixture(autouse=True)
def _restore_flags():
    """The suite imports app.main, which flips this global for the whole session — so every
    test here sets what it needs and puts back what it found."""
    was_interactive = g.is_interactive_process()
    was_delay = g.get_min_delay()
    yield
    g.set_interactive_process(was_interactive)
    g.set_min_delay(was_delay)


# ---------- the throttle ----------

def test_a_batch_process_still_sleeps_between_calls():
    """The one that would cost money if it broke."""
    g.set_interactive_process(False)
    g.set_min_delay(0.25)
    g._enforce_rate_limit()                       # stamps the clock
    start = time.time()
    g._enforce_rate_limit()                       # must wait out the delay
    assert time.time() - start >= 0.2


def test_an_interactive_process_does_not_sleep():
    g.set_interactive_process(True)
    g.set_min_delay(5)
    g._enforce_rate_limit()
    start = time.time()
    for _ in range(5):
        g._enforce_rate_limit()
    assert time.time() - start < 0.1, "the web path is still taking the batch throttle"


def test_the_default_for_a_bare_process_is_batch():
    """An agent imports this module and gets the throttle without doing anything. The default
    has to stay the safe one — the exemption is opt-in, declared by exactly one caller."""
    import ast
    import inspect
    src = inspect.getsource(g)
    tree = ast.parse(src)
    assigned = [n for n in tree.body
                if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "_interactive_process" for t in n.targets)]
    assert len(assigned) == 1
    assert assigned[0].value.value is False


def test_the_web_app_declares_itself_interactive():
    import app.main                                              # noqa: F401
    assert g.is_interactive_process() is True


def test_only_the_web_app_flips_the_switch():
    """An agent calling set_interactive_process would silently lose the delay that keeps a
    long paid run under Gemini's rate limit. Pin the caller list at one."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    callers = []
    for d in ("app", "agents", "ops", "wingman", "scripts", "eval"):
        for py in (root / d).rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            if "set_interactive_process(" in text and py.name != "gemini_common.py":
                callers.append(str(py.relative_to(root)))
    assert callers == ["app/main.py"], callers


# ---------- the 429 retry ----------

def _http_429():
    return urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, None)


def test_an_interactive_429_propagates_instead_of_sleeping_to_retry(monkeypatch):
    """The route already degrades well — app/routes/ai.py turns an upstream 429 into
    'Wingman is busy right now'. Sleeping 5s in a threadpool slot to re-ask a service that
    just said 'too many requests' is the pathology, not the fix."""
    g.set_interactive_process(True)
    calls = []

    def fake_urlopen(*a, **k):
        calls.append(1)
        raise _http_429()
    monkeypatch.setattr(g.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(g.time, "sleep", lambda _s: pytest.fail("interactive path must not sleep"))

    start = time.time()
    with pytest.raises(urllib.error.HTTPError):
        g.call_gemini("sys", "user", "key", use_web_search=False, max_tokens=10)
    assert calls == [1], "it retried; interactive callers get one attempt"
    assert time.time() - start < 0.5


def test_a_batch_429_still_retries_once(monkeypatch):
    """Unchanged behaviour for the agents, which is the point of the guard being conditional."""
    g.set_interactive_process(False)
    g.set_min_delay(0)
    calls = []

    def fake_urlopen(*a, **k):
        calls.append(1)
        raise _http_429()
    monkeypatch.setattr(g.urllib.request, "urlopen", fake_urlopen)
    slept = []
    monkeypatch.setattr(g.time, "sleep", lambda s: slept.append(s))

    with pytest.raises(urllib.error.HTTPError):
        g.call_gemini("sys", "user", "key", use_web_search=False, max_tokens=10)
    assert calls == [1, 1], "the batch path retries exactly once"
    assert 5 in slept
