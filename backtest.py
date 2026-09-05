"""Walk-forward backtest harness for the signal logic.

Purpose: make changes to signals.py measurable instead of plausible. Three
things it takes seriously, because each one independently manufactures a fake
edge:

* **No look-ahead** — the evaluator only ever sees bars up to the decision bar.
* **Non-overlapping windows** — overlapping horizons reuse the same period many
  times over and inflate significance. ``step`` should be >= ``horizon``.
* **Costs and a benchmark** — an edge smaller than round-trip cost is not an
  edge, and one smaller than buy-and-hold is not worth trading.

Run:
    .venv/bin/python backtest.py
"""

import json
import statistics as st
from pathlib import Path

import dataquality
import splits

BASE_DIR = Path(__file__).parent

# Indian delivery equity round trip, approximate:
#   STT 0.10% buy + 0.10% sell, stamp duty 0.015% buy, exchange txn ~0.003%,
#   SEBI fees, brokerage (discount broker) and 18% GST on brokerage+charges.
INDIA_ROUND_TRIP_PCT = 0.30

HORIZON = 20        # trading days held (~1 calendar month)
MIN_BARS = 60       # need history before the first decision
IN_SAMPLE_FRAC = 0.70

VOTE_NAMES = ["RSI", "20SMA", "50SMA", "Volume", "Range"]


def forward_return(closes, i, horizon):
    """Percent change from bar ``i`` to bar ``i + horizon``, or None."""
    if i + horizon >= len(closes):
        return None
    base = closes[i]
    if not base:
        return None
    return (closes[i + horizon] - base) / base * 100.0


def strategy_return(verdict, fwd_pct, cost_pct, long_short=False):
    """Tradable P&L of acting on a verdict.

    Long-only by default: retail delivery cannot short, so SELL means "avoid"
    and earns nothing. Flat positions pay no cost.
    """
    if verdict == "BUY":
        return fwd_pct - cost_pct
    if verdict == "SELL" and long_short:
        return -fwd_pct - cost_pct
    return 0.0


def summarise(values):
    """n, mean, std, t-statistic and a 95% interval. None where undefined."""
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "std": None, "t_stat": None,
                "ci_low": None, "ci_high": None}
    mean = st.mean(values)
    if n < 2:
        return {"n": n, "mean": mean, "std": None, "t_stat": None,
                "ci_low": None, "ci_high": None}
    std = st.stdev(values)
    if std == 0:
        return {"n": n, "mean": mean, "std": 0.0, "t_stat": None,
                "ci_low": mean, "ci_high": mean}
    se = std / (n ** 0.5)
    return {"n": n, "mean": mean, "std": std, "t_stat": mean / se,
            "ci_low": mean - 1.96 * se, "ci_high": mean + 1.96 * se}


def collect(rows, horizon, step, evaluator, min_bars=MIN_BARS, code=None):
    """Walk forward, recording one observation per sampled bar.

    The forward return is resolved *before* the evaluator is called, so bars
    without a future are skipped without consuming a decision.
    """
    closes = [r["close"] for r in rows]
    out = []
    for i in range(min_bars, len(rows), step):
        fwd = forward_return(closes, i, horizon)
        if fwd is None:
            continue
        res = evaluator(rows[:i + 1])          # only the past, inclusive
        out.append({
            "code": code,
            "index": i,
            "date": rows[i].get("datetime"),
            "verdict": res["signal"],
            "score": res["score"],
            "votes": [v["vote"] for v in res.get("indicators", [])],
            "fwd": fwd,
        })
    return out


def split_by_time(observations, frac=IN_SAMPLE_FRAC):
    """Chronological split. Random splits leak the future into training."""
    cut = int(len(observations) * frac)
    return observations[:cut], observations[cut:]


def verdict_from_votes(votes, keep=None, flip=(), buy_at=2, sell_at=-2):
    """Re-derive a verdict from stored votes, for ablation without refetching."""
    keep = range(len(votes)) if keep is None else keep
    score = sum((-votes[k] if k in flip else votes[k]) for k in keep)
    if score >= buy_at:
        return "BUY", score
    if score <= sell_at:
        return "SELL", score
    return "HOLD", score


def selection_skill(observations, keep=None, flip=()):
    """Discrimination between BUY and SELL, independent of how often it trades.

    Total P&L is the wrong yardstick for comparing variants: a variant that
    signals BUY more often simply carries more market exposure and scores higher
    without picking better. This measures picking instead -- mean forward return
    of BUY names minus that of SELL names, plus each side's edge over the
    benchmark.
    """
    buys, sells, alls = [], [], []
    for o in observations:
        verdict, _ = verdict_from_votes(o["votes"], keep, flip)
        alls.append(o["fwd"])
        if verdict == "BUY":
            buys.append(o["fwd"])
        elif verdict == "SELL":
            sells.append(o["fwd"])
    bench = st.mean(alls) if alls else None
    return {
        "n_buy": len(buys), "n_sell": len(sells),
        "buy_mean": st.mean(buys) if buys else None,
        "sell_mean": st.mean(sells) if sells else None,
        "buy_edge": (st.mean(buys) - bench) if buys and bench is not None else None,
        "spread": (st.mean(buys) - st.mean(sells)) if buys and sells else None,
        "benchmark": bench,
    }


