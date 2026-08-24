"""
Find and quantify a disclosure signal in the matched corpus.

WHY THIS EXISTS

Aggregate NEW counts hide the finding. FY2023 shows 163 newly disclosed risks
across the panel -- the second LOWEST of four years -- which looks like evidence
against a post-SVB disclosure response. Search the same data for deposit and
liquidity language and the picture inverts:

    FY2022   2 new
    FY2023  19 new
    FY2024   2 new
    FY2025   0 new

The effect is real and concentrated. It was invisible in the totals because 19
risks out of 163 is a small share of everything that changed that year.

This is the argument for retaining full risk HEADING TEXT rather than only a
category label: the question "which banks newly disclosed a deposit
concentration risk" cannot be answered from a four-way category, and a coarser
taxonomy would have made the headline finding unreachable.

WHAT IT REPORTS

  * label counts by fiscal year for headings matching a pattern
  * how many DISTINCT COMPANIES, not just how many risks -- 19 new risks across
    19 banks is a sector-wide response; 19 across 4 banks is an outlier story,
    and the two support completely different claims
  * a Wilson interval on the share of the panel, because "38% of banks" from
    n=50 is an estimate and should be written as one
  * the actual headings, because a keyword count nobody read is not evidence

Usage:
    python find_signal.py --keywords "uninsured|deposit concentration|bank failure"
    python find_signal.py --preset deposits
    python find_signal.py --preset deposits --show
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from collections import Counter, defaultdict

from config import DATA

MATCHES = DATA / "interim" / "yoy_matches.csv"

PRESETS = {
    "deposits": r"uninsured|deposit concentration|deposit outflow|bank failure|"
                r"liquidity risk|depositor confidence|run on|withdraw",
    "cyber": r"cyber|information security|data breach|ransomware|"
             r"unauthorized (access|occurrence)",
    "ai": r"artificial intelligence|\bAI\b|machine learning|generative",
    "climate": r"climate|weather|natural disaster|environmental",
    "rates": r"interest rate|net interest|yield curve|unrealized loss|"
             r"held.to.maturity|securities portfolio",
    "crypto": r"crypto|digital asset|stablecoin|blockchain",
    "cre": r"commercial real estate|\bCRE\b|office (property|building|space)",
}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--show", action="store_true", help="print the headings")
    ap.add_argument("--label", default="NEW")
    args = ap.parse_args()

    pattern = args.keywords or (PRESETS[args.preset] if args.preset else None)
    if not pattern:
        ap.error("give --keywords or --preset")
    if not MATCHES.exists():
        raise SystemExit("No matches. Run match_yoy.py --run first.")

    kw = re.compile(pattern, re.I)
    with MATCHES.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    panel = len({r["cik"] for r in rows})
    hits = [r for r in rows if kw.search(r["heading"])]

    print("=" * 74)
    print(f"  SIGNAL: {args.preset or pattern[:50]}")
    print("=" * 74)
    print(f"  {len(hits):,} matching rows of {len(rows):,}   panel: {panel} companies")

    years = sorted({r["fiscal_year"] for r in rows})
    labels = ["NEW", "CARRIED_FORWARD", "MATERIALLY_REVISED", "DROPPED", "AMBIGUOUS"]

    print("\n  " + "-" * 70)
    print(f"  {'FY':<6}" + "".join(f"{l[:11]:>13}" for l in labels))
    print("  " + "-" * 70)
    by_year = defaultdict(Counter)
    for r in hits:
        by_year[r["fiscal_year"]][r["label"]] += 1
    for fy in years:
        print(f"  {fy:<6}" + "".join(f"{by_year[fy][l]:>13}" for l in labels))

    # Distinct companies is the number that supports a claim about the sector.
    print("\n  " + "-" * 70)
    print(f"  DISTINCT COMPANIES with a {args.label} matching risk")
    print("  " + "-" * 70)
    for fy in years:
        firms = {r["ticker"] for r in hits
                 if r["fiscal_year"] == fy and r["label"] == args.label}
        n = len(firms)
        lo, hi = wilson(n, panel)
        bar = "#" * int(40 * n / max(panel, 1))
        print(f"  {fy}   {n:>3} of {panel}  = {100*n/panel:>5.1f}%   "
              f"95% CI {100*lo:>4.1f}-{100*hi:>4.1f}%  {bar}")

    peak = max(years, key=lambda fy: len(
        {r["ticker"] for r in hits
         if r["fiscal_year"] == fy and r["label"] == args.label}))
    firms = sorted({r["ticker"] for r in hits
                    if r["fiscal_year"] == peak and r["label"] == args.label})

    print(f"\n  Peak year FY{peak}: {len(firms)} companies")
    print(f"    {', '.join(firms)}")

    if args.show:
        print("\n" + "=" * 74)
        print(f"  THE ACTUAL HEADINGS  --  {args.label} in FY{peak}")
        print("=" * 74)
        print("  Read these. A keyword count nobody read is not evidence.")
        for r in sorted((r for r in hits
                         if r["fiscal_year"] == peak and r["label"] == args.label),
                        key=lambda r: r["ticker"]):
            flag = "  [review]" if r["review"] else ""
            print(f"\n  {r['ticker']:<7} {r['heading'][:150]}{flag}")

    print("\n" + "-" * 74)
    print("  BEFORE CLAIMING THIS")
    print("  1. Read the headings (--show). Keyword matches are not the risk.")
    print("  2. Report DISTINCT COMPANIES with the interval, not raw risk counts.")
    print("  3. State the panel: surviving, calendar-year, conventionally-filing")
    print("     banks. The banks that actually failed are not in this data.")
    print("  4. Check the matching itself: python match_yoy.py --audit 100")
    print("-" * 74)


if __name__ == "__main__":
    main()
