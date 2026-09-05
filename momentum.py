"""Cross-sectional momentum.

The best-replicated equity anomaly (Jegadeesh & Titman 1993, reproduced on data
back to the 19th century) is not an absolute per-stock verdict but a *relative*
ranking: sort a universe by trailing return and hold the leaders.

Two construction details carry the evidence:

* **12-1 formation** -- measure the trailing 12 months but end the window one
  month before today. Short-horizon reversal runs opposite to medium-horizon
  momentum, so including the most recent month dilutes the signal.
* **Absolute-momentum filter** -- optionally require the score to be positive,
  so a universe in drawdown produces no holdings rather than "least bad".
"""

import math

LOOKBACK_DAYS = 252   # ~12 months of trading days
SKIP_DAYS = 21        # ~1 month, excluded
TOP_FRACTION = 0.2    # top quintile


def momentum_score(closes, lookback_days=LOOKBACK_DAYS, skip_days=SKIP_DAYS):
    """Percent return over the formation window ending ``skip_days`` ago."""
    if not closes or len(closes) < lookback_days + 1:
        return None
    base_idx = len(closes) - 1 - lookback_days
    end_idx = len(closes) - 1 - skip_days
    if end_idx <= base_idx:
        return None
    base = closes[base_idx]
    if not base:
        return None
    return (closes[end_idx] - base) / base * 100.0


def rank_and_select(scores, top_fraction=TOP_FRACTION, require_positive=True):
    """Codes to hold: the strongest ``top_fraction`` of the scored universe.

    Ties break on code so the selection is reproducible.
    """
    eligible = {c: s for c, s in scores.items() if s is not None}
    if require_positive:
        eligible = {c: s for c, s in eligible.items() if s > 0}
    if not eligible:
        return []
    ordered = sorted(eligible.items(), key=lambda kv: (-kv[1], kv[0]))
    count = max(1, math.ceil(len(eligible) * top_fraction))
    return [code for code, _ in ordered[:count]]
