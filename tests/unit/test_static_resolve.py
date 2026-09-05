"""Security-relevant tests for app.main._resolve_static.

Return contract (pinned from source): returns an absolute filesystem path string
when the resolved target is an existing, allowed file; returns None otherwise
(empty/root path, dotfile/dotdir, a blocked directory, traversal escape, denied ext/name,
or missing file). Paths are anchored under main.REPO_ROOT. Since the Phase 3
cutover (tag `workingwithauth`) the old SPA is gone: the route only serves the
static pages the app still links to (terms/privacy/about + public/styles.css/favicon.svg),
and the root path is handled by serve_static (status/redirect), never by a file.
"""
import os

from app import main


REPO_ROOT = main.REPO_ROOT
PUBLIC_DIR = main.PUBLIC_DIR


def _norm(*parts):
    """Resolve against public/ — the only directory _resolve_static looks in.

    It was REPO_ROOT until 2026-09-04, when the route stopped resolving against the whole
    repository. Serving is now opt-in by putting a file in public/, rather than opt-out via
    the deny-lists (PRODUCTION_READINESS_PLAN.md High #5)."""
    return os.path.normpath(os.path.join(PUBLIC_DIR, *parts))


def _repo(*parts):
    """A path in the repo but OUTSIDE public/ — for asserting it cannot be served."""
    return os.path.normpath(os.path.join(REPO_ROOT, *parts))


# --------------------------------------------------------------------------- #
# Allowed / existing files
# --------------------------------------------------------------------------- #
def test_empty_path_returns_none():
    assert main._resolve_static("") is None


def test_root_slash_returns_none():
    assert main._resolve_static("/") is None


def test_walkthrough_film_is_served():
    """The landing film survived the cutover and MUST resolve.

    frontend/app/landing.tsx iframes it via backendUrl('/walkthrough.html'), so a 404
    here breaks the landing page in production while working perfectly against a local
    checkout — the file sits at the repo root, which is mostly gitignored, and it was
    untracked once already after the Phase 3 cutover. That is the failure this pins.
    """
    assert main._resolve_static("walkthrough.html") == _norm("walkthrough.html")


def test_walkthrough_served_with_playhead_reset_injected(monkeypatch):
    """The film's player persists its playhead ('animstage-v3:t') and plays exactly once,
    so a reload holds the final frame. The parent-side clear only works same-origin
    (production); in dev the iframe is cross-origin and it no-ops — so serve_static
    injects the clear into the served document itself, where it runs in the film's own
    origin before the bundle builds its initial state. This pins: injected exactly once,
    at the top of <head> (before anything else can run), and NEVER written to the
    vendored file on disk, which is a re-export-only artifact."""
    from fastapi.responses import HTMLResponse

    monkeypatch.setattr(main, "SERVE_WEB_DIST", False)
    resp = main.serve_static("walkthrough.html")
    assert isinstance(resp, HTMLResponse)
    snippet = b"localStorage.removeItem('animstage-v3:t')"
    assert resp.body.count(snippet) == 1
    assert resp.body.index(snippet) < resp.body.index(b"<style")
    with open(_norm("walkthrough.html"), "rb") as f:
        assert snippet not in f.read()


def test_other_root_pages_are_not_injected(monkeypatch):
    """Only the walkthrough gets the rewrite; everything else streams from disk."""
    from fastapi.responses import FileResponse

    monkeypatch.setattr(main, "SERVE_WEB_DIST", False)
    assert isinstance(main.serve_static("terms.html"), FileResponse)


def test_allowed_static_assets():
    assert main._resolve_static("styles.css") == _norm("styles.css")
    assert main._resolve_static("terms.html") == _norm("terms.html")
    assert main._resolve_static("privacy.html") == _norm("privacy.html")
    assert main._resolve_static("about.html") == _norm("about.html")
    assert main._resolve_static("favicon.svg") == _norm("favicon.svg")


def test_leading_slash_stripped():
    assert main._resolve_static("/terms.html") == _norm("terms.html")


# --------------------------------------------------------------------------- #
# Non-existent files -> None
# --------------------------------------------------------------------------- #
def test_missing_file_returns_none():
    assert main._resolve_static("does-not-exist-xyz.html") is None


def test_retired_spa_files_return_none():
    # The old SPA was deleted at the cutover — these must 404, not resurrect.
    # public/walkthrough.html is NOT one of them; see test_walkthrough_film_is_served.
    assert main._resolve_static("index.html") is None
    assert main._resolve_static("script.js") is None


def test_directory_returns_none():
    # 'app' exists as a dir; directories are not served (no index.html mapping anymore).
    assert main._resolve_static("app") is None


# --------------------------------------------------------------------------- #
# Dotfiles / dotdirs
# --------------------------------------------------------------------------- #
def test_dotfile_env_rejected():
    assert main._resolve_static(".env") is None


def test_dotdir_git_rejected():
    assert main._resolve_static(".git/config") is None


def test_dotdir_in_middle_rejected():
    assert main._resolve_static("app/.secret/x.html") is None


# --------------------------------------------------------------------------- #
# agent_logs
# --------------------------------------------------------------------------- #
def test_agent_logs_rejected():
    assert main._resolve_static("agent_logs/run.log") is None


def test_agent_logs_nested_rejected():
    assert main._resolve_static("agent_logs") is None


