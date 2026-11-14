"""Word (.docx) export of one profile's pick log — the
Results/Ledger record as a file Neko can open and read back
on his phone.

Neko's order (2026-11-14): "My logs should all be word files
I can Read back."

What this module is:

- The Word twin of the parlay-ledger backup export on the
  Results page: the CURRENT profile's recorded tickets
  (kings_picks/ledger.py LedgerParlay entries, already
  filtered to the profile by Ledger.parlays_for_profile),
  one section per ticket — the date it was built, the
  ticket's name, every leg with its line (the leg label
  carries player, prop, side and line), the leg's price,
  and the result in plain words: Won / Lost / Still open.
  A ticket's own result line says whether the whole ticket
  won, lost, or is still waiting on legs.
- Built from the same typed blocks as the picks download
  and rendered by the SAME renderer
  (kings_picks/picks_export.py render_docx), so every Word
  file Lilly hands over reads the same way. Pure builder:
  no file IO, no Odds API call, nothing rescored — it only
  reads the ledger entries it is handed.
- Honest limits: the stored ledger does not record which
  sportsbook priced each leg (the book lives on the live
  pull's props and is not persisted), so the file names no
  book rather than inventing one. Empty record -> b"" —
  no tickets, no file — and the app says so where the
  button would be.
"""

from __future__ import annotations

from datetime import date

from kings_picks.picks_export import render_docx

_RESULT_WORDS = {
    "won": "Won",
    "lost": "Lost",
    "pending": "Still open",
}


def _date_words(iso: str) -> str:
    """'Sat Nov 15, 2026' from an ISO date; the raw text
    when it does not parse — never an invented date."""
    try:
        return date.fromisoformat(iso).strftime("%a %b %d, %Y")
    except (ValueError, TypeError):
        return str(iso or "")


def _ticket_result(parlay) -> str:
    """The whole ticket's state in one plain line, from its
    legs' results: every leg in -> Won or Lost; anything
    still pending -> Still open, with the count so far."""
    won = parlay.picks_won
    lost = parlay.picks_lost
    pending = parlay.picks_pending
    if pending:
        return (
            f"Ticket result: Still open — {won} won, {lost} lost, "
            f"{pending} still to play."
        )
    if lost == 0:
        return "Ticket result: ✅ Won — every pick on the ticket won."
    return "Ticket result: ❌ Lost."


def _log_blocks(parlays, profile_label: str) -> list[tuple[str, str]]:
    entries = list(parlays or [])
    if not entries:
        return []
    settled = sum(1 for lp in entries if lp.settled)
    won_tickets = sum(1 for lp in entries if lp.settled and lp.won_all)
    blocks: list[tuple[str, str]] = [
        ("title", "👑 KINGS SPORTS BOOKS PICKS"),
        ("heading", f"📒 {profile_label}'s pick log"),
        (
            "body",
            f"{len(entries)} tickets on this record · {settled} settled · "
            f"{won_tickets} won. Every pick is listed with its line "
            "and its result — Won, Lost, or Still open.",
        ),
        ("blank", ""),
    ]
    for parlay in entries:
        blocks.append(
            ("ticket", f"{_date_words(parlay.date)} — {parlay.label}")
        )
        blocks.append(("body", _ticket_result(parlay)))
        blocks.append(
            (
                "body",
                f"Lilly's chance for the ticket: "
                f"{parlay.overall_probability:.0%}.",
            )
        )
        blocks.append(("blank", ""))
        for index, pick in enumerate(parlay.picks, 1):
            result = _RESULT_WORDS.get(pick["result"], "Still open")
            blocks.append(("leg", f"{index}. {pick['label']}"))
            blocks.append(
                (
                    "detail",
                    f"Price: {int(pick['odds']):+d} · Lilly's chance: "
                    f"{pick['probability']:.0%} · Result: {result}",
                )
            )
        blocks.append(("blank", ""))
    blocks += [
        ("rule", ""),
        (
            "body",
            "This is your record exactly as saved in the app. Settle "
            "open picks on the Results page after the games, then "
            "download this file again so your copy is up to date.",
        ),
    ]
    return blocks


def build_log_docx(parlays, profile_label: str) -> bytes:
    """The profile's pick log as a Word file. `parlays` are
    LedgerParlay entries already filtered to the profile
    (Ledger.parlays_for_profile); `profile_label` is the
    display name ("👑 King"). b"" when the record is empty."""
    return render_docx(_log_blocks(parlays, profile_label))
