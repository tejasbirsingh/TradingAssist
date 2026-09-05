"""Reminders for a selected IPO's application deadline.

An IPO window is days long, so this is date logic rather than a price watch: the
background watcher asks once an hour whether anything is due, which is ample.

Three moments earn a reminder, and each fires **exactly once**:

* ``opens``        -- the issue opened today
* ``closing-soon`` -- ``days_before`` days before it closes (default 1)
* ``closes``       -- the last day to apply

Firing once matters more than it sounds. A reminder that repeats every hour until
the window shuts is one you learn to dismiss without reading, which is worse than
no reminder at all.

The close date is re-read from the live feed when the issue is still listed, because
issues get extended and NSE is more current than whatever was stored at subscribe
time. When the issue has dropped out of the feed the stored date is used, so a
reminder cannot be lost just because NSE stopped advertising it.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

REMINDERS_PATH = Path(__file__).parent / "data" / "ipo_reminders.json"

REASONS = ("opens", "closing-soon", "closes")


class ReminderError(ValueError):
    """Rejected edit, with a message meant for the user."""


def _parse(text):
    """NSE's ``27-Aug-2026`` as a date, or None."""
    if not text or not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def load():
    if not REMINDERS_PATH.exists():
        return []
    try:
        stored = json.loads(REMINDERS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return stored if isinstance(stored, list) else []


def save(entries):
    REMINDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REMINDERS_PATH.write_text(json.dumps(entries, indent=2))


def add(entries, ipo, days_before=1):
    """Subscribe to reminders for one issue. Raises ReminderError if unusable."""
    symbol = str((ipo or {}).get("symbol") or "").strip().upper()
    closes = _parse((ipo or {}).get("closes"))
    if not symbol:
        raise ReminderError("That issue has no symbol to track.")
    if closes is None:
        raise ReminderError(
            "NSE has not published a closing date for that issue yet.")
    if any(r["symbol"] == symbol for r in entries or []):
        raise ReminderError(f"A reminder for {symbol} already exists.")

    return list(entries or []) + [{
        "symbol": symbol,
        "company": str(ipo.get("company") or symbol),
        "opens": ipo.get("opens"),
        "closes": ipo.get("closes"),
        "is_sme": bool(ipo.get("is_sme")),
        "days_before": max(0, int(days_before or 0)),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sent": [],
    }]


def remove(entries, symbol):
    symbol = str(symbol or "").strip().upper()
    kept = [r for r in entries or [] if r["symbol"] != symbol]
    if len(kept) == len(entries or []):
        raise ReminderError("No reminder is set for that issue.")
    return kept


def _describe(entry, reason, closes, today):
    """Subject and body for one due reminder."""
    symbol, company = entry["symbol"], entry.get("company") or entry["symbol"]
    left = (closes - today).days
    when = ("closes today" if left == 0 else
            "closes tomorrow" if left == 1 else
            f"closes in {left} days")
    headline = ("opened today" if reason == "opens" else when)

    body = [f"{symbol} — {company} {headline}."]
    if reason != "opens":
        body.append(f"Last day to apply: {closes:%d %b %Y}.")
    else:
        body.append(f"The issue closes {closes:%d %b %Y}.")
    if entry.get("is_sme"):
        body.append("This is an SME issue: the minimum application is much larger "
                    "than a mainboard issue and it will list far less liquid.")
    body.append("Apply through your broker — this dashboard cannot place "
                "applications.")
    return f"IPO reminder: {symbol} {headline}", " ".join(body)


def due(entries, ipos, today=None):
    """Reminders that should be sent now, newest reason first. Sends nothing."""
    today = today or date.today()
    live = {str(i.get("symbol") or "").strip().upper(): i for i in ipos or []}
    out = []

    for entry in entries or []:
        current = live.get(entry["symbol"])
        # The feed wins while the issue is listed; issues do get extended.
        closes = _parse((current or {}).get("closes")) or _parse(entry.get("closes"))
        opens = _parse((current or {}).get("opens")) or _parse(entry.get("opens"))
        if closes is None or today > closes:
            continue

        already = set(entry.get("sent") or [])
        for reason, when in (("opens", opens),
                             ("closing-soon",
                              closes - timedelta(days=entry.get("days_before", 1))),
                             ("closes", closes)):
            if when is None or reason in already or today != when:
                continue
            subject, body = _describe(entry, reason, closes, today)
            out.append({"symbol": entry["symbol"], "reason": reason,
                        "subject": subject, "body": body})
    return out


def mark_sent(entries, sent):
    """Record which reasons have gone out. Does not mutate the input."""
    by_symbol = {}
    for item in sent or []:
        by_symbol.setdefault(item["symbol"], set()).add(item["reason"])
    out = []
    for entry in entries or []:
        reasons = by_symbol.get(entry["symbol"])
        if reasons:
            entry = {**entry,
                     "sent": sorted(set(entry.get("sent") or []) | reasons)}
        out.append(entry)
    return out
