"""Security-relevant tests for app.main._resolve_static.

Return contract (pinned from source): returns an absolute filesystem path string
when the resolved target is an existing, allowed file; returns None otherwise
(empty/root path, dotfile/dotdir, agent_logs, traversal escape, denied ext/name,
or missing file). Paths are anchored under main.REPO_ROOT. Since the Phase 3
cutover (tag `workingwithauth`) the old SPA is gone: the route only serves the
static pages the app still links to (terms/privacy/about + styles.css/favicon.svg),
and the root path is handled by serve_static (status/redirect), never by a file.
"""
import os

from app import main


REPO_ROOT = main.REPO_ROOT


def _norm(*parts):
    return os.path.normpath(os.path.join(REPO_ROOT, *parts))


# --------------------------------------------------------------------------- #
# Allowed / existing files
# --------------------------------------------------------------------------- #
def test_empty_path_returns_none():
    assert main._resolve_static("") is None


def test_root_slash_returns_none():
    assert main._resolve_static("/") is None


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
    assert main._resolve_static("index.html") is None
    assert main._resolve_static("script.js") is None
    assert main._resolve_static("walkthrough.html") is None


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
