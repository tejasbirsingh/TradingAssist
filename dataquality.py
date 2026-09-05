"""Remove single-bar artifacts from Breeze daily history.

Breeze back-adjusts its historical series for corporate actions but skips NSE
**special sessions** -- the Diwali Muhurat hour, Saturday Budget sittings, disaster
recovery sessions -- so those bars keep their unadjusted price inside an otherwise
adjusted series:

    2013-11-01  close 454.00  volume 5,428,564
    2013-11-03  close 908.70  volume   425,619   <- Muhurat, a Sunday, exactly 2.00x
    2013-11-05  close 454.70  volume 4,997,836

195 such bars sit across 34 of the 40 cached names. Across the universe their price
factor clusters on split ratios (1.5, 2.0, 10.0) and their median volume is **7.6%**
of the neighbouring sessions.

Detection is the round trip, not the size of the move: the bar has to disagree with
*both* neighbours. NSE circuit filters cap ordinary daily moves at 5/10/20%, so a
>25% move that fully reverts the next session is not a market event. A genuine crash
fails this test because the next day stays down, and so does a split, because the new
level persists.

These bars are dropped rather than repaired. Dividing by an inferred factor would
fabricate a price, and a one-hour ceremonial session is not a daily bar that
indicators should be reading in the first place.
"""

ROUND_TRIP_PCT = 25.0

# A special session trades for about an hour, so its volume is a fraction of a
# normal day's -- median 7.6% across the universe, 90th percentile 63%. Requiring
# thin volume as well as the round trip does two things: it keeps a genuine
# limit-up/limit-down day, which comes on *heavy* volume, and it stops a normal bar
# that happens to sit between two artifacts from being flagged itself, since its
# volume is high relative to those neighbours rather than low.
VOLUME_RATIO_MAX = 0.75


def _close(bar):
    value = (bar or {}).get("close")
    return float(value) if isinstance(value, (int, float)) and value else None


def _volume(bar):
    value = (bar or {}).get("volume")
    return float(value) if isinstance(value, (int, float)) else None


def find_artifacts(bars):
    """Bars that look like special sessions, with the evidence for each.

    Returns dicts of date, index, price factor versus the neighbouring mean, and
    volume ratio -- so a surprising drop can be inspected rather than trusted.
    """
    out = []
    if not bars or len(bars) < 3:
        return out
    limit = ROUND_TRIP_PCT / 100.0

    for i in range(1, len(bars) - 1):
        prev, cur, nxt = bars[i - 1], bars[i], bars[i + 1]
        cp, cc, cn = _close(prev), _close(cur), _close(nxt)
        if cp is None or cc is None or cn is None:
            continue
        if abs(cc / cp - 1) <= limit or abs(cc / cn - 1) <= limit:
            continue

        neighbour_close = (cp + cn) / 2.0
        vp, vn = _volume(prev), _volume(nxt)
        neighbour_volume = ((vp or 0.0) + (vn or 0.0)) / 2.0
        cur_volume = _volume(cur)
        ratio = (cur_volume / neighbour_volume
                 if cur_volume is not None and neighbour_volume else None)
        # No volume to judge by means the round trip has to stand alone.
        if ratio is not None and ratio > VOLUME_RATIO_MAX:
            continue
        out.append({
            "index": i,
            "date": str(cur.get("datetime") or cur.get("date"))[:10],
            "factor": cc / neighbour_close if neighbour_close else None,
            "volume_ratio": ratio,
        })
    return out


def clean_bars(bars):
    """The series with special-session artifacts dropped. Input is not mutated."""
    if not bars:
        return []
    bad = {a["index"] for a in find_artifacts(bars)}
    if not bad:
        return list(bars)
    return [bar for i, bar in enumerate(bars) if i not in bad]


SEAM_STEP_PCT = 30.0        # a persistent step this large is not a market move
SEAM_PERSIST_PCT = 15.0     # ...and the new level has to stick
SEAM_MIN_NAMES = 5          # ...on the same date, across this many unrelated names


def _persistent_steps(bars):
    """(date, factor) for permanent level changes, ignoring reverting spikes."""
    out = []
    if not bars or len(bars) < 3:
        return out
    dated = [(str(b.get("datetime") or b.get("date"))[:10], _close(b)) for b in bars]
    for i in range(1, len(dated) - 1):
        (_, prev), (day, cur), (_, nxt) = dated[i - 1], dated[i], dated[i + 1]
        if not prev or not cur or not nxt:
            continue
        if abs(cur / prev - 1) * 100 <= SEAM_STEP_PCT:
            continue
        if abs(nxt / cur - 1) * 100 >= SEAM_PERSIST_PCT:
            continue                      # reverts, so it is a spike not a seam
        out.append((day, cur / prev))
    return out


def seam_dates(series_by_code, min_names=SEAM_MIN_NAMES):
    """Dates where many unrelated names all change scale at once.

    ``history.py`` pages backwards in 3-year windows and Breeze adjusts its history
    retroactively, so each window arrived with the adjustment state Breeze had at
    fetch time. Joining them left permanent scale breaks at the window seams -- 79
    of them across 25 of 40 names, at factors that are cumulative split ratios.

    The cross-sectional test is what makes this safe. A seam is an artifact of *when
    the data was fetched*, so it lands on one date across many unrelated companies.
    A real collapse is idiosyncratic: one name, one date. Requiring agreement across
    names means a genuine crash is never flattened.
    """
    counts = {}
    for bars in (series_by_code or {}).values():
        for day, _ in _persistent_steps(bars):
            counts[day] = counts.get(day, 0) + 1
    return sorted(d for d, n in counts.items() if n >= max(1, min_names))


def repair_splices(bars, dates):
    """Rescale each pre-seam segment so the series is continuous. Input untouched.

    Applied newest-seam-first so earlier segments compound correctly, exactly as
    cumulative split factors do.
    """
    if not bars:
        return [] if bars is None else bars
    wanted = set(dates or [])
    if not wanted:
        return bars

    out = [dict(b) for b in bars]
    for day, factor in reversed(_persistent_steps(out)):
        if day not in wanted or not factor:
            continue
        index = next(i for i, b in enumerate(out)
                     if str(b.get("datetime") or b.get("date"))[:10] == day)
        for bar in out[:index]:
            for field in ("open", "high", "low", "close"):
                value = bar.get(field)
                if isinstance(value, (int, float)):
                    bar[field] = value * factor
            volume = bar.get("volume")
            if isinstance(volume, (int, float)):
                bar["volume"] = volume / factor
    return out
