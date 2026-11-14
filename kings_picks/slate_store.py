"""The stored slate — a pulled slate PERSISTS until the next pull.

Neko's order (2026-11-12): "when I pull the odds it stores
till I pull it again." Before this module, the scored slate —
the props board, Lilly's parlays AND the 👑 King-style
parlays, i.e. the picks exactly as he sees them — lived only
in Streamlit session state. An app reload or a Cloud
sleep-wake lost it or rebuilt it, which confused him and
burned Odds API quota: a live pull day costs ~40 of the free
tier's 500 monthly credits, so re-pulling just to SEE the
same picks again is real money out of a free budget.

Now a pull is an event with a durable result:

- The pull (fetch + score + build, in the app) saves its
  whole renderable result here, to data/current_slate.json —
  the raw props, the scored props, the standard parlays, the
  King parlays, the note, and the pull's timestamp.
- Every later app load renders THAT stored slate — Dashboard,
  Props page and My Slip board alike, one source of truth —
  with NO Odds API call and NO rescore. The picks display is
  the pulled display, byte for byte, until he pulls again.
- Only an explicit new pull overwrites the store. A stored
  slate is never auto-expired by date; its pull time is shown
  prominently instead, so staleness is his call to make.

The store only ever holds LIVE slates. Demo mode was removed
from the app entirely on 2026-11-12 (Neko: "the app is live,
it's only for me and my kid, we don't need the reminder"), so
there is no demo slate to store — and if a file written by
the short-lived demo-aware build ever shows up here with a
demo payload, load() purges it rather than render sample
picks as if they were real.

The pick log is untouched by all of this: a pull still logs
its picks exactly as before (with the same timestamp the
slate carries), and because the store is written from the
very objects that were logged, the stored render and the
pick log agree after a pull.

The store is deliberately dumb: plain JSON, tolerant reads
(a missing or corrupt file is "no stored slate", never a
crash), same persistence style as SlipBook / PickLog.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields as _dataclass_fields
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    _DETROIT = ZoneInfo("America/Detroit")
except Exception:  # no tz database on this machine — fail soft
    _DETROIT = None

from .model import Prop, ScoredProp
from .parlays import Parlay

_SCORED_FIELDS = {f.name for f in _dataclass_fields(ScoredProp)}
_PROP_FIELDS = {f.name for f in _dataclass_fields(Prop)}

# The payload keys, for the record: version, pulled_at,
# note, king_info, props (raw, as pulled), scored (the
# board), parlays, king_parlays.


# ---------- serialisation ----------


def prop_to_dict(prop) -> dict:
    """A Prop / ScoredProp as a plain dict.

    Uses the instance dict, not dataclasses.asdict, on
    purpose: King-style legs carry two annotations that are
    NOT dataclass fields (base_line, king_note — set on the
    object by kings_picks/king_style.py), and the stored
    render must reproduce them or a dropped leg would lose
    its "(dropped from 24.5)" label on reload.
    """
    return dict(prop.__dict__)


def prop_from_dict(data, scored: bool = True):
    """Rebuild a Prop (scored=False) or ScoredProp from a
    stored dict. Known dataclass fields go to the
    constructor; anything else (base_line, king_note) is set
    back on the object afterwards. Returns None for a row
    that can't be rebuilt — one bad row never sinks a slate.
    """
    if not isinstance(data, dict):
        return None
    cls = ScoredProp if scored else Prop
    known = _SCORED_FIELDS if scored else _PROP_FIELDS
    kwargs = {k: v for k, v in data.items() if k in known}
    try:
        obj = cls(**kwargs)
    except Exception:
        return None
    for key, value in data.items():
        if key not in known:
            setattr(obj, key, value)
    return obj


def parlay_to_dict(parlay) -> dict:
    return {
        "size": parlay.size,
        "label": parlay.label,
        "style": getattr(parlay, "style", "standard") or "standard",
        "overall_probability": parlay.overall_probability,
        "decimal_payout": parlay.decimal_payout,
        "expected_value_per_dollar": parlay.expected_value_per_dollar,
        "picks": [prop_to_dict(p) for p in parlay.picks],
    }


def parlay_from_dict(data):
    """Rebuild a Parlay (with ScoredProp legs) or None."""
    if not isinstance(data, dict) or not isinstance(data.get("picks"), list):
        return None
    picks = [
        p for p in (prop_from_dict(row) for row in data["picks"]) if p is not None
    ]
    try:
        return Parlay(
            size=int(data.get("size") or len(picks)),
            picks=picks,
            overall_probability=float(data.get("overall_probability") or 0.0),
            decimal_payout=float(data.get("decimal_payout") or 0.0),
            expected_value_per_dollar=float(
                data.get("expected_value_per_dollar") or 0.0
            ),
            label=str(data.get("label") or ""),
            style=str(data.get("style") or "standard"),
        )
    except Exception:
        return None


def build_payload(
    *,
    pulled_at: str,
    note: str,
    raw_props,
    scored,
    parlays,
    king_parlays,
    king_info=None,
    game_date: str = "",
) -> dict:
    """The stored-slate payload for one pull — everything
    the Dashboard, Props page and My Slip board need to
    re-render the pulled slate exactly, with no API call
    and no rescore. `pulled_at` is the pull's ISO timestamp;
    it is also the stamp the pull's pick-log entries carry,
    so the stored slate and the log tell the same time.
    `game_date` is the calendar day (America/Detroit,
    YYYY-MM-DD) the slate's GAMES are played on — the day
    the user picked for the pull (2026-11-14: pick the
    date, pre-build ahead). Payloads written before game
    dates existed simply carry ""."""
    return {
        "version": 2,
        "pulled_at": pulled_at,
        "game_date": game_date,
        "note": note,
        "king_info": dict(king_info or {}),
        "props": [prop_to_dict(p) for p in raw_props or []],
        "scored": [prop_to_dict(p) for p in scored or []],
        "parlays": [parlay_to_dict(p) for p in parlays or []],
        "king_parlays": [parlay_to_dict(p) for p in king_parlays or []],
    }


def restore_slate(payload) -> dict | None:
    """A stored payload back into renderable objects:
    {note, pulled_at, king_info, raw_props, scored, parlays,
    king_parlays}. None when the payload isn't a slate at
    all, or is a leftover DEMO payload (demo mode is gone —
    a demo slate is never rendered). Individual bad rows
    inside are dropped, never fatal.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("mode") == "demo":
        return None

    def _props(key, scored):
        rows = payload.get(key)
        if not isinstance(rows, list):
            return []
        return [p for p in (prop_from_dict(r, scored=scored) for r in rows) if p]

    def _parlays(key):
        rows = payload.get(key)
        if not isinstance(rows, list):
            return []
        return [p for p in (parlay_from_dict(r) for r in rows) if p]

    king_info = payload.get("king_info")
    return {
        "note": str(payload.get("note") or ""),
        "pulled_at": str(payload.get("pulled_at") or ""),
        "game_date": payload_game_date(payload),
        "king_info": king_info if isinstance(king_info, dict) else {},
        "raw_props": _props("props", scored=False),
        "scored": _props("scored", scored=True),
        "parlays": _parlays("parlays"),
        "king_parlays": _parlays("king_parlays"),
    }


