"""Open and upcoming IPOs, from NSE's public JSON feeds.

Breeze has no IPO endpoint -- its 15 endpoints cover quotes, history, orders and
portfolio only -- so this is the one part of the app that does not come from the
broker. Two NSE feeds are merged:

* ``all-upcoming-issues?category=ipo`` -- the full list, EQ and SME
* ``ipo-current-issue``               -- adds live subscription figures

Being third-party, it is cached and degrades to a stale copy rather than
breaking the dashboard. Issue state is computed from the dates rather than
trusted from NSE's ``status`` field, which can lag.
"""

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

NSE_BASE = "https://www.nseindia.com"
UPCOMING_PATH = "/api/all-upcoming-issues?category=ipo"
CURRENT_PATH = "/api/ipo-current-issue"

CACHE_PATH = Path(__file__).parent / "data" / "ipos.json"
CACHE_TTL = 30 * 60          # IPO windows move in days, not seconds
REQUEST_TIMEOUT = 20

# NSE serves JSON only to browser-shaped requests.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{NSE_BASE}/market-data/all-upcoming-issues-ipo",
}

# How long a closed issue stays listed before being dropped.
KEEP_CLOSED_DAYS = 0

STATE_ORDER = {"OPEN": 0, "UPCOMING": 1, "UNKNOWN": 2, "CLOSED": 3}


def parse_nse_date(text):
    """Parse NSE's ``21-Aug-2026`` format. Returns None on anything else."""
    if not text or not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def classify(row, today):
    """OPEN / UPCOMING / CLOSED / UNKNOWN, decided by the issue dates."""
    start = parse_nse_date(row.get("issueStartDate"))
    end = parse_nse_date(row.get("issueEndDate"))
    if start is None or end is None:
        return "UNKNOWN"
    if today < start:
        return "UPCOMING"
    if today > end:
        return "CLOSED"
    return "OPEN"


def day_counts(row, today):
    """Days until close (inclusive) and days until it opens."""
    start = parse_nse_date(row.get("issueStartDate"))
    end = parse_nse_date(row.get("issueEndDate"))
    state = classify(row, today)
    return {
        "days_left": (end - today).days if end and state == "OPEN" else None,
        "days_to_start": (start - today).days if start and state == "UPCOMING" else None,
    }


def subscription_times(row):
    """How many times the issue is subscribed, or None."""
    raw = row.get("noOfTime")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_price_band(text):
    """``Rs.750 to Rs.788`` -> (750.0, 788.0). A single price gives (p, p)."""
    if not text or not isinstance(text, str):
        return None
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    return (numbers[0], numbers[-1])


def merge_feeds(upcoming, current):
    """One row per symbol, with subscription figures folded in where present."""
    merged = {}
    for row in (upcoming or []):
        symbol = row.get("symbol")
        if symbol:
            merged[symbol] = dict(row)
    for row in (current or []):
        symbol = row.get("symbol")
        if not symbol:
            continue
        merged.setdefault(symbol, {}).update(row)
    for symbol, row in merged.items():
        row["symbol"] = symbol
        row["subscription_times"] = subscription_times(row)
    return list(merged.values())


def sort_rows(rows):
    """Open issues first (closing soonest), then upcoming (starting soonest)."""
    def key(row):
        state = row.get("state", "UNKNOWN")
        return (
            STATE_ORDER.get(state, 9),
            row.get("days_left") if row.get("days_left") is not None else
            (row.get("days_to_start") if row.get("days_to_start") is not None else 999),
            row.get("symbol") or "",
        )
    return sorted(rows, key=key)


def build(upcoming, current, today=None):
    """Merge, classify and shape the feeds into rows for the dashboard."""
    today = today or date.today()
    out = []
    for row in merge_feeds(upcoming, current):
        state = classify(row, today)
        counts = day_counts(row, today)
        end = parse_nse_date(row.get("issueEndDate"))
        if state == "CLOSED" and end and (today - end).days > KEEP_CLOSED_DAYS:
            continue
        band = parse_price_band(row.get("issuePrice"))
        series = (row.get("series") or "").upper()
        out.append({
            "symbol": row.get("symbol"),
            "company": row.get("companyName"),
            "state": state,
            "nse_status": row.get("status"),
            "series": series,
            "is_sme": series == "SME",
            "lot_size": row.get("lotSize"),
            "price_display": row.get("issuePrice"),
            "price_low": band[0] if band else None,
            "price_high": band[1] if band else None,
            "opens": row.get("issueStartDate"),
            "closes": row.get("issueEndDate"),
            "subscription_times": row.get("subscription_times"),
            **counts,
        })
    return sort_rows(out)


def _get(path):
    request = Request(NSE_BASE + path, headers=HEADERS)
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def load(today=None, fetch=None, use_cache=True):
    """IPO rows plus provenance. Falls back to a stale cache on failure."""
    fetch = fetch or _get

    if use_cache and CACHE_PATH.exists():
        try:
            blob = json.loads(CACHE_PATH.read_text())
            if time.time() - blob.get("fetched_at", 0) < CACHE_TTL:
                return {**blob, "stale": False}
        except (json.JSONDecodeError, OSError):
            pass

    try:
        rows = build(fetch(UPCOMING_PATH), fetch(CURRENT_PATH), today=today)
    except Exception as exc:
        # Third-party source: prefer stale data over an empty section.
        if CACHE_PATH.exists():
            try:
                blob = json.loads(CACHE_PATH.read_text())
                return {**blob, "stale": True, "error": f"NSE unreachable: {exc}"}
            except (json.JSONDecodeError, OSError):
                pass
        return {"ipos": [], "fetched_at": None, "source": "NSE",
                "stale": False, "error": f"NSE unreachable: {exc}"}

    blob = {"ipos": rows, "fetched_at": time.time(), "source": "NSE",
            "error": None}
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(blob))
    return {**blob, "stale": False}