def report(label, observations, cost_pct=INDIA_ROUND_TRIP_PCT, long_short=False):
    """Print per-verdict forward returns and the tradable P&L vs buy-and-hold."""
    if not observations:
        print(f"  {label}: no observations")
        return None
    base = summarise([o["fwd"] for o in observations])
    print(f"\n  {label}  (n={base['n']})")
    print(f"    buy-and-hold benchmark      mean {base['mean']:+6.2f}%")
    for verdict in ("BUY", "HOLD", "SELL"):
        vals = [o["fwd"] for o in observations if o["verdict"] == verdict]
        s = summarise(vals)
        if s["n"] == 0:
            print(f"    after {verdict:<5}                 never fired")
            continue
        t = f"t={s['t_stat']:+5.2f}" if s["t_stat"] is not None else "t=  n/a"
        ci = (f"[{s['ci_low']:+.2f}, {s['ci_high']:+.2f}]"
              if s["ci_low"] is not None else "[n/a]")
        print(f"    after {verdict:<5} n={s['n']:<5} mean {s['mean']:+6.2f}%  "
              f"{t}  95%CI {ci}")
    pnl = summarise([strategy_return(o["verdict"], o["fwd"], cost_pct, long_short)
                     for o in observations])
    mode = "long/short" if long_short else "long-only"
    t = f"t={pnl['t_stat']:+5.2f}" if pnl["t_stat"] is not None else "t=  n/a"
    print(f"    strategy P&L ({mode}, {cost_pct:.2f}% cost) "
          f"mean {pnl['mean']:+6.2f}% per {HORIZON}d  {t}")
    print(f"    -> beats buy-and-hold? "
          f"{'YES' if pnl['mean'] is not None and base['mean'] is not None and pnl['mean'] > base['mean'] else 'NO'}")
    return {"benchmark": base, "pnl": pnl}


def run_cross_sectional(candles_by_code, horizon, lookback, skip, top_fraction,
                        cost_pct=INDIA_ROUND_TRIP_PCT, require_positive=True):
    """Rank the universe by momentum each rebalance and hold the leaders.

    Stocks have different histories and listing dates, so bars are located by
    date via bisect rather than by position -- aligning on index would compare
    different calendar dates across names.
    """
    import bisect

    from momentum import momentum_score, rank_and_select

    if not candles_by_code:
        return []

    series = {}
    for code, rows in candles_by_code.items():
        dates = [r.get("datetime") or "" for r in rows]
        closes = [r["close"] for r in rows]
        series[code] = (dates, closes)

    calendar = sorted({d for dates, _ in series.values() for d in dates})
    observations = []

    for c in range(0, len(calendar), horizon):
        as_of = calendar[c]
        scores, forwards = {}, {}
        for code, (dates, closes) in series.items():
            # Last bar at or before as_of: only the past is visible.
            i = bisect.bisect_right(dates, as_of) - 1
            if i < lookback:
                continue
            scores[code] = momentum_score(closes[:i + 1], lookback, skip)
            forwards[code] = forward_return(closes, i, horizon)

        tradable = {k: v for k, v in scores.items() if forwards.get(k) is not None}
        if not tradable:
            continue

        selected = rank_and_select(tradable, top_fraction, require_positive)
        held = [forwards[k] for k in selected if forwards.get(k) is not None]
        portfolio = (st.mean(held) - cost_pct) if held else 0.0
        bench_vals = [v for k, v in forwards.items()
                      if v is not None and k in tradable]
        observations.append({
            "date": as_of,
            "selected": selected,
            "n_universe": len(tradable),
            "portfolio": portfolio,
            "benchmark": st.mean(bench_vals) if bench_vals else 0.0,
        })
    return observations


