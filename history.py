"""Long daily histories from Breeze, fetched in windows and stitched.

``get_historical_data_v2`` returns at most 1000 bars per call regardless of the
requested range: asking from 2000, 2010 or 2018 all yield the same most-recent
1000 bars. Moving ``to_date`` backwards does reach older data, so a long history
is assembled from overlapping windows and de-duplicated by date.

Windows are deliberately shorter than the cap (3 years ~= 750 trading days) so
no window is silently truncated, which would leave a gap.
"""

import json
import time
from datetime import date, timedelta
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "data" / "history"
WINDOW_YEARS = 3
YEARS_BACK = 15
REQUEST_PAUSE = 0.35          # stay well inside 100 calls/minute


def merge_candles(chunks):
    """Merge candle chunks into one ascending, de-duplicated series."""
    by_date = {}
    for chunk in chunks or []:
        for row in chunk or []:
            key = row.get("datetime")
            if not key:
                continue
            by_date.setdefault(key, row)
    return [by_date[k] for k in sorted(by_date)]


def window_bounds(end_date, years_back=YEARS_BACK, window_years=WINDOW_YEARS):
    """Contiguous (start, end) date windows walking backwards from ``end_date``."""
    if window_years <= 0 or years_back <= 0:
        raise ValueError("years_back and window_years must be positive")
    end = date.fromisoformat(end_date)
    floor = end - timedelta(days=int(365.25 * years_back))
    windows, cursor = [], end
    while cursor > floor:
        start = cursor - timedelta(days=int(365.25 * window_years))
        windows.append((start.isoformat(), cursor.isoformat()))
        cursor = start
    return windows


def _cache_path(code, interval):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{code}_{interval}.json"


def fetch_long_history(client, code, interval="1day", end_date=None,
                       years_back=YEARS_BACK, use_cache=True, verbose=False):
    """Full daily history for one code, cached to disk after the first fetch."""
    path = _cache_path(code, interval)
    if use_cache and path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    end_date = end_date or date.today().isoformat()
    chunks = []
    for start, end in window_bounds(end_date, years_back):
        resp = client.get_historical_data_v2(
            interval=interval,
            from_date=f"{start}T07:00:00.000Z",
            to_date=f"{end}T07:00:00.000Z",
            stock_code=code, exchange_code="NSE", product_type="cash")
        rows = resp.get("Success") or []
        if verbose:
            print(f"    {code} {start}->{end}: {len(rows)} bars")
        chunks.append(rows)
        if not rows:
            break                      # history exhausted; stop paging back
        time.sleep(REQUEST_PAUSE)

    merged = merge_candles(chunks)
    normalised = []
    for r in merged:
        try:
            normalised.append({
                "datetime": r["datetime"],
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": max(float(r.get("volume") or 0), 0.0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if normalised:
        path.write_text(json.dumps(normalised))
    return normalised
