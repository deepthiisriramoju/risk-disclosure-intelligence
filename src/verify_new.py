"""
Verify that specific NEW risks are genuinely new.

WHY A SEPARATE CHECK

The match audit put the corpus-wide missed-match rate at 43.8% (n=48). Nearly
half of everything labelled NEW was a reworded existing risk, counted once as
NEW and again as DROPPED. That rate is dominated by synonym rewrites and COVID
rewording, heavily concentrated in one company-year.

A corpus-wide rate does not tell you whether YOUR finding survives. The headline
claim rests on 17 specific risk factors in one fiscal year, and those are
different in kind: new vocabulary about a new event, not paraphrases of existing
text. That is a plausible argument and plausible is not evidence.

So this checks the finding directly. For every NEW risk matching a pattern, it
lists the prior-year risks from the SAME company ranked by similarity, so each
one can be confirmed to have no counterpart rather than assumed to have none.

WHAT COUNTS AS DISCONFIRMING

A prior-year risk that a reader would call the same risk. Not a similar score --
the whole point is that the score failed. Read the headings.

Usage:
    python verify_new.py --preset deposits --fy 2023
    python verify_new.py --preset deposits --fy 2023 --top 5
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict

from config import DATA
from match_yoy import (
    MATCH_MIN, build_idf, similarity, tokens, vectorise,
)
from find_signal import PRESETS

RISK_DIR = DATA / "interim" / "risk_factors"
MATCHES = DATA / "interim" / "yoy_matches.csv"
OUT = DATA / "interim" / "verify_new.csv"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load_by_company() -> dict:
    out: dict = defaultdict(dict)
    for path in sorted(RISK_DIR.glob("*.json")):
        try:
            f = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out[f["cik"]][f["fiscal_year"]] = [
            {"risk_id": f"{f['cik']}_FY{f['fiscal_year']}_{i:03d}",
             "ticker": f["ticker"], "heading": r["heading"], "body": r["body"]}
            for i, r in enumerate(f["risks"])
        ]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--keywords")
    ap.add_argument("--fy", type=int, required=True)
    ap.add_argument("--top", type=int, default=5,
                    help="prior-year candidates to show per risk")
    ap.add_argument("--all", action="store_true",
                    help="list EVERY prior-year heading, not just the closest. "
                         "Slower to read, but the only version that cannot miss "
                         "a zero-similarity counterpart")
    args = ap.parse_args()

    pattern = args.keywords or (PRESETS[args.preset] if args.preset else None)
    if not pattern:
        ap.error("give --preset or --keywords")
    kw = re.compile(pattern, re.I)

    with MATCHES.open(encoding="utf-8-sig") as fh:
        matches = list(csv.DictReader(fh))
    targets = [r for r in matches
               if r["label"] == "NEW" and r["fiscal_year"] == str(args.fy)
               and kw.search(r["heading"])]
    if not targets:
        raise SystemExit(f"No NEW rows matching that pattern in FY{args.fy}.")

    by_co = load_by_company()

    # IDF over the whole corpus, exactly as the matcher builds it, so the
    # scores shown here are the same scores the matcher rejected.
    heads, bodies, ids, index = [], [], [], {}
    for years in by_co.values():
        for risks in years.values():
            for r in risks:
                index[r["risk_id"]] = r
                heads.append(tokens(r["heading"]))
                bodies.append(tokens(r["body"]))
                ids.append(r["risk_id"])
    idf_h, idf_b = build_idf(heads), build_idf(bodies)
    vecs = {rid: (vectorise(h, idf_h), vectorise(b, idf_b))
            for rid, h, b in zip(ids, heads, bodies)}

    print("=" * 78)
    print(f"  VERIFYING {len(targets)} NEW RISKS IN FY{args.fy}")
    print(f"  pattern: {args.preset or pattern[:50]}")
    print("=" * 78)
    print("  For each, the closest prior-year risks from the SAME company.")
    print("  Ask: would a reader call any of these the same risk? If yes, the")
    print("  NEW label is wrong and the headline count must come down.\n")

    rows = []
    for t in sorted(targets, key=lambda r: r["ticker"]):
        cid = t["curr_id"]
        cik = int(t["cik"])
        prior = by_co.get(cik, {}).get(args.fy - 1, [])
        ranked = sorted(((similarity(vecs[cid], vecs[p["risk_id"]]), p)
                         for p in prior if p["risk_id"] in vecs),
                        key=lambda x: (-x[0], x[1]["heading"]))
        cand = ranked if args.all else ranked[: args.top]

        print("-" * 78)
        print(f"  {t['ticker']}   {t['heading'][:110]}")
        shown = "all" if args.all else f"closest {args.top}"
        print(f"  {shown} FY{args.fy - 1} candidates ({len(prior)} risks that year):")
        for score, p in cand:
            mark = "  <-- ABOVE MATCH_MIN" if score >= MATCH_MIN else ""
            print(f"      {score:.3f}  {p['heading'][:96]}{mark}")
        if not cand:
            print("      (no prior-year risks found)")

        # A pure synonym rewrite scores near zero, so ranking by similarity
        # cannot surface it -- it ties with every unrelated risk and may fall
        # outside the top N. That is precisely the failure this check exists to
        # catch, so the tie has to be visible rather than silently truncated.
        if not args.all:
            tied = sum(1 for sc, _ in ranked[args.top:] if sc <= 0.05)
            if tied:
                print(f"      ... {tied} further prior-year risks scored under 0.05.")
                print("      A synonym rewrite would sit among them, invisible to this")
                print("      ranking. Re-run with --all before calling this one new.")
        rows.append({
            "verdict": "", "ticker": t["ticker"], "fiscal_year": args.fy,
            "new_heading": t["heading"][:300],
            "top_prior_sim": round(cand[0][0], 4) if cand else 0.0,
            "top_prior_heading": cand[0][1]["heading"][:300] if cand else "",
            "curr_id": cid,
        })

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    panel = len({r["cik"] for r in matches})
    firms = len({r["ticker"] for r in targets})
    lo, hi = wilson(firms, panel)
    print("\n" + "=" * 78)
    print(f"  {len(targets)} risks across {firms} companies of {panel}"
          f" = {100*firms/panel:.1f}%   95% CI {100*lo:.1f}-{100*hi:.1f}%")
    print("=" * 78)
    print(f"\n  wrote {OUT}")
    print("\n  Put y (genuinely new) or n (has a prior-year counterpart) in the")
    print("  'verdict' column. The count of y's, over the panel, is the number")
    print("  to publish -- with the interval, and with the corpus-wide")
    print("  missed-match rate stated as a separate limitation.")
    if not args.all:
        print("\n  NOTE: this run showed only the closest candidates. Similarity")
        print("  ranking cannot surface a synonym rewrite, which is the very error")
        print("  being checked for. Use --all before publishing the number.")


if __name__ == "__main__":
    main()
