"""Shared library layer: the modules app/, ops/, agents/, tests/ and the one-off
scripts all import. Not a third-party package -- first-party code, resolved because the
repo root is the cwd for every entry point (uvicorn, pytest, `python -m agents.x`).
"""

import os

# The repository root, as an absolute path.
#
# Every module that writes agent_logs/, a dry-run snapshot, or a lock file used to
# compute this itself with `os.path.dirname(os.path.abspath(__file__))`, which was
# correct only while every one of them sat AT the root. The 2026-09-04 move broke all
# 17 of those at once, and 16 would have failed SILENTLY -- writing to or reading from
# the wrong directory rather than raising. gemini_common's .gemini_web_search.lock is
# the worst of them: two agents computing different lock paths do not error, they just
# both hit the Google search quota and cost real money.
#
# So it is defined ONCE, here, and imported. A file that moves again changes nothing.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
