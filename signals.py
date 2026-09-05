"""Turn indicator values into a transparent verdict.

Each indicator casts one vote of -1, 0 or +1 and the votes are summed. Nothing
is hidden: callers get every vote, its value and why it voted that way, so a
verdict can be inspected and overruled rather than trusted blindly.

This is a mechanical summary of price history, not investment advice.
"""

from indicators import range_position, rsi, sma, volume_ratio

RSI_PERIOD = 14
SMA_SHORT = 20
SMA_LONG = 50
VOL_WINDOW = 20
RANGE_WINDOW = 252  # roughly one trading year

OVERSOLD, OVERBOUGHT = 30.0, 70.0
SMA_BAND_PCT = 2.0
VOL_HEAVY, VOL_THIN = 1.2, 0.8
# Above this the bar is not comparable to a normal one: NSE's closing auction
# routinely prints 50x a regular 5-minute bar.
VOL_ABSURD = 10.0
RANGE_HIGH, RANGE_LOW = 0.85, 0.15

BUY_AT, SELL_AT = 2, -2

MIN_BARS = RSI_PERIOD + 1
MEDIUM_BARS, HIGH_BARS = 50, 200
RECENT_LISTING_SESSIONS = 20

DISCLAIMER = ("Mechanical indicator summary, not investment advice. "
              "Indicators describe past price behaviour and are frequently wrong.")


def confidence_for_bars(bars):
    """Confidence follows how much history actually exists."""
    if bars < MIN_BARS:
        return "INSUFFICIENT"
    if bars < MEDIUM_BARS:
        return "LOW"
    if bars < HIGH_BARS:
        return "MEDIUM"
    return "HIGH"


def verdict_from_score(score, confidence):
    """Map a summed score to a verdict, refusing to guess without data."""
    if confidence == "INSUFFICIENT":
        return "INSUFFICIENT_DATA"
    if score >= BUY_AT:
        return "BUY"
    if score <= SELL_AT:
        return "SELL"
    return "HOLD"


def _vote(name, value, display, vote, note):
    return {"name": name, "value": value, "display": display,
            "vote": vote, "note": note}


def _rsi_vote(value):
    if value is None:
        return _vote("RSI(14)", None, "—", 0, f"needs {MIN_BARS} bars")
    display = f"{value:.1f}"
    if value < OVERSOLD:
        return _vote("RSI(14)", value, display, 1, "oversold")
    if value > OVERBOUGHT:
        return _vote("RSI(14)", value, display, -1, "overbought")
    return _vote("RSI(14)", value, display, 0, "neutral")


def _sma_vote(label, pct):
    name = f"Price vs {label}"
    if pct is None:
        return _vote(name, None, "—", 0, "not enough bars")
    display = f"{pct:+.1f}%"
    if pct > SMA_BAND_PCT:
        return _vote(name, pct, display, 1, f"trading above its {label}")
    if pct < -SMA_BAND_PCT:
        return _vote(name, pct, display, -1, f"trading below its {label}")
    return _vote(name, pct, display, 0, f"hugging its {label}")


def _volume_vote(ratio, price_rising):
    name = "Volume vs 20d"
    if ratio is None:
        return _vote(name, None, "—", 0, "not enough bars")
    display = f"{ratio:.2f}x"
    if ratio >= VOL_ABSURD:
        return _vote(name, ratio, display, 0,
                     "outsized volume — likely a closing auction or block trade, "
                     "not intraday conviction")
    if ratio >= VOL_HEAVY and price_rising is not None:
        if price_rising:
            return _vote(name, ratio, display, 1, "heavy volume behind the rise")
        return _vote(name, ratio, display, -1, "heavy volume behind the fall")
    if ratio <= VOL_THIN:
        return _vote(name, ratio, display, 0,
                     "below-average volume, so the move lacks conviction")
    return _vote(name, ratio, display, 0, "typical volume")


