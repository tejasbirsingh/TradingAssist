"""User-editable watchlist, persisted to disk.

Codes are validated against the NSE security master before being accepted. That
matters twice over: a typo would otherwise sit in the watchlist producing a
broken row forever, and matching on company name has already been shown to pick
the wrong instrument (searching "Axis Bank" finds AXBETF, an ETF).

``app_config.WATCHLIST`` is only the initial seed; once saved, the file wins.
"""

import json
from pathlib import Path

import app_config

WATCHLIST_PATH = Path(__file__).parent / "data" / "watchlist.json"

# Instrument types that are not plain equity.
EXCLUDE_TOKENS = ("ETF", " OFS", "DVR", "PARTLY", "RIGHTS", "WARRANT")

SEARCH_LIMIT = 10


class WatchlistError(ValueError):
    """Rejected watchlist edit, with a message meant for the user."""


def _index():
    """Security-master mapping of ShortName -> CompanyName (EQ series only)."""
    from universe import master_index
    return master_index()


def _is_equity(name):
    return not any(token in name for token in EXCLUDE_TOKENS)


def _label(name):
    return name.title()


def load():
    """Current watchlist, seeded from app_config on first run."""
    if WATCHLIST_PATH.exists():
        try:
            saved = json.loads(WATCHLIST_PATH.read_text())
            if isinstance(saved, list):
                return [e for e in saved if e.get("code")]
        except (json.JSONDecodeError, OSError):
            pass          # fall through to the seed rather than break the page
    return [dict(e) for e in app_config.WATCHLIST]


def save(entries):
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(json.dumps(entries, indent=2))


def add(code, entries, index=None):
    """Return a new list with ``code`` appended. Raises on anything suspect."""
    index = _index() if index is None else index
    code = (code or "").strip().upper()
    if not code:
        raise WatchlistError("Enter a stock code.")

    if any(e["code"].upper() == code for e in entries):
        raise WatchlistError(f"{code} is already in the watchlist.")

    name = index.get(code)
    if name is None:
        raise WatchlistError(
            f"{code} is not a tradable NSE equity code. Search by company name "
            "instead — the Breeze code is often not the chart ticker."
        )
    if not _is_equity(name):
        raise WatchlistError(
            f"{code} is '{_label(name)}', which is not plain equity."
        )
    return list(entries) + [{"code": code, "label": _label(name)}]


def replace(codes, index=None):
    """Build a fresh watchlist from ``codes``. Returns (entries, rejected).

    Used when syncing a list down from another device: one stale code that no
    longer resolves must not discard the whole sync, so invalid codes are
    reported rather than raised.
    """
    index = _index() if index is None else index
    entries, rejected, seen = [], [], set()
    for raw in (codes or []):
        code = (raw or "").strip().upper()
        if not code or code in seen:
            continue
        name = index.get(code)
        if name is None or not _is_equity(name):
            rejected.append(code)
            continue
        seen.add(code)
        entries.append({"code": code, "label": _label(name)})
    return entries, rejected


def remove(code, entries):
    """Return a new list without ``code``."""
    code = (code or "").strip().upper()
    kept = [e for e in entries if e["code"].upper() != code]
    if len(kept) == len(entries):
        raise WatchlistError(f"{code} is not in the watchlist.")
    return kept


def search(query, index=None, limit=SEARCH_LIMIT):
    """Find equities by code or company name, best matches first."""
    index = _index() if index is None else index
    q = (query or "").strip().upper()
    if not q:
        return []

    scored = []
    for code, name in index.items():
        if not _is_equity(name):
            continue
        if code == q:
            rank = 0
        elif code.startswith(q):
            rank = 1
        elif name.startswith(q):
            rank = 2
        elif q in name or q in code:
            rank = 3
        else:
            continue
        scored.append((rank, len(name), code, name))

    scored.sort()
    return [{"code": c, "label": _label(n)} for _, _, c, n in scored[:limit]]
