"""Deploy prerequisites — S0-11 in SECURITY_HARDENING_PLAN.md.

Not security, but the plan pulls them in because nothing ships without them: an unpinned
requirement means two builds of the same commit can install different code, and numpy was
missing entirely, so production survived only on Render's build cache.
"""
import os
import re

from wingman import REPO_ROOT

_REQ = os.path.join(REPO_ROOT, "requirements.txt")


def _requirement_lines():
    with open(_REQ) as fh:
        return [ln.strip() for ln in fh
                if ln.strip() and not ln.lstrip().startswith("#")]


def test_every_requirement_is_pinned_exactly():
    """They were all `>=`, so a deploy picked up whatever was newest that day — which makes a
    production break impossible to attribute and impossible to roll back by reverting."""
    for line in _requirement_lines():
        assert "==" in line, f"{line!r} is not pinned to an exact version"
        assert not re.search(r"[><~!]=|[<>]", line.split("==", 1)[1]), \
            f"{line!r} has a range on the right of =="


def test_numpy_is_declared():
    """app/services/matching.py and app/services/recall_query.py import it at module top and
    app/main.py mounts the matching router unconditionally, but nothing else pulls it
    transitively. Before it was listed, the next Render build-cache miss was a
    ModuleNotFoundError at import and a hard outage."""
    assert any(ln.lower().startswith("numpy==") for ln in _requirement_lines())


def test_the_pins_match_what_is_installed():
    """The pinned versions must be the ones this repo is actually developed and tested
    against, or the suite is not evidence about what production runs."""
    from importlib.metadata import version, PackageNotFoundError

    for line in _requirement_lines():
        name, pinned = line.split("==", 1)
        name = name.split("[", 1)[0]                    # uvicorn[standard] -> uvicorn
        try:
            installed = version(name)
        except PackageNotFoundError:                    # not installed in this env; skip
            continue
        assert installed == pinned, \
            f"{name}: requirements.txt pins {pinned} but {installed} is installed"