def format_pulled_at(iso: str) -> str:
    """'2026-11-12T21:37:00+00:00' -> 'Thu Nov 12, 2026 ·
    04:37 PM ET' — pull times are shown in America/Detroit,
    where Neko is. Unparseable input formats as "" — the
    caption then just says a slate is stored, with no
    invented time.
    """
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if moment.tzinfo is not None and _DETROIT is not None:
        moment = moment.astimezone(_DETROIT)
    return moment.strftime("%a %b %d, %Y · %I:%M %p ET")


# ---------- game dates (pick-the-date, 2026-11-14) ----------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_date_string(value) -> bool:
    """True for a real YYYY-MM-DD calendar date — the only
    shape a slate's game date (and a per-date filename) is
    ever allowed to take."""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def format_game_date(iso_date: str) -> str:
    """'2026-11-16' -> 'Monday, November 16, 2026' — the
    plain way a slate's game day is named everywhere (the
    Dashboard banner, the tickets, the download). "" for
    anything that isn't a date."""
    if not is_date_string(iso_date):
        return ""
    moment = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"{moment.strftime('%A, %B')} {moment.day}, {moment.year}"


def _detroit_date_of(iso_value) -> str:
    """The America/Detroit calendar date (YYYY-MM-DD) of an
    ISO timestamp, or "" when it can't be read."""
    try:
        moment = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if moment.tzinfo is not None and _DETROIT is not None:
        moment = moment.astimezone(_DETROIT)
    return moment.date().isoformat()


