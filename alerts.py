"""Price alerts, evaluated on the server.

Server-side rather than in the browser so an alert fires whether or not a tab is
open, and **one-shot**: once a threshold is crossed the alert deactivates, because
a price that stays past the line would otherwise re-notify every few seconds.
Re-arm it explicitly to watch the same level again.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

ALERTS_PATH = Path(__file__).parent / "data" / "alerts.json"

DIRECTIONS = ("above", "below")


class AlertError(ValueError):
    """Rejected alert edit, with a message meant for the user."""


def _price(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def load():
    if not ALERTS_PATH.exists():
        return []
    try:
        stored = json.loads(ALERTS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return stored if isinstance(stored, list) else []


def save(entries):
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_PATH.write_text(json.dumps(entries, indent=2))


def add(entries, code, direction, price, note=None):
    """Append a new active alert. Raises AlertError on anything unusable."""
    code = (code or "").strip().upper()
    direction = (direction or "").strip().lower()
    level = _price(price)

    if not code:
        raise AlertError("Pick a stock.")
    if direction not in DIRECTIONS:
        raise AlertError("Direction must be 'above' or 'below'.")
    if level is None:
        raise AlertError("Enter a price greater than zero.")

    for existing in entries or []:
        if (existing["code"] == code and existing["direction"] == direction
                and abs(existing["price"] - level) < 1e-9 and existing.get("active")):
            raise AlertError(
                f"An active alert for {code} {direction} {level:g} already exists.")

    return list(entries or []) + [{
        "id": uuid.uuid4().hex[:12],
        "code": code,
        "direction": direction,
        "price": level,
        "note": (note or "").strip() or None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "triggered_at": None,
        "triggered_price": None,
        "active": True,
    }]


def remove(entries, alert_id):
    kept = [a for a in (entries or []) if a["id"] != alert_id]
    if len(kept) == len(entries or []):
        raise AlertError("That alert no longer exists.")
    return kept


def rearm(entries, alert_id):
    """Reactivate a fired alert so it watches the same level again."""
    out, found = [], False
    for a in (entries or []):
        if a["id"] == alert_id:
            found = True
            a = {**a, "active": True, "triggered_at": None, "triggered_price": None}
        out.append(a)
    if not found:
        raise AlertError("That alert no longer exists.")
    return out


def should_trigger(alert, price):
    """Whether ``alert`` fires at ``price``."""
    if not alert or not alert.get("active") or alert.get("triggered_at"):
        return False
    level = _price(alert.get("price"))
    current = _price(price)
    if level is None or current is None:
        return False
    if alert.get("direction") == "above":
        return current >= level
    if alert.get("direction") == "below":
        return current <= level
    return False


def evaluate(entries, prices, now=None):
    """Return (newly_fired, updated_entries). Does not mutate the input."""
    stamp = now or datetime.now().isoformat(timespec="seconds")
    fired, updated = [], []
    for alert in (entries or []):
        current = (prices or {}).get(alert.get("code"))
        if should_trigger(alert, current):
            hit = {**alert, "triggered_at": stamp,
                   "triggered_price": _price(current), "active": False}
            fired.append(hit)
            updated.append(hit)
        else:
            updated.append(alert)
    return fired, updated


def describe(alert, price):
    """One-line description used as the notification body."""
    level = alert.get("price")
    body = (f"{alert.get('code')} is {alert.get('direction')} "
            f"{level:g} — now {float(price):g}")
    if alert.get("note"):
        body += f" ({alert['note']})"
    return body
