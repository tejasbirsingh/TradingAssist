"""Pure indicator maths. No I/O, no Breeze types — just numbers in, numbers out.

Every function returns None rather than a fabricated value when there is not
enough data, so callers must decide explicitly what to do about it.
"""


def sma(values, window):
    """Simple moving average of the last ``window`` values."""
    if not values or window <= 0 or len(values) < window:
        return None
    tail = values[-window:]
    return sum(tail) / len(tail)


def rsi(closes, period=14):
    """Wilder's RSI. Needs ``period + 1`` closes to form ``period`` deltas."""
    if not closes or len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing over whatever deltas remain.
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def volume_ratio(volumes, window=20):
    """Latest volume divided by the mean of the ``window`` bars before it."""
    if not volumes or window <= 0 or len(volumes) < window + 1:
        return None
    prior = volumes[-(window + 1):-1]
    prior_mean = sum(prior) / len(prior)
    if prior_mean == 0:
        return None
    return volumes[-1] / prior_mean


def range_position(highs, lows, close):
    """Where ``close`` sits in the high-low range: 0.0 at the low, 1.0 at the high."""
    if not highs or not lows:
        return None
    high, low = max(highs), min(lows)
    width = high - low
    if width == 0:
        return None
    return (close - low) / width
