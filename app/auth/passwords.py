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

# ---------- Argon2id parameters (Phase 2 item 7) ----------
# The OWASP Password Storage Cheat Sheet's Argon2id recommendation, set explicitly. Until now
# this was a bare PasswordHasher(), i.e. argon2-cffi's library defaults, and the interesting
# thing is which way that moves: the defaults are HEAVIER than the recommendation, not lighter.
#
#   library default   m=65536 KiB (64 MiB)  t=3  p=4   ~46 ms/hash on this laptop
#   OWASP Argon2id    m=19456 KiB (19 MiB)  t=2  p=1   ~22 ms/hash
#
# So this makes sign-in about twice as fast and cuts its memory by ~70%. Two reasons that is
# the right direction here rather than a weakening:
#
#   1. MEMORY. Render Free is 512 MB. At 64 MiB per hash in flight, eight concurrent sign-ins
#      is the whole instance — an OOM reachable from an unauthenticated endpoint, which is a
#      worse property than any margin the higher cost buys. At 19 MiB that is ~27 concurrent
#      before the same point, and the login rate limiter caps the approach long before it.
#   2. PARALLELISM. p=4 asks for four lanes on an instance with 0.1 CPU. The lanes do not make
#      it faster there; they make each hash contend with itself and with every other request
#      on the box.
#
# The input is already a fixed-length client SHA-256 (see the module docstring), so the work
# factor is not defending a low-entropy human password here — it is defence in depth over a
# value that is already 256 bits of hash.
#
# Changing these is SAFE and self-migrating: argon2 encodes its parameters in the stored hash,
# verify_password below returns check_needs_rehash(), and the login route already rewrites the
# row when it does. Existing accounts move to the new parameters one successful sign-in at a
# time, exactly as the SHA-256 legacy rows did — nobody is locked out and nothing is migrated
# ahead of time.
ARGON2_TIME_COST = 2
ARGON2_MEMORY_COST_KIB = 19456          # 19 MiB
ARGON2_PARALLELISM = 1

_ph = PasswordHasher(time_cost=ARGON2_TIME_COST,
                     memory_cost=ARGON2_MEMORY_COST_KIB,
                     parallelism=ARGON2_PARALLELISM)

# The legacy stored value is exactly what crypto.subtle.digest('SHA-256') hex-encodes: 64
# lowercase hex chars. An argon2 hash starts with "$argon2", so the two never collide.
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def is_legacy_hash(stored):
    """True if `stored` is a bare client SHA-256 (pre-Phase-2) rather than an argon2 hash."""
    return bool(stored) and bool(_SHA256_HEX.match(stored))


def is_valid_client_hash(value):
    """True if `value` is the shape the client contract promises: 64 lowercase hex chars.

    S1-11, finding L1: nothing validated this server-side, so `passwordHash` was whatever
    the caller sent. It is hashed with argon2 and stored either way, so a client sending a
    one-character "hash" produced an account whose password-equivalent secret was one
    character — and the browser's own hashing was the only thing making that not happen.
    A server must not depend on its client for that.
    """
    return bool(value) and isinstance(value, str) and bool(_SHA256_HEX.match(value))


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
        # Bytes, not str: a non-ASCII `passwordHash` raises TypeError out of compare_digest,
        # which would 500 the login route rather than answering "incorrect".
        ok = hmac.compare_digest(stored.encode("utf-8"), client_hash.encode("utf-8"))
        return ok, ok

    try:
        _ph.verify(stored, client_hash)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False, False
    return True, _ph.check_needs_rehash(stored)
