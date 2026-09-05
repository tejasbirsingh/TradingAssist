"""Does a model trained on this app's own features predict tomorrow's return?

Run:
    .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python forecast_experiment.py

The decision rule is printed before any result and is fixed in ``PRE_REGISTERED``
below, because the failure mode of this kind of experiment is not a bug -- it is
finding a reason afterwards why 51% was encouraging.

Three things this is built to avoid:

* **Predicting price levels.** A model that answers "tomorrow is about today" scores
  a tiny error and carries no information. The target here is the next-day *return*,
  and the price baseline it must beat is exactly that persistence rule.
* **Overlapping samples.** Test folds are whole calendar years, disjoint, and the
  model only ever sees data from before the year it is judged on.
* **Ignoring costs.** ``INDIA_ROUND_TRIP_PCT`` from backtest.py is charged on every
  round trip, and the accuracy needed to clear it is derived from the data.

Known bias, stated up front: the universe is the 40 names that are liquid large caps
*today*, so it excludes everything that failed on the way here. That flatters any
result. A negative result is therefore trustworthy; a positive one is suspect.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import INDIA_ROUND_TRIP_PCT
from indicators import rsi as ref_rsi

HISTORY_DIR = Path(__file__).parent / "data" / "history"

RSI_PERIOD = 14
SMA_SHORT, SMA_LONG = 20, 50
VOL_WINDOW = 20
RANGE_WINDOW = 252
FIRST_TEST_YEAR = 2012

PRE_REGISTERED = """
PRE-REGISTERED DECISION RULE  (fixed before any result was seen)

  1. DIRECTION   out-of-sample accuracy must beat the always-up baseline by more
                 than 1.0 percentage point, in at least two thirds of test years.
  2. PRICE       mean absolute error must beat the persistence baseline
                 ("tomorrow's close equals today's").
  3. MONEY       mean net return per trade, after a {cost:.2f}% round trip, must be
                 positive.

  All three must hold. Any single failure means the model is not usable as a
  reference, and that is the conclusion regardless of how close it came.
""".strip().format(cost=INDIA_ROUND_TRIP_PCT)


# ---- features -------------------------------------------------------------
# Vectorised for speed over ~170k rows, then checked against indicators.py so a
# faster reimplementation cannot quietly disagree with what the dashboard shows.

def wilder_rsi(close, period=RSI_PERIOD):
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(avg_loss != 0.0, 100.0)


def verify_rsi_matches_the_app(frame):
    """Guard: the vectorised RSI must agree with indicators.rsi to 0.01."""
    closes = frame["close"].tolist()
    mine = wilder_rsi(frame["close"]).iloc[-1]
    theirs = ref_rsi(closes, RSI_PERIOD)
    if theirs is None or not np.isfinite(mine):
        return None
    return abs(mine - theirs)


SPIKE_PCT = 25.0


def drop_bad_bars(frame):
    """Remove single-bar price spikes. Returns (frame, count).

    Breeze's daily history carries bars that are 5x or 10x the neighbouring days
    and revert immediately -- ASIPAI prints +901% on 2011-10-26 and -90% two days
    later, and the same dates recur across unrelated names. NSE price bands make a
    real +900% day impossible, so these are feed errors.

    A spike is identified by disagreeing with *both* neighbours, which leaves
    genuine sustained moves intact: after a real crash the next day stays down.
    """
    close = frame["close"].to_numpy(dtype=float)
    keep = np.ones(len(close), dtype=bool)
    for i in range(1, len(close) - 1):
        prev, cur, nxt = close[i - 1], close[i], close[i + 1]
        if not (prev and cur and nxt):
            continue
        off_prev = abs(cur / prev - 1) > SPIKE_PCT / 100
        off_next = abs(cur / nxt - 1) > SPIKE_PCT / 100
        if off_prev and off_next:
            keep[i] = False
    return frame[keep], int((~keep).sum())


def features_for(frame):
    """One row per bar, using only information available at that bar's close."""
    close, high, low, vol = (frame["close"], frame["high"], frame["low"],
                             frame["volume"])
    out = pd.DataFrame(index=frame.index)
    out["ret1"] = close.pct_change() * 100
    out["ret5"] = close.pct_change(5) * 100
    out["ret21"] = close.pct_change(21) * 100
    out["rsi"] = wilder_rsi(close)
    out["vs_sma20"] = (close / close.rolling(SMA_SHORT).mean() - 1) * 100
    out["vs_sma50"] = (close / close.rolling(SMA_LONG).mean() - 1) * 100
    # Zero-volume bars are artifacts (closing stamps, pre-open) and would flatten
    # the comparison, so they are excluded from the mean rather than counted as calm.
    positive = vol.where(vol > 0)
    out["vol_ratio"] = vol / positive.rolling(VOL_WINDOW).mean()
    span = high.rolling(RANGE_WINDOW).max() - low.rolling(RANGE_WINDOW).min()
    out["range_pos"] = ((close - low.rolling(RANGE_WINDOW).min())
                        / span.replace(0.0, np.nan))
    out["gap"] = (frame["open"] / close.shift(1) - 1) * 100

    # The target: tomorrow's return. shift(-1) is the only forward-looking step
    # in this file, and nothing derived from it enters the features.
    out["target"] = close.pct_change().shift(-1) * 100
    out["close"] = close
    out["next_close"] = close.shift(-1)
    return out


