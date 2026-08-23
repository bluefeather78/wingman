"""Security-relevant tests for app.main._resolve_static.

Return contract (pinned from source): returns an absolute filesystem path string
when the resolved target is an existing, allowed file; returns None otherwise
(dotfile/dotdir, agent_logs, traversal escape, denied ext/name, or missing file).
Paths are anchored under main.REPO_ROOT (the repo root, which holds index.html).
"""
import os

from app import main


REPO_ROOT = main.REPO_ROOT


def _norm(*parts):
    return os.path.normpath(os.path.join(REPO_ROOT, *parts))


# --------------------------------------------------------------------------- #
# Allowed / existing files
# --------------------------------------------------------------------------- #
def test_empty_path_resolves_to_index():
    assert main._resolve_static("") == _norm("index.html")


def test_root_slash_resolves_to_index():
    assert main._resolve_static("/") == _norm("index.html")


def test_index_html_returns_path():
    assert main._resolve_static("index.html") == _norm("index.html")


def test_allowed_static_assets():
    assert main._resolve_static("styles.css") == _norm("styles.css")
    assert main._resolve_static("script.js") == _norm("script.js")


def test_leading_slash_stripped():
    assert main._resolve_static("/index.html") == _norm("index.html")


# --------------------------------------------------------------------------- #
# Non-existent files -> None
# --------------------------------------------------------------------------- #
def test_missing_file_returns_none():
    assert main._resolve_static("does-not-exist-xyz.html") is None


def test_directory_without_index_returns_none():
    # 'app' exists as a dir but has no index.html -> maps to app/index.html -> missing.
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
