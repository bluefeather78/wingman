"""Unit tests for app.core consent/email helpers and app.config.EMAIL_RE.

All pure functions — no seams to mock.
"""
import pytest

from app.core import (
    _check_signup_consent, normalize_email, _is_email_conflict, extract_qa_pair,
)
from app.config import EMAIL_RE


# ---------- _check_signup_consent (truth table) ----------

@pytest.mark.parametrize("is_adult,parental,terms,ok", [
    (True, False, True, True),    # adult, accepted -> ok
    (False, True, True, True),    # minor with parental consent -> ok
    (True, True, True, True),
    (True, False, False, False),  # no terms -> error regardless
    (False, True, False, False),  # no terms wins over having consent
    (False, False, True, False),  # minor, no parental consent -> error
    (False, False, False, False),
])
def test_check_signup_consent(is_adult, parental, terms, ok):
    result = _check_signup_consent(is_adult, parental, terms)
    if ok:
        assert result is None
    else:
        assert isinstance(result, str) and result


def test_consent_no_terms_message_mentions_terms():
    msg = _check_signup_consent(True, False, False)
    assert "Terms of Use" in msg


def test_consent_minor_message_mentions_guardian():
    msg = _check_signup_consent(False, False, True)
    assert "parent or guardian" in msg


# ---------- normalize_email ----------

@pytest.mark.parametrize("raw,expected", [
    ("  Foo@Bar.COM ", "foo@bar.com"),
    ("Already@Lower.io", "already@lower.io"),
    (None, ""),
    ("", ""),
    ("   ", ""),
])
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


# ---------- _is_email_conflict ----------

def test_is_email_conflict_wrong_code():
    assert _is_email_conflict({"code": "23503", "message": "email"}) is False
    assert _is_email_conflict({}) is False


def test_is_email_conflict_by_index_name():
    detail = {"code": "23505", "message": "duplicate key value",
              "details": "Key (lower(email))=(x) already exists.",
              "hint": "users_email_lower_key"}
    assert _is_email_conflict(detail) is True


def test_is_email_conflict_by_email_word():
    detail = {"code": "23505", "message": "duplicate email address"}
    assert _is_email_conflict(detail) is True


def test_is_email_conflict_pk_collision_is_false():
    # A userid/PK 23505 with no mention of email -> not an email conflict.
    detail = {"code": "23505", "message": "users_pkey duplicate",
              "details": "Key (userid)=(bob) already exists.", "hint": None}
    assert _is_email_conflict(detail) is False


# ---------- extract_qa_pair ----------

def _content(convo):
    return f"stuff\nCONVERSATION SO FAR:\n{convo}\nRespond with the next question."


def test_qa_no_conversation_marker():
    assert extract_qa_pair("no marker here at all") == (None, None)


def test_qa_empty_conversation():
    assert extract_qa_pair(_content("")) == (None, None)


def test_qa_nothing_yet():
    assert extract_qa_pair(_content("(nothing yet)")) == (None, None)


def test_qa_question_and_answer():
    q, a = extract_qa_pair(_content("You: What clubs are you in?\nStudent: Robotics club"))
    assert q == "What clubs are you in?"
    assert a == "Robotics club"


def test_qa_answer_only_no_prior_you_line():
    # Last line is a student answer but the preceding line isn't a "You:" question.
    q, a = extract_qa_pair(_content("Student: Robotics club"))
    assert q is None
    assert a == "Robotics club"


def test_qa_last_line_not_student():
    assert extract_qa_pair(_content("You: q\nBot: not a student line")) == (None, None)


def test_qa_empty_answer_rejected():
    assert extract_qa_pair(_content("You: q?\nStudent:    ")) == (None, None)


def test_qa_case_insensitive_labels():
    q, a = extract_qa_pair(_content("you: hey?\nSTUDENT: yes"))
    assert q == "hey?"
    assert a == "yes"


# ---------- EMAIL_RE (config) ----------

@pytest.mark.parametrize("email", [
    "a@b.co",
    "user.name@example.com",
    "user_name@example.io",   # underscore is legitimate (and would be an ILIKE wildcard)
    "a+tag@sub.domain.org",
])
def test_email_re_accepts(email):
    assert EMAIL_RE.match(email)


@pytest.mark.parametrize("email", [
    "a b@c.co",       # space
    "a@@b.co",        # double @
    "a,b@c.co",       # comma
    "a(b@c.co",       # paren
    'a"b@c.co',       # double quote
    "a'b@c.co",       # single quote
    "a@bc",           # no dot
    "a@b.c",          # TLD only 1 char
    "@b.co",          # empty local part
    "a@.co",          # empty domain label
    "plainaddress",
    "",
])
def test_email_re_rejects(email):
    assert EMAIL_RE.match(email) is None