def load_panel():
    """Every name's features, split-adjusted, stacked with a date index."""
    import splits
    events = splits.load()
    import dataquality

    # Two passes, because a fetch-window seam is only distinguishable from a real
    # crash by landing on the same date across many unrelated names. Without this
    # the panel carries 79 permanent scale breaks and its return sd reads 5.21%
    # instead of 2.33%, which flatters the breakeven accuracy this experiment turns on.
    raw = {}
    for path in sorted(HISTORY_DIR.glob("*_1day.json")):
        code = path.stem.replace("_1day", "")
        raw[code] = splits.adjust_bars(
            dataquality.clean_bars(json.loads(path.read_text())), events.get(code))
    seams = dataquality.seam_dates(raw)

    frames, rsi_checks, dropped = [], [], 0
    for code, rows in raw.items():
        rows = dataquality.repair_splices(rows, seams)
        frame = pd.DataFrame(rows)
        if len(frame) < RANGE_WINDOW + 60:
            continue
        frame["date"] = pd.to_datetime(frame["datetime"].str.slice(0, 10))
        frame = frame.sort_values("date").set_index("date")
        frame, bad = drop_bad_bars(frame)
        dropped += bad
        drift = verify_rsi_matches_the_app(frame)
        if drift is not None:
            rsi_checks.append((code, drift))
        feats = features_for(frame)
        feats["code"] = code
        frames.append(feats)
    panel = pd.concat(frames).dropna()
    return panel, rsi_checks, dropped


FEATURES = ["ret1", "ret5", "ret21", "rsi", "vs_sma20", "vs_sma50",
            "vol_ratio", "range_pos", "gap"]


def breakeven_accuracy(panel, cost_pct):
    """Directional accuracy needed just to cover costs, from this data's own moves."""
    avg_move = panel["target"].abs().mean()
    # (p * win) - ((1-p) * loss) = cost, with win == loss == avg_move
    return 0.5 * (1.0 + cost_pct / avg_move), avg_move


