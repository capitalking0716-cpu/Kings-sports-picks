"""Suite-wide isolation for the stored slate.

Several AppTests run the app in the repo working directory,
where the app's data dir is the real one — and a pull now
PERSISTS its slate: to data/current_slate.json (today's
mirror) and, since pick-the-date (2026-11-14), to
data/slates/<YYYY-MM-DD>.json for the day it pulled
(that is the feature). Without isolation, one test's
pulled slate would hydrate into the next test's fresh app
session and change what it renders. So: every test starts
with no stored slate in either store, and any slate a test
pulls in the repo data dir is removed afterwards. (Tests
that chdir into a tmp dir are unaffected — the paths are
resolved in whatever the working directory is when the
fixture runs.)
"""

from pathlib import Path

import pytest

_SLATE = Path("data/current_slate.json")
_DATED_SLATES = Path("data/slates")


def _clear_stored_slates() -> None:
    if _SLATE.exists():
        _SLATE.unlink()
    if _DATED_SLATES.is_dir():
        for path in _DATED_SLATES.glob("*.json"):
            path.unlink()


@pytest.fixture(autouse=True)
def _no_stored_slate_leak():
    _clear_stored_slates()
    yield
    _clear_stored_slates()
