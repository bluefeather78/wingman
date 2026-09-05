"""Server-side password hashing (docs/archive/PLAN_2_auth.md).

The client already SHA-256s the password before it leaves the browser (hashPassword in
script.js) and the server never sees plaintext — so "hash server-side" here means hashing
the *client hash*: we store argon2(passwordHash). That closes the real weakness (the old
stored value was the SHA-256 itself, i.e. password-equivalent — anyone reading the row
could replay it) without changing the client contract at all.

Migration with no lockout: existing rows hold the bare client SHA-256 (a 64-char hex
string). On the next successful login we detect that, verify by direct comparison, and
overwrite the row with argon2(passwordHash). Accounts upgrade transparently, one login at a
time; nothing has to be migrated ahead of time and no one is locked out.
"""
import hmac
import re

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

# Default parameters are fine for this workload — the input is already a fixed-length hash,
# and these accounts are low-value targets individually. Tuning is a later hardening step.
_ph = PasswordHasher()

# The legacy stored value is exactly what crypto.subtle.digest('SHA-256') hex-encodes: 64
# lowercase hex chars. An argon2 hash starts with "$argon2", so the two never collide.
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def is_legacy_hash(stored):
    """True if `stored` is a bare client SHA-256 (pre-Phase-2) rather than an argon2 hash."""
    return bool(stored) and bool(_SHA256_HEX.match(stored))


def hash_password(client_hash):
    """argon2-hash the client-supplied SHA-256 hex for storage."""
    return _ph.hash(client_hash)


def verify_password(stored, client_hash):
    """Check an incoming client SHA-256 against the stored value.

    Returns (ok, needs_upgrade):
      * ok            — the password matched.
      * needs_upgrade — the row should be rewritten with hash_password(client_hash). True
                        when a legacy SHA-256 row just verified (upgrade it to argon2), and
                        also when argon2's own parameters have moved on under it.

    A Google-only account (password_hash NULL/empty) never matches here — those sign in
    through the Google flow, which has no password to present.
    """
    if not stored or not client_hash:
        return False, False

    if is_legacy_hash(stored):
        # Constant-time compare so a legacy row can't be probed by timing. Match ⇒ upgrade.
        ok = hmac.compare_digest(stored, client_hash)
        return ok, ok

    try:
        _ph.verify(stored, client_hash)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False, False
    return True, _ph.check_needs_rehash(stored)