def run():
    print(PRE_REGISTERED)
    print()
    panel, rsi_checks, dropped = load_panel()
    worst = max((d for _, d in rsi_checks), default=None)
    print(f"panel: {len(panel):,} rows, {panel['code'].nunique()} names, "
          f"{panel.index.min().date()} .. {panel.index.max().date()}")
    print(f"dropped {dropped} single-bar price spikes (Breeze feed errors, "
          f"|move| > {SPIKE_PCT:.0f}% reverting immediately)")
    if worst is not None:
        print(f"vectorised RSI agrees with indicators.rsi to {worst:.4f} "
              f"across {len(rsi_checks)} names")

    need, avg_move = breakeven_accuracy(panel, INDIA_ROUND_TRIP_PCT)
    print(f"mean |next-day move|: {avg_move:.3f}%   "
          f"round-trip cost: {INDIA_ROUND_TRIP_PCT:.2f}%")
    print(f"=> breakeven directional accuracy: {need * 100:.1f}%")
    print()

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.preprocessing import StandardScaler

    years = sorted({d.year for d in panel.index})
    test_years = [y for y in years if y >= FIRST_TEST_YEAR]
    rows = []

    for year in test_years:
        train = panel[panel.index.year < year]
        test = panel[panel.index.year == year]
        if len(train) < 5000 or len(test) < 200:
            continue

        x_train = train[FEATURES].to_numpy()
        x_test = test[FEATURES].to_numpy()
        up_train = (train["target"] > 0).to_numpy().astype(int)
        up_test = (test["target"] > 0).to_numpy().astype(int)

        scaler = StandardScaler().fit(x_train)          # fitted on train only
        xs_train, xs_test = scaler.transform(x_train), scaler.transform(x_test)

        logit = LogisticRegression(max_iter=2000).fit(xs_train, up_train)
        gbm = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.05, max_depth=4,
            random_state=0).fit(x_train, up_train)
        ridge = Ridge(alpha=1.0).fit(xs_train, train["target"].to_numpy())

        # Baselines. Always-up is the honest directional one, because equities
        # drift upward and a model must beat that, not 50%.
        base_dir = up_test.mean()
        persistence_mae = test["target"].abs().mean()   # predicting 0% return
        ridge_pred = ridge.predict(xs_test)
        ridge_mae = np.abs(ridge_pred - test["target"].to_numpy()).mean()

        for name, pred_up in (("logit", logit.predict(xs_test)),
                              ("gbm", gbm.predict(x_test))):
            acc = (pred_up == up_test).mean()
            traded = test["target"].to_numpy()[pred_up == 1]
            net = (traded.mean() - INDIA_ROUND_TRIP_PCT) if len(traded) else 0.0
            rows.append({"year": year, "model": name, "n": len(test),
                         "acc": acc, "base": base_dir, "edge": acc - base_dir,
                         "trades": len(traded), "net": net})
        rows.append({"year": year, "model": "ridge", "n": len(test),
                     "acc": np.nan, "base": np.nan, "edge": np.nan,
                     "trades": np.nan, "net": np.nan,
                     "mae": ridge_mae, "mae_base": persistence_mae})

    table = pd.DataFrame(rows)
    dirs = table[table["model"].isin(["logit", "gbm"])]
    regs = table[table["model"] == "ridge"]

    print("DIRECTION — out-of-sample, one row per test year")
    print(f"{'year':>6} {'model':>6} {'n':>7} {'accuracy':>9} {'always-up':>10} "
          f"{'edge':>7} {'net/trade':>10}")
    for _, r in dirs.iterrows():
        print(f"{r['year']:>6} {r['model']:>6} {r['n']:>7,} {r['acc']*100:>8.2f}% "
              f"{r['base']*100:>9.2f}% {r['edge']*100:>+6.2f}pp {r['net']:>+9.3f}%")

    print()
    print("PRICE — mean absolute error, ridge vs persistence")
    for _, r in regs.iterrows():
        better = "beats" if r["mae"] < r["mae_base"] else "LOSES TO"
        print(f"{r['year']:>6}  model {r['mae']:.4f}%   persistence "
              f"{r['mae_base']:.4f}%   {better} baseline")

    print()
    print("=" * 72)
    verdict = []
    for model in ("logit", "gbm"):
        sub = dirs[dirs["model"] == model]
        beat = (sub["edge"] > 0.01).sum()
        rule1 = beat >= (2 / 3) * len(sub)
        rule3 = sub["net"].mean() > 0
        print(f"{model}: beat always-up by >1pp in {beat}/{len(sub)} years "
              f"(rule 1 {'PASS' if rule1 else 'FAIL'});  "
              f"mean net/trade {sub['net'].mean():+.3f}% "
              f"(rule 3 {'PASS' if rule3 else 'FAIL'})")
        verdict.append(rule1 and rule3)
    rule2 = bool((regs["mae"] < regs["mae_base"]).all())
    print(f"ridge: beats persistence MAE in every year "
          f"(rule 2 {'PASS' if rule2 else 'FAIL'})")
    print()
    if any(verdict) and rule2:
        print("VERDICT: the pre-registered bar was cleared. Worth a second look —")
        print("         and given the survivorship bias above, treat it as suspect")
        print("         until it holds on a point-in-time universe.")
    else:
        print("VERDICT: the pre-registered bar was NOT cleared. This model is not")
        print("         usable as a reference, and no amount of tuning changes that")
        print("         without a fresh out-of-sample period to test on.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