# --------------------------------------------------------------------------- #
# Blocked directories
#
# _DENY_EXT is by FILE TYPE and has no .json/.xlsx/.docx, so before the
# 2026-09-04 tidy-up `GET /Opportunities.xlsx`, `/opportunities.json` and
# `/test_resume.docx` were all served from the repo root in production. Moving
# them into data/ does not fix that by itself — this resolver joins any relative
# path under REPO_ROOT — so the directories are blocked by name. These paths are
# real files in the tree, which is the point: the assertion has to be that a file
# that EXISTS is refused, not that a missing one 404s.
# --------------------------------------------------------------------------- #
def test_blocked_dirs_rejected():
    for rel in (
        "data/opportunities.json",
        "data/Opportunities.xlsx",
        "data/hubs_seattle.json",
        "tests/fixtures/test_resume.docx",
        "db/email_schema.sql",
        "docs/review-2026-09-02/load_results.json",
        "frontend/package.json",
    ):
        assert os.path.isfile(_repo(rel)), f"fixture moved: {rel}"
        assert main._resolve_static(rel) is None, rel


def test_root_pages_still_served():
    """The five pages this route exists for must survive the directory block."""
    for name in ("terms.html", "privacy.html", "about.html", "styles.css", "favicon.svg"):
        assert main._resolve_static(name) == _norm(name)


def test_nothing_outside_public_is_reachable():
    """The inversion this route exists for since 2026-09-04.

    Every one of these is a real file in the repo. Before the change they were refused
    only because _DENY_EXT/_DENY_NAMES/_DENY_DIRS happened to name them -- a file type or
    directory nobody thought of was served. Now the resolver never looks outside public/,
    so the deny-lists are a second line rather than the only one. ops/logic_map.html is the
    one that was NOT covered: it returned 200 in production, publishing the ops console's
    internal pipeline map, and it now lives in ops/."""
    for rel in ("README.md", "CLAUDE.md", "requirements.txt", "render.yaml",
                "pyproject.toml", "server.py", "wingman/gemini_common.py",
                "agents/scrape_opportunities.py", "ops/logic_map.html",
                "ops/admin_console.html", "data/opportunities.json",
                "db/email_schema.sql", "legal/terms.md"):
        assert os.path.isfile(_repo(rel)), f"fixture moved: {rel}"
        assert main._resolve_static(rel) is None, rel


# --------------------------------------------------------------------------- #
# Traversal
# --------------------------------------------------------------------------- #
def test_parent_traversal_rejected():
    # '..' begins with '.' so the dotdir guard catches it before the escape check.
    assert main._resolve_static("../secret.txt") is None


def test_deep_traversal_rejected():
    assert main._resolve_static("../../etc/passwd") is None


def test_embedded_traversal_rejected():
    assert main._resolve_static("app/../../outside.html") is None


# --------------------------------------------------------------------------- #
# Denied extensions / names
# --------------------------------------------------------------------------- #
def test_deny_py_extension():
    # server.py exists but .py is denied.
    assert main._resolve_static("server.py") is None


def test_deny_md_extension():
    assert main._resolve_static("CLAUDE.md") is None


def test_deny_sql_ps1_txt_sh_log():
    for name in ("x.sql", "x.ps1", "x.txt", "x.sh", "x.log"):
        assert main._resolve_static(name) is None


def test_deny_names_agent_settings():
    assert main._resolve_static("agent_settings.json") is None


def test_deny_ext_case_insensitive():
    # base is lowercased before the ext check.
    assert main._resolve_static("SERVER.PY") is None


# --------------------------------------------------------------------------- #
# frontend/dist serving (SERVE_WEB_DIST) — opt-in, guarded, never shadows the
# repo-root legal pages (serve_static tries _resolve_static before _dist_index).
# --------------------------------------------------------------------------- #
def _dist(tmp_path, monkeypatch, *files):
    root = tmp_path / "dist"
    root.mkdir()
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    monkeypatch.setattr(main, "SERVE_WEB_DIST", True)
    monkeypatch.setattr(main, "WEB_DIST_ROOT", str(root))
    return root


def test_dist_off_returns_none(monkeypatch):
    monkeypatch.setattr(main, "SERVE_WEB_DIST", False)
    assert main._resolve_dist("index.html") is None
    assert main._dist_index() is None


def test_dist_exact_and_route_html(tmp_path, monkeypatch):
    root = _dist(tmp_path, monkeypatch, "index.html", "tracker.html", "_expo/static/js/app.js")
    assert main._resolve_dist("index.html") == str(root / "index.html")
    # expo-router's exported route html answers the extensionless route path.
    assert main._resolve_dist("tracker") == str(root / "tracker.html")
    assert main._resolve_dist("_expo/static/js/app.js") == str(root / "_expo" / "static" / "js" / "app.js")
    # root path serves the app shell.
    assert main._resolve_dist("") == str(root / "index.html")


def test_dist_misses_return_none_not_index(tmp_path, monkeypatch):
    # A miss must NOT fall back to index here — serve_static gives the repo-root
    # pages (terms/privacy/about) their chance first, THEN applies _dist_index().
    _dist(tmp_path, monkeypatch, "index.html")
    assert main._resolve_dist("terms.html") is None
    assert main._resolve_dist("no-such-route") is None


def test_dist_traversal_rejected(tmp_path, monkeypatch):
    _dist(tmp_path, monkeypatch, "index.html")
    assert main._resolve_dist("../secret.txt") is None
    assert main._resolve_dist("a/../../outside.html") is None
