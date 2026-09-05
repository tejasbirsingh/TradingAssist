"""Split and bonus adjustment for daily history.

Breeze returns prices exactly as traded, so a share subdivision reads as a
collapse. RELIND closed 2655.70 on 2024-10-25 and 1334.35 on 2024-10-28 after a
1:1 bonus, and ``momentum_score`` measured that as a 50% loss -- nothing was
lost, one share became two. Left uncorrected it put KOTMAH last of 39 on a
-80.7% "return" that was really -3.6%, and because momentum is a *rank*, that
displaced a further ten names whose own scores were correct.

Ratios come from Yahoo via ``yfinance``, which publishes the authoritative
effective date and factor. Inferring them from the price step was rejected: a
genuine 50% one-day collapse is rare but real, and guessing would erase it.

Nothing here runs on the live path. ``fetch``/``refresh`` touch the network and
are called only by ``refresh_splits.py``; the request path reads the cached file,
and a missing entry means "no adjustment" rather than an error.
"""

import csv
import io
import json
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
SPLITS_PATH = DATA_DIR / "splits.json"
MASTER_PATH = DATA_DIR / "NSEScripMaster.txt"


def _as_date(value):
    """First 10 chars of an ISO-ish string as a date, or None."""
    try:
        parts = str(value)[:10].split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (TypeError, ValueError, IndexError):
        return None


def _as_ratio(value):
    """A usable split factor, or None. 1.0 is a no-op and treated as unusable."""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    return ratio if ratio > 0 and abs(ratio - 1.0) > 1e-9 else None


def _usable(events):
    """(date, ratio) pairs worth applying, malformed entries dropped."""
    out = []
    for event in events or []:
        if not isinstance(event, (list, tuple)) or len(event) < 2:
            continue
        when, ratio = _as_date(event[0]), _as_ratio(event[1])
        if when and ratio:
            out.append((when, ratio))
    return out


STEP_TOLERANCE = 0.15


def applicable_events(bars, events):
    """The (iso_date, ratio) events this series has NOT already been restated for.

    Breeze is inconsistent about corporate actions: of 59 events across the cached
    universe, 27 leave the raw overnight step in place and 31 arrive already
    adjusted. RELIND's 1:1 bonus steps 2655.70 -> 1334.35, while TATSTE's 10:1
    split runs straight through 96.04 -> 100.20. Trusting the ratio blindly
    therefore divides a third of the series twice, which is worse than raw.

    So the series is asked rather than assumed: an event counts only if the close
    either side of it actually moves by about the ratio. Events outside the data
    are skipped, which is also what makes a bad ratio on pre-history dates
    harmless.
    """
    usable, out = _usable(events), []
    if not bars or not usable:
        return out

    dated = []
    for bar in bars:
        when = _as_date(bar.get("datetime") or bar.get("date"))
        close = bar.get("close")
        if when and isinstance(close, (int, float)) and close:
            dated.append((when, float(close)))
    dated.sort()

    for event_date, ratio in usable:
        before = [c for d, c in dated if d < event_date]
        after = [c for d, c in dated if d >= event_date]
        if not before or not after:
            continue                      # nothing on one side to compare
        observed = before[-1] / after[0]
        if abs(observed - ratio) / ratio <= STEP_TOLERANCE:
            out.append((event_date.isoformat(), ratio))
    return out


def adjust_bars(bars, events):
    """Daily bars restated in today's share terms. Does not mutate the input.

    A bar is divided by every split that took effect *after* it, so the series
    becomes continuous across the event. Expressing it as a product rather than a
    running rescale makes the result independent of event order. Only events the
    series does not already reflect are applied -- see applicable_events.
    """
    if not bars:
        return []
    pairs = _usable(applicable_events(bars, events))
    if not pairs:
        return list(bars)

    out = []
    for bar in bars:
        when = _as_date(bar.get("datetime") or bar.get("date"))
        factor = 1.0
        if when:
            for event_date, ratio in pairs:
                if when < event_date:
                    factor *= ratio
        if factor == 1.0:
            out.append(dict(bar))
            continue
        adjusted = dict(bar)
        for field in ("open", "high", "low", "close"):
            value = bar.get(field)
            if isinstance(value, (int, float)):
                adjusted[field] = value / factor
        volume = bar.get("volume")
        if isinstance(volume, (int, float)):
            adjusted["volume"] = volume * factor      # more shares, same value
        out.append(adjusted)
    return out


def load():
    """Cached events, keyed by Breeze code. Empty when absent or unreadable."""
    if not SPLITS_PATH.exists():
        return {}
    try:
        stored = json.loads(SPLITS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return stored if isinstance(stored, dict) else {}


def save(mapping):
    SPLITS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPLITS_PATH.write_text(json.dumps(mapping, indent=2, sort_keys=True))


def for_code(code, cache=None):
    """Events for one Breeze code. Empty list when unknown, never an error."""
    return (cache if cache is not None else load()).get(code) or []


def _exchange_codes():
    """Breeze ShortName -> NSE ExchangeCode, EQ series only.

    ExchangeCode is the NSE ticker, which is what Yahoo knows: Breeze calls
    Reliance RELIND, Yahoo calls it RELIANCE.NS.
    """
    if not MASTER_PATH.exists():
        return {}
    rows = list(csv.reader(io.StringIO(MASTER_PATH.read_text(errors="replace")),
                           skipinitialspace=True))
    if not rows:
        return {}
    header = [h.strip().strip('"') for h in rows[0]]
    try:
        i_code = header.index("ShortName")
        i_series = header.index("Series")
        i_exchange = header.index("ExchangeCode")
    except ValueError:
        return {}

    index = {}
    for row in rows[1:]:
        if len(row) <= max(i_code, i_series, i_exchange):
            continue
        if row[i_series].strip().strip('"').upper() != "EQ":
            continue
        short = row[i_code].strip().strip('"').upper()
        exchange = row[i_exchange].strip().strip('"').upper()
        if short and exchange:
            index[short] = exchange
    return index


def yahoo_symbol(code, index=None):
    """``RELIND`` -> ``RELIANCE.NS``. None when the master cannot place it."""
    index = _exchange_codes() if index is None else index
    exchange = index.get((code or "").strip().upper())
    return f"{exchange}.NS" if exchange else None


def fetch(codes, ticker_factory=None):
    """Look up splits for each code. Network. Skips whatever it cannot resolve.

    One failing symbol must not lose the rest of the run, so each lookup is
    isolated; a code that genuinely has no splits is recorded as an empty list so
    a later run does not keep refetching it.
    """
    if ticker_factory is None:                     # imported lazily: the request
        import yfinance                            # path must never need it
        ticker_factory = yfinance.Ticker

    index = _exchange_codes()
    found = {}
    for code in codes or []:
        symbol = yahoo_symbol(code, index=index)
        if not symbol:
            continue
        try:
            raw = ticker_factory(symbol).get_splits()
            events = []
            for when, ratio in dict(raw).items():
                stamp, factor = _as_date(when), _as_ratio(ratio)
                if stamp and factor:
                    events.append([stamp.isoformat(), factor])
            found[code] = sorted(events)
        except Exception:
            continue                               # leave any cached copy alone
    return found


def refresh(codes, ticker_factory=None):
    """Fetch and merge into the cache, keeping codes that were not refetched."""
    merged = load()
    merged.update(fetch(codes, ticker_factory=ticker_factory))
    save(merged)
    return merged
