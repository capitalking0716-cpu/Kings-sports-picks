"""Suite-wide isolation for the stored slate.

Several AppTests run the app in the repo working directory,
where the app's data dir is the real one — and a pull now
PERSISTS its slate to data/current_slate.json (that is the
feature). Without isolation, one test's pulled slate would
hydrate into the next test's fresh app session and change
what it renders. So: every test starts with no stored slate,
and any slate a test pulls in the repo data dir is removed
afterwards. (Tests that chdir into a tmp dir are unaffected —
the path is resolved in whatever the working directory is
when the fixture runs.)
"""

from pathlib import Path

import pytest

_SLATE = Path("data/current_slate.json")


@pytest.fixture(autouse=True)
def _no_stored_slate_leak():
    if _SLATE.exists():
        _SLATE.unlink()
    yield
    if _SLATE.exists():
        _SLATE.unlink()
