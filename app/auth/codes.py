"""Team access codes: generate, hash, verify.

Codes are random (~59 bits), printed on a card, typed at login. Because they are random
and long, a fast hash (SHA-256) is enough. Slow password hashes (bcrypt, argon2) exist to
protect short human-chosen passwords; these are not that.
The database stores only the hash, so a leaked table does not hand out logins.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# no 0/O, 1/I/L: codes are read off paper and typed by hand
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
GROUPS, GROUP_LEN = 3, 4


def generate_access_code() -> str:
    """e.g. 'K7QM-RX4T-9HCW' (12 symbols from 31 -> ~59 bits)."""
    chars = [secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN)]
    return "-".join("".join(chars[i : i + GROUP_LEN]) for i in range(0, len(chars), GROUP_LEN))


def normalise(code: str) -> str:
    """Accept lower case, spaces and missing dashes: 'k7qm rx4t 9hcw' -> 'K7QMRX4T9HCW'."""
    return "".join(ch for ch in code.upper() if ch in ALPHABET)


def hash_access_code(code: str) -> str:
    return hashlib.sha256(normalise(code).encode()).hexdigest()


def verify_access_code(code: str, stored_hash: str) -> bool:
    # compare_digest takes the same time whether the first or last character differs,
    # so response timing doesn't leak how close a guess was
    return hmac.compare_digest(hash_access_code(code), stored_hash)
