"""Backtest universe: explicit Breeze codes, validated against the security master.

Codes are listed explicitly rather than fuzzy-matched on company name. Matching
by name silently picked ``AXBETF`` (Axis Banking *ETF*) for Axis Bank and OFS
lines for NTPC and Coal India — a wrong code backtests the wrong instrument and
nothing complains, so every code here is verified against the master instead.
"""

import csv
import io
import json
import zipfile
from pathlib import Path
from urllib.request import urlopen

MASTER_URL = "https://directlink.icicidirect.com/NewSecurityMaster/SecurityMaster.zip"
CACHE = Path(__file__).parent / "data" / "NSEScripMaster.txt"

# 40 liquid, long-history NSE names across sectors.
CODES = [
    "RELIND", "TCS", "HDFBAN", "ICIBAN", "INFTEC", "HINLEV", "ITC", "STABAN",
    "BHAAIR", "KOTMAH", "LARTOU", "BAJFI", "ASIPAI", "AXIBAN", "MARUTI",
    "SUNPHA", "TITIND", "NESIND", "ULTCEM", "WIPRO", "ONGC", "NTPC", "POWGRI",
    "TATCOV", "TATSTE", "JSWSTE", "COALIN", "GRASIM", "TECMAH", "HCLTEC",
    "DRREDD", "CIPLA", "EICMOT", "HERHON", "BAAUTO", "BRIIND", "INDBA",
    "ADAPOR", "MAHMAH", "BHAELE",
]

# Instrument types that are not plain equity and must never enter the universe.
EXCLUDE_TOKENS = ("ETF", " OFS", "DVR", "PARTLY", "RIGHTS", "WARRANT")


def master_index():
    """Map EQ-series ShortName -> CompanyName, caching the master locally."""
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(MASTER_URL) as resp:
            blob = resp.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            CACHE.write_bytes(z.read("NSEScripMaster.txt"))

    rows = list(csv.reader(io.StringIO(CACHE.read_text(errors="replace")),
                          skipinitialspace=True))
    header = [h.strip().strip('"') for h in rows[0]]
    i_code = header.index("ShortName")
    i_series = header.index("Series")
    i_name = header.index("CompanyName")

    index = {}
    for r in rows[1:]:
        if len(r) <= max(i_code, i_series, i_name):
            continue
        if r[i_series].strip().strip('"').upper() != "EQ":
            continue
        index[r[i_code].strip().strip('"')] = r[i_name].strip().strip('"').upper()
    return index


def resolve(codes=None):
    """Validate codes against the master. Returns (universe, problems)."""
    codes = CODES if codes is None else codes
    index = master_index()
    universe, problems = [], []
    for code in codes:
        name = index.get(code)
        if name is None:
            problems.append(f"{code}: not an EQ-series code in the master")
            continue
        bad = [t for t in EXCLUDE_TOKENS if t in name]
        if bad:
            problems.append(f"{code}: '{name}' looks like {bad[0].strip()}, not plain equity")
            continue
        universe.append({"code": code, "label": name.title()})
    return universe, problems


if __name__ == "__main__":
    universe, problems = resolve()
    path = Path(__file__).parent / "data" / "universe.json"
    path.write_text(json.dumps(universe, indent=2))
    for u in universe:
        print(f"  {u['code']:<8} {u['label']}")
    if problems:
        print("\n  PROBLEMS:")
        for p in problems:
            print(f"    {p}")
    print(f"\n  {len(universe)}/{len(CODES)} validated -> {path}")
