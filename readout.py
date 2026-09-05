"""Descriptive readout: what the price is doing, not what to do about it.

This replaces the BUY / SELL / HOLD verdict. Backtesting the verdict over 18
years and 8,460 non-overlapping observations found no out-of-sample edge -- and
in-sample its BUY bucket underperformed its own HOLD bucket -- so presenting it
as a recommendation was not defensible. Every state below is a statement of
fact about price relative to its own history.
"""

SMA_BAND_PCT = 2.0
MIN_BARS_FOR_TREND = 50   # the 50-day average needs 50 bars

STATES = ("UPTREND", "DOWNTREND", "RANGE-BOUND", "MIXED", "TREND UNKNOWN")

NOT_ADVICE = (
    "Descriptive only. Backtested over 18 years and 39 NSE names, buy/sell "
    "rules built from these indicators showed no out-of-sample edge, so this "
    "tool reports what price is doing and leaves the decision to you."
)


STATE_HELP = {
    "UPTREND": (
        "Price is more than 2% above BOTH its 20-day and 50-day average price. "
        "Recent prices are higher than the medium-term average, and both "
        "timeframes agree."
    ),
    "DOWNTREND": (
        "Price is more than 2% below BOTH its 20-day and 50-day average price. "
        "Recent prices are lower than the medium-term average, and both "
        "timeframes agree."
    ),
    "RANGE-BOUND": (
        "Price is within 2% of both its 20-day and 50-day averages — it is "
        "drifting sideways around its own average rather than trending. Neither "
        "direction is established."
    ),
    "MIXED": (
        "The two averages disagree: price is clearly above one and clearly "
        "below the other. Typically a trend that is turning, or a sharp recent "
        "move against a longer-running one."
    ),
    "TREND UNKNOWN": (
        "Not enough history to judge. The 50-day average needs 50 daily bars, "
        "so a recently listed stock has no trend to measure yet."
    ),
}

DATA_HELP = {
    "INSUFFICIENT": (
        "Fewer than 15 bars. RSI(14) alone needs 15, so most indicators cannot "
        "be calculated at all and no reading is shown."
    ),
    "LOW": (
        "15-49 bars of history. Short-window indicators work; the 50-day "
        "average does not. A stock listed under 20 sessions ago is held at LOW "
        "however many intraday bars exist, because hundreds of 5-minute bars "
        "from a few days is not established history."
    ),
    "MEDIUM": "50-199 bars. All indicators here are calculable.",
    "HIGH": "200 or more bars. Every indicator has its full lookback available.",
}

MOMENTUM_HELP = (
    "Trailing 12-month price return, measured up to one month ago rather than "
    "today. The skipped recent month is deliberate: over very short horizons "
    "prices tend to reverse, which pollutes the longer-run signal. Shown as a "
    "rank because momentum only means something relative to other stocks — "
    "'+15%' is strong or weak depending on what everything else did."
)

INDICATOR_HELP = {
    "RSI(14)": (
        "Relative Strength Index over 14 bars, scaled 0-100. It compares the "
        "size of recent gains to recent losses. Conventionally below 30 is "
        "called oversold and above 70 overbought, but in a genuine uptrend RSI "
        "can sit above 70 for months, so a high reading is not by itself a "
        "warning."
    ),
    "Price vs 20SMA": (
        "How far current price sits above or below the average closing price of "
        "the last 20 bars — roughly the last month on a daily chart. A "
        "short-term trend gauge."
    ),
    "Price vs 50SMA": (
        "The same comparison against the average of the last 50 bars — roughly "
        "the last quarter. Slower to react than the 20-day, so it describes the "
        "medium-term trend."
    ),
    "Volume vs 20d": (
        "Latest bar's traded volume divided by the average of the previous 20 "
        "bars. Above 1.0 means more shares changed hands than usual. Untraded "
        "artifact bars are excluded, and readings above 10x are flagged as a "
        "closing auction or block trade rather than ordinary activity."
    ),
    "Range position": (
        "Where the current price sits between the lowest low and highest high "
        "of the period — 0% is at the bottom of the range, 100% at the top. "
        "Over a full year this is the familiar 52-week range position."
    ),
}


def glossary():
    """All help text, served with the payload so tooltips match the code."""
    return {"state": STATE_HELP, "data": DATA_HELP,
            "indicator": INDICATOR_HELP, "momentum": MOMENTUM_HELP}


def trend_state(pct_vs_sma20, pct_vs_sma50, bars):
    """Where price sits relative to its 20- and 50-day averages."""
    if bars < MIN_BARS_FOR_TREND or pct_vs_sma20 is None or pct_vs_sma50 is None:
        return "TREND UNKNOWN"
    above = pct_vs_sma20 > SMA_BAND_PCT and pct_vs_sma50 > SMA_BAND_PCT
    below = pct_vs_sma20 < -SMA_BAND_PCT and pct_vs_sma50 < -SMA_BAND_PCT
    if above:
        return "UPTREND"
    if below:
        return "DOWNTREND"
    if abs(pct_vs_sma20) <= SMA_BAND_PCT and abs(pct_vs_sma50) <= SMA_BAND_PCT:
        return "RANGE-BOUND"
    return "MIXED"


def rank_universe(scores):
    """Rank codes by score, strongest first. Unscored codes are omitted.

    Momentum is reported as a rank because it is a cross-sectional effect: the
    number only means something relative to other names.
    """
    scored = {c: s for c, s in scores.items() if s is not None}
    if not scored:
        return {}
    ordered = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    total = len(ordered)
    out = {}
    for position, (code, score) in enumerate(ordered, start=1):
        pct = 100.0 if total == 1 else (1 - (position - 1) / total) * 100.0
        out[code] = {"rank": position, "of": total, "score": score,
                     "percentile": pct}
    return out
