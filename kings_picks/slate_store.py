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
) -> dict:
    """The stored-slate payload for one pull — everything
    the Dashboard, Props page and My Slip board need to
    re-render the pulled slate exactly, with no API call
    and no rescore. `pulled_at` is the pull's ISO timestamp;
    it is also the stamp the pull's pick-log entries carry,
    so the stored slate and the log tell the same time."""
    return {
        "version": 2,
        "pulled_at": pulled_at,
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