def payload_game_date(payload) -> str:
    """Which game day a stored payload belongs to.

    The payload's own game_date when the pull recorded one.
    Older payloads (written before game dates existed) are
    dated by their games instead: the Detroit date of the
    earliest commence time among the stored raw props,
    falling back to the pull's own Detroit date. "" when
    nothing about the payload says — callers treat that as
    "the day it has always been shown for", never a guess
    presented as fact."""
    if not isinstance(payload, dict):
        return ""
    recorded = payload.get("game_date")
    if is_date_string(recorded):
        return recorded
    commences = sorted(
        str(row.get("commence_time"))
        for row in payload.get("props") or []
        if isinstance(row, dict) and row.get("commence_time")
    )
    for commence in commences:
        day = _detroit_date_of(commence)
        if day:
            return day
    return _detroit_date_of(payload.get("pulled_at"))


# ---------- the store ----------


class SlateStore:
    """The one stored LIVE slate, saved to disk.

    data/current_slate.json holds the build_payload dict —
    plain JSON, so the file doubles as a readable record of
    the slate Lilly is currently showing.
    """

    def __init__(self, path: Path | str = "data/current_slate.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, payload: dict) -> None:
        self.path.write_text(json.dumps(payload, indent=2))

    def load(self) -> dict | None:
        """The stored payload, or None — a missing file, an
        unreadable file, or JSON that isn't an object all
        mean "no stored slate", never a crash. A leftover
        DEMO payload is purged (file deleted, None returned):
        demo mode is gone, and sample picks must never render
        as a stored slate."""
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text())
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        if raw.get("mode") == "demo":
            self.clear()
            return None
        return raw

    def exists(self) -> bool:
        return self.load() is not None

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass


class DatedSlateStore:
    """One stored slate PER GAME DATE (2026-11-14: pick the
    date, pre-build the picks).

    data/slates/<YYYY-MM-DD>.json — the same build_payload
    dict SlateStore holds, one file per game day. Pulling
    Sunday's games writes Sunday's file and leaves today's
    untouched; switching the app's date picker to a day with
    a stored slate re-renders it with ZERO Odds API calls,
    exactly like the single store, just keyed by date.

    The single SlateStore (data/current_slate.json) still
    exists as today's mirror — older flows and older
    payloads keep working — but this book is the source the
    date picker reads. Same tolerance rules: a missing or
    corrupt file is "no slate for that date", never a
    crash, and a leftover demo payload is purged on sight.
    """

    def __init__(self, directory: Path | str = "data/slates"):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, date_iso: str) -> Path | None:
        if not is_date_string(date_iso):
            return None
        return self.dir / f"{date_iso}.json"

    def save(self, date_iso: str, payload: dict) -> bool:
        """Store one date's slate. False (nothing written)
        for a date string that isn't a real YYYY-MM-DD day —
        a date is a filename here, so nothing else is ever
        allowed to become one."""
        path = self._path(date_iso)
        if path is None:
            return False
        path.write_text(json.dumps(payload, indent=2))
        return True

    def load(self, date_iso: str) -> dict | None:
        """One date's stored payload, or None — same rules
        as SlateStore.load, including the demo purge."""
        path = self._path(date_iso)
        if path is None or not path.exists():
            return None
        try:
            raw = json.loads(path.read_text())
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        if raw.get("mode") == "demo":
            self.clear(date_iso)
            return None
        return raw

    def exists(self, date_iso: str) -> bool:
        return self.load(date_iso) is not None

    def clear(self, date_iso: str) -> None:
        path = self._path(date_iso)
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    def dates(self) -> list[str]:
        """Every game date with a stored slate, earliest
        first — for showing which days are already pulled."""
        if not self.dir.exists():
            return []
        return sorted(
            path.stem for path in self.dir.glob("*.json") if is_date_string(path.stem)
        )
