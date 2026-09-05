"""conversations gets a schema file, loses client_ip, and stdout stops being a roster —
S1-9, the report's M9 finding.

Two halves. The table's only definition was a COMMENT in app/core.py with no
`enable row level security` line, while every other user table in this repo has one — and
it holds the most sensitive free text in the product, a minor describing themselves,
duplicated outside the RLS-protected `users` row. And userids and full email addresses were
printed to stdout in five places; Render retains stdout, so anyone with log access got a
timeline of which minors did what and from where.
"""
import json
import pathlib
import re

import pytest

import app.core as core
import app.services.email as es
import app.services.mailing_list as mls


# ---------------- the schema files ----------------

@pytest.mark.parametrize("path", ["db/conversations_schema.sql",
                                  "db/agent_runs_schema.sql",
                                  "db/deadline_check_log_schema.sql"])
def test_every_flagged_table_now_enables_rls(path):
    sql = pathlib.Path(path).read_text().lower()
    assert "enable row level security" in sql


@pytest.mark.parametrize("path", ["db/conversations_schema.sql", "db/agent_runs_schema.sql"])
def test_the_new_files_say_a_file_does_not_secure_a_live_table(path):
    """The instruction IS the fix. Running this repo's SQL cannot retroactively secure a
    table somebody created by hand from a code comment."""
    text = pathlib.Path(path).read_text().lower()
    assert "dashboard" in text


def test_the_conversations_file_drops_client_ip():
    sql = pathlib.Path("db/conversations_schema.sql").read_text().lower()
    assert "drop column if exists client_ip" in sql
    # And does not re-create it.
    assert not re.search(r"client_ip\s+text", sql)


def test_the_schema_is_no_longer_a_comment_in_core():
    """The comment was the definition, and it is what shipped a table with no RLS."""
    src = pathlib.Path("app/core.py").read_text()
    assert "create table conversations" not in src
    assert "db/conversations_schema.sql" in src


# ---------------- client_ip is not written ----------------

def test_the_insert_carries_no_client_ip(monkeypatch):
    sent = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    monkeypatch.setattr(core, "SUPABASE_URL", "https://db.example")
    monkeypatch.setattr(core, "SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(core.urllib.request, "urlopen",
                        lambda req, timeout=None: sent.update(
                            body=json.loads(req.data)) or _Resp())
    core.log_conversation("alice", "live", "What do you like?", "Robotics.")
    row = sent["body"][0]
    assert "client_ip" not in row
    assert row["userid"] == "alice"
    assert row["user_content"] == "Robotics."


def test_the_logging_signatures_no_longer_take_an_ip():
    """A parameter that is accepted but ignored is how a dropped field comes back."""
    import inspect
    assert "client_ip" not in inspect.signature(core.log_conversation).parameters
    assert "client_ip" not in inspect.signature(core.log_conversation_async).parameters


def test_no_caller_still_passes_an_ip_to_the_conversation_log():
    src = pathlib.Path("app/routes/ai.py").read_text()
    assert "log_conversation_async(userid, ip," not in src


# ---------------- the pseudonym ----------------

def test_the_same_account_gets_the_same_pseudonym():
    """The operational value of these lines is correlation, not identity."""
    assert core.pseudonym("alice") == core.pseudonym("alice")
    assert core.pseudonym("alice") != core.pseudonym("bob")


def test_it_is_case_and_whitespace_insensitive():
    assert core.pseudonym("  Alice  ") == core.pseudonym("alice")


def test_it_is_short_and_never_the_input():
    p = core.pseudonym("alice")
    assert len(p) == 8
    assert "alice" not in p


def test_an_empty_identity_reads_as_anon():
    assert core.pseudonym("") == core.pseudonym(None) == "anon"


def test_it_is_peppered_so_a_wordlist_does_not_reverse_it(monkeypatch):
    """Userids are short, guessable strings — often a first name — so an unsalted digest
    is reversible by anyone who can run sha256 over a wordlist."""
    before = core.pseudonym("alice")
    monkeypatch.setattr(core, "JWT_SECRET", "a-completely-different-secret")
    assert core.pseudonym("alice") != before


# ---------------- stdout is not a roster ----------------

def test_a_mock_send_does_not_print_the_address(monkeypatch, capsys):
    monkeypatch.setattr(es, "RESEND_API_KEY", "")
    monkeypatch.setattr(es, "render_for", lambda kind, rec: ("Subj", "<p>", "text"))
    out = es.send_lifecycle_email(
        "alice", "welcome", record={"userid": "alice", "email": "alice@school.edu"})
    printed = capsys.readouterr().out
    assert out["state"] == "mock"
    assert "alice@school.edu" not in printed
    assert core.pseudonym("alice") in printed


def test_the_response_still_carries_the_real_address(monkeypatch):
    """It goes to the localhost, token-gated console — not to a retained log."""
    monkeypatch.setattr(es, "RESEND_API_KEY", "")
    monkeypatch.setattr(es, "render_for", lambda kind, rec: ("Subj", "<p>", "text"))
    out = es.send_lifecycle_email(
        "alice", "welcome", record={"userid": "alice", "email": "alice@school.edu"})
    assert out["to"] == "alice@school.edu"


def test_a_failed_send_logs_the_email_sends_id_not_the_address(monkeypatch, capsys):
    monkeypatch.setattr(es, "RESEND_API_KEY", "key")
    monkeypatch.setattr(es, "render_for", lambda kind, rec: ("Subj", "<p>", "text"))
    monkeypatch.setattr(es, "_claim", lambda *a: ({"id": 4242}, None))
    monkeypatch.setattr(es, "_finish", lambda *a, **k: None)
    monkeypatch.setattr(es, "_resend_post", lambda *a: (None, "provider said no"))
    es.send_lifecycle_email("alice", "welcome",
                            record={"userid": "alice", "email": "alice@school.edu"})
    printed = capsys.readouterr().out
    assert "alice@school.edu" not in printed
    assert "4242" in printed


def test_the_mailing_list_warning_keeps_the_opportunity_id(monkeypatch, capsys):
    """The opportunity id is catalog data, not a person — and it plus the pseudonym is what
    makes the line actionable."""
    def _boom(*a, **k):
        raise RuntimeError("table missing")
    monkeypatch.setattr(mls, "_supabase_request", _boom)
    monkeypatch.setattr(mls, "_missing_table_error", lambda e: False)
    mls._record_subscription_attempt("alice", "opp-7", "a@b.c", "sent", "", "form", None)
    printed = capsys.readouterr().out
    assert "opp-7" in printed
    assert "alice" not in printed


def test_no_identity_bearing_print_survives_in_the_app():
    """The regression guard: a new `print(f"... {userid}")` fails here, not in production
    where Render would retain it."""
    offenders = []
    for path in list(pathlib.Path("app").rglob("*.py")):
        for i, line in enumerate(path.read_text().split("\n"), 1):
            if "print(" not in line:
                continue
            code = line.split("#", 1)[0]
            if "pseudonym(" in code:
                continue
            if re.search(r"\{\s*(userid|uid|email|key)\s*\}", code):
                offenders.append(f"{path}:{i}")
    assert not offenders, offenders
