"""Profiles — King & Kohen, each with a 4-digit PIN login.

Exactly two profiles exist: "King" (Neko) and "Kohen" (his son).

PINs are NEVER stored in this code, in tests, or anywhere in the
repo (the repo is public). The real PINs live only in Streamlit
secrets, set by Neko himself:

    KING_PIN  = "...."
    KOHEN_PIN = "...."

This module is pure logic — no Streamlit imports — so it can be
unit-tested with plain dicts standing in for st.secrets.

Per-profile records: every ledger entry carries a "profile" field.
Entries written before profiles existed have no such field and
count as King's (see DEFAULT_PROFILE).
"""

from __future__ import annotations

import hmac

PROFILES: list[str] = ["King", "Kohen"]

# Ledger entries with no "profile" field (legacy / demo data) are King's.
DEFAULT_PROFILE = "King"

# Profile name -> the Streamlit secrets key that holds its PIN.
SECRET_KEYS: dict[str, str] = {
    "King": "KING_PIN",
    "Kohen": "KOHEN_PIN",
}

# How each profile is labelled on buttons / in the sidebar.
PROFILE_LABELS: dict[str, str] = {
    "King": "👑 King",
    "Kohen": "Kohen",
}


def _clean(value) -> str:
    """Coerce a secret / entered value to a stripped string."""
    if value is None:
        return ""
    return str(value).strip()


def get_expected_pin(profile: str, secrets) -> str | None:
    """The configured PIN for a profile, or None if it is not set.

    `secrets` is any mapping-like object (a plain dict in tests,
    st.secrets in the app). Unknown profiles, missing keys and
    empty values all return None — never a guess, never a default.
    """
    key = SECRET_KEYS.get(profile)
    if key is None or secrets is None:
        return None
    try:
        value = secrets.get(key)
    except Exception:
        # st.secrets raises (instead of returning a default) when no
        # secrets exist at all. Treat that as "not set up".
        return None
    pin = _clean(value)
    return pin or None


def profiles_configured(secrets) -> bool:
    """True only when BOTH profiles have a PIN in secrets."""
    return all(get_expected_pin(p, secrets) for p in PROFILES)


def is_valid_pin_format(pin) -> bool:
    """A PIN entry must be exactly 4 digits (after stripping spaces)."""
    cleaned = _clean(pin)
    return len(cleaned) == 4 and cleaned.isdigit()


def verify_pin(profile: str, pin, secrets) -> bool:
    """True only when `pin` matches the profile's secret PIN.

    Requires exactly 4 digits at entry, strips whitespace, and
    compares in constant time. A profile with no configured PIN
    can never be logged into — there is no fallback.
    """
    expected = get_expected_pin(profile, secrets)
    if expected is None:
        return False
    if not is_valid_pin_format(pin):
        return False
    return hmac.compare_digest(_clean(pin), expected)


def normalize_profile(profile) -> str:
    """Anything that is not a known profile name counts as King's.

    Used when tagging and filtering ledger entries, so legacy data
    (no profile field) and stray values land in King's record.
    """
    return profile if profile in PROFILES else DEFAULT_PROFILE


def entry_profile(entry) -> str:
    """The profile a ledger entry belongs to.

    Accepts a LedgerParlay or a plain dict. Missing field -> King.
    """
    if isinstance(entry, dict):
        raw = entry.get("profile")
    else:
        raw = getattr(entry, "profile", None)
    return normalize_profile(raw)