def _range_vote(pos):
    name = "Range position"
    if pos is None:
        return _vote(name, None, "—", 0, "no range width")
    display = f"{pos * 100:.0f}%"
    if pos > RANGE_HIGH:
        return _vote(name, pos, display, -1, "near the top of its range")
    if pos < RANGE_LOW:
        return _vote(name, pos, display, 1, "near the bottom of its range")
    return _vote(name, pos, display, 0, "mid-range")


def score_indicators(rsi_value=None, pct_vs_sma20=None, pct_vs_sma50=None,
                     vol_ratio=None, range_pos=None, price_rising=None):
    """Return one vote per indicator, always all five, in a stable order."""
    return [
        _rsi_vote(rsi_value),
        _sma_vote(f"{SMA_SHORT}SMA", pct_vs_sma20),
        _sma_vote(f"{SMA_LONG}SMA", pct_vs_sma50),
        _volume_vote(vol_ratio, price_rising),
        _range_vote(range_pos),
    ]


def _pct_from(price, average):
    if average is None or average == 0:
        return None
    return (price - average) / average * 100.0


def _reasoning(signal, votes, score, bars):
    if signal == "INSUFFICIENT_DATA":
        return (f"Only {bars} bar(s) available and RSI(14) alone needs {MIN_BARS}. "
                "No verdict issued rather than a guess.")
    bullish = [v for v in votes if v["vote"] > 0]
    bearish = [v for v in votes if v["vote"] < 0]
    parts = [f"{len(bullish)} bullish, {len(bearish)} bearish (net {score:+d})."]
    movers = [f"{v['name']} {v['note']}" for v in votes if v["vote"] != 0]
    if movers:
        parts.append("Driven by: " + "; ".join(movers) + ".")
    else:
        parts.append("No indicator is at an extreme.")
    thin = [v for v in votes if v["vote"] == 0 and "conviction" in v["note"]]
    if thin:
        parts.append("Note: " + thin[0]["note"] + ".")
    return " ".join(parts)


def evaluate(candles, daily_sessions=None):
    """Score a candle series. ``daily_sessions`` drives the recent-listing caveat."""
    bars = len(candles)
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]

    last = closes[-1] if bars else None
    rsi_value = rsi(closes, RSI_PERIOD)
    pct20 = _pct_from(last, sma(closes, SMA_SHORT)) if bars else None
    pct50 = _pct_from(last, sma(closes, SMA_LONG)) if bars else None
    # Intraday series contain artifact bars with no trading (pre-open stamps and
    # the 15:30 closing stamp). They carry a valid close, so they stay in the
    # price series, but including them would flatten the volume comparison.
    vol = volume_ratio([v for v in volumes if v > 0], VOL_WINDOW)
    pos = (range_position(highs[-RANGE_WINDOW:], lows[-RANGE_WINDOW:], last)
           if bars else None)
    rising = closes[-1] > closes[-2] if bars >= 2 else None

    votes = score_indicators(rsi_value=rsi_value, pct_vs_sma20=pct20,
                             pct_vs_sma50=pct50, vol_ratio=vol,
                             range_pos=pos, price_rising=rising)
    score = sum(v["vote"] for v in votes)
    bar_confidence = confidence_for_bars(bars)
    recent_listing = (daily_sessions is not None
                      and daily_sessions < RECENT_LISTING_SESSIONS)
    # Hundreds of intraday bars drawn from a handful of trading days must not
    # read as well-established history, so a recent listing caps confidence.
    confidence = ("LOW" if recent_listing and bar_confidence in {"HIGH", "MEDIUM"}
                  else bar_confidence)
    signal = verdict_from_score(score, confidence)

    caveats = []
    if recent_listing:
        caveats.append(
            f"Listed only {daily_sessions} session(s) ago. A stock in post-IPO "
            "price discovery has no established behaviour for these indicators "
            "to measure, so treat any reading as weak evidence.")
    if bar_confidence in {"LOW", "INSUFFICIENT"}:
        caveats.append(f"Only {bars} bar(s) of history; longer-window "
                       "indicators are unavailable or unreliable.")

    return {
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "bars": bars,
        "indicators": votes,
        "reasoning": _reasoning(signal, votes, score, bars),
        "caveats": caveats,
        "disclaimer": DISCLAIMER,
    }