def load_universe_candles(codes=None, verbose=True):
    """Daily candles per code, using market_data's on-disk cache."""
    import market_data
    if codes is None:
        path = BASE_DIR / "data" / "universe.json"
        codes = [u["code"] for u in json.loads(path.read_text())]
    out = {}
    for n, code in enumerate(codes, 1):
        try:
            # Prefer the multi-year files on disk: ~4,400 bars per name against
            # roughly 1,000 from a fresh fetch, and no API calls, so the harness
            # runs offline. _long_history already applies the dashboard's pipeline
            # -- special-session artifacts dropped, then restated for splits --
            # without which the benchmark scores a 1:1 bonus as a -50% day and a
            # Muhurat bar as a +100% one. Marking the code as topped up keeps this
            # research script from making 40 network calls it does not need.
            market_data._topped_up.add(code)
            rows = market_data._long_history(code)
            if not rows:
                rows = splits.adjust_bars(
                    dataquality.clean_bars(market_data.fetch_candles(code, "1day")),
                    splits.for_code(code))
            if len(rows) >= MIN_BARS + HORIZON + 1:
                out[code] = rows
                if verbose:
                    print(f"  [{n:>2}/{len(codes)}] {code:<8} {len(rows):>4} bars")
            elif verbose:
                print(f"  [{n:>2}/{len(codes)}] {code:<8} SKIP (only {len(rows)} bars)")
        except Exception as exc:
            if verbose:
                print(f"  [{n:>2}/{len(codes)}] {code:<8} ERROR {type(exc).__name__}: {exc}")

    # Repair the fetch-window seams. This needs the whole universe at once, which
    # is why it cannot live in _long_history: a scale break is only distinguishable
    # from a real crash by landing on the same date across many unrelated names.
    seams = dataquality.seam_dates(out)
    if seams:
        if verbose:
            print(f"\n  repairing {len(seams)} fetch-window seam(s): "
                  f"{', '.join(seams)}")
        out = {code: dataquality.repair_splices(rows, seams)
               for code, rows in out.items()}
    return out


def main():
    from signals import evaluate

    print("=== fetching universe (cached; ~1 API call per new symbol) ===")
    universe = load_universe_candles()
    print(f"\n  {len(universe)} symbols usable")

    print(f"\n=== walking forward (horizon={HORIZON}d, step={HORIZON} "
          f"= non-overlapping) ===")
    obs = []
    for code, rows in universe.items():
        obs.extend(collect(rows, HORIZON, HORIZON, evaluate, MIN_BARS, code))
    obs.sort(key=lambda o: (o["date"] or "", o["code"] or ""))
    print(f"  {len(obs)} independent observations across {len(universe)} symbols")

    in_s, out_s = split_by_time(obs)
    print(f"  in-sample {len(in_s)}  |  out-of-sample {len(out_s)}")

    print("\n=== CURRENT MODEL ===")
    report("in-sample", in_s)
    report("OUT-OF-SAMPLE", out_s)

    print("\n=== ABLATION (in-sample only; out-of-sample stays untouched) ===")
    variants = [("full model", None, ())]
    for k, name in enumerate(VOTE_NAMES):
        variants.append((f"without {name}", [j for j in range(5) if j != k], ()))
    variants += [("Range reversed", None, (4,)),
                 ("RSI reversed", None, (0,)),
                 ("trend only", [1, 2], ()),
                 ("mean-reversion only", [0, 4], ())]

    ranked = []
    for label, keep, flip in variants:
        pnl, buys, sells = [], 0, 0
        for o in in_s:
            v, _ = verdict_from_votes(o["votes"], keep, flip)
            buys += v == "BUY"
            sells += v == "SELL"
            pnl.append(strategy_return(v, o["fwd"], INDIA_ROUND_TRIP_PCT))
        s = summarise(pnl)
        ranked.append((s["mean"], label, s, buys, sells))
        t = f"t={s['t_stat']:+5.2f}" if s["t_stat"] is not None else "t=  n/a"
        print(f"  {label:<22} P&L {s['mean']:+6.2f}%  {t}  "
              f"(buys={buys} sells={sells})")

    best = max(ranked, key=lambda r: (r[0] is not None, r[0]))
    print(f"\n  best in-sample variant: {best[1]}")

    print("\n=== HONEST TEST: best in-sample variant, run OUT-OF-SAMPLE ===")
    keep, flip = next((k, f) for lbl, k, f in variants if lbl == best[1])
    pnl = [strategy_return(verdict_from_votes(o["votes"], keep, flip)[0],
                           o["fwd"], INDIA_ROUND_TRIP_PCT) for o in out_s]
    s = summarise(pnl)
    bench = summarise([o["fwd"] for o in out_s])
    t = f"t={s['t_stat']:+5.2f}" if s["t_stat"] is not None else "t=  n/a"
    print(f"  {best[1]} out-of-sample: mean {s['mean']:+6.2f}%  {t}  n={s['n']}")
    print(f"  buy-and-hold out-of-sample: mean {bench['mean']:+6.2f}%")
    print(f"  -> {'BEATS' if s['mean'] > bench['mean'] else 'LOSES TO'} buy-and-hold")


if __name__ == "__main__":
    main()
