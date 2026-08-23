"""
Pull the full evidence behind each flagged audit row.

The audit worksheet carries only the first 200 and last 120 characters of each
risk factor, which is enough to label most rows but not enough to confirm a
MERGED verdict -- a 43,000-character record is invisible in the middle, so the
call rests on length and position rather than on reading.

This prints, for every row labelled F, M or T:
  * how many risks the splitter found in that whole filing (the decisive number:
    a bank Item 1A holds 30-50 risk factors, so 3 is a merge and 45 is not)
  * the record itself, with more of the body
  * the records immediately before and after it, which is where a merge or a
    truncation shows up

Usage:
    python verify_audit.py
    python verify_audit.py --labels F        # only fragments
    python verify_audit.py --body 1200       # more body text per record
"""

from __future__ import annotations

import argparse
import csv
import json

from config import DATA

RISK_DIR = DATA / "interim" / "risk_factors"
WORKSHEET = DATA / "interim" / "split_audit.csv"


def load_filing(cik: str, fy: str) -> dict | None:
    path = RISK_DIR / f"{cik}_FY{fy}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="FMT?",
                    help="which labels to expand (default FMT?)")
    ap.add_argument("--body", type=int, default=700)
    ap.add_argument("--worksheet", default=str(WORKSHEET))
    args = ap.parse_args()

    with open(args.worksheet, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    wanted = set(args.labels.upper())
    flagged = [r for r in rows if r["label"].strip().upper() in wanted]
    if not flagged:
        raise SystemExit(f"No rows labelled {args.labels} in {args.worksheet}")

    print(f"Expanding {len(flagged)} flagged rows of {len(rows)}\n")

    for r in flagged:
        filing = load_filing(r["cik"], r["fiscal_year"])
        print("=" * 78)
        print(f"  [{r['label'].upper()}]  {r['ticker']} FY{r['fiscal_year']}  "
              f"risk #{r['risk_index']}  ({r['chars']} chars)")
        if r["note"].strip():
            print(f"  claimed reason: {r['note'][:150]}")
        print("=" * 78)

        if filing is None:
            print("  filing json not found\n")
            continue

        risks = filing["risks"]
        n = len(risks)
        # THE decisive number for a merge verdict.
        verdict = ("<-- far too few; headings were not detected" if n < 15
                   else "<-- normal range" if n <= 70
                   else "<-- unusually many; possible over-splitting")
        print(f"\n  THIS FILING CONTAINS {n} RISK FACTORS IN TOTAL  {verdict}")
        print(f"  section was {filing['section_chars']:,} chars, "
              f"{filing['furniture_dropped']} furniture runs dropped")

        idx = int(r["risk_index"])
        if idx >= n:
            print("  index out of range (file changed since the sample was drawn)\n")
            continue

        for j in (idx - 1, idx, idx + 1):
            if not (0 <= j < n):
                continue
            rk = risks[j]
            tag = ">>> THE FLAGGED ONE" if j == idx else f"    neighbour #{j}"
            print(f"\n  {tag}   [{rk['category'] or 'no category'}]  {rk['chars']:,} chars")
            print(f"    HEADING: {rk['heading'][:200]}")
            body = rk["body"]
            print(f"    BODY   : {body[:args.body]}")
            if len(body) > args.body * 2:
                print(f"    ...[{len(body) - args.body * 2:,} chars omitted]...")
                print(f"    END    : {body[-args.body:]}")
        print()

    print("-" * 78)
    print("  HOW TO READ THIS")
    print("  MERGED  -> the filing total is very low (3-6) while peers show 30-50,")
    print("             and the flagged record's body wanders across many topics.")
    print("  FRAGMENT-> the heading names a group of risks rather than one risk,")
    print("             or the body is not risk-factor content at all.")
    print("  WRONG LABEL -> the filing total is normal and the body stays on the")
    print("             heading's topic from start to end.")
    print("-" * 78)


if __name__ == "__main__":
    main()
