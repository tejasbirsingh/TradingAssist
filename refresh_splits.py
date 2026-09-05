"""Populate data/splits.json with split and bonus ratios from Yahoo.

    .venv/bin/python refresh_splits.py

Run it once, and again after adding a stock to the watchlist. Splits are
historical facts that do not change, so this is not a recurring job -- it only
needs to see a name it has not seen before, or a newly announced split.

Deliberately a separate command rather than something the server does: the
dashboard's request path must never depend on Yahoo. Without this file momentum
is simply unadjusted, which is how it behaved before splits.py existed.
"""

import sys

import splits
import universe
from watchlist import load as load_watchlist


def codes():
    """Every code whose history the momentum ranking reads."""
    stored = {entry["code"] for entry in load_watchlist()}
    on_disk = {p.stem.replace("_1day", "")
               for p in (splits.DATA_DIR / "history").glob("*_1day.json")}
    return sorted(stored | on_disk | set(universe.CODES))


def main():
    wanted = codes()
    print(f"Looking up {len(wanted)} codes on Yahoo…")

    unresolved = [c for c in wanted if splits.yahoo_symbol(c) is None]
    if unresolved:
        print(f"  no NSE symbol in the security master for: {', '.join(unresolved)}")

    merged = splits.refresh(wanted)

    fetched = [c for c in wanted if c in merged]
    with_events = {c: merged[c] for c in fetched if merged[c]}
    print(f"  resolved {len(fetched)}/{len(wanted)}; "
          f"{len(with_events)} have splits on record")
    for code, events in sorted(with_events.items()):
        shown = ", ".join(f"{d} ÷{r:g}" for d, r in events[-3:])
        print(f"    {code:8s} {shown}")

    print(f"Written to {splits.SPLITS_PATH}")
    if not fetched:
        print("Nothing resolved — momentum stays unadjusted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
