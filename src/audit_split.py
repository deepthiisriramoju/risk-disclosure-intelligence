"""
Measure how often the splitter is right, by reading a random sample.

WHY THIS EXISTS

Every splitter defect so far was found by reading output and noticing something
wrong. That loop has no end: read more output, find another defect. It also
gives no answer to the only question that matters -- what fraction of the
~10,000 risk factors are correct?

Without that number you cannot say whether the known defects affect 3% of
records or 30%, and you cannot tell whether a fix helped or just moved the
problem. This is the same argument the project makes about LLM extraction,
applied one stage earlier.

It also protects week 2. The gold set is 300 hand-labelled risk factors drawn
from this output. If a meaningful share of the population is fragments or
truncations, that contamination propagates into every precision and recall
figure computed against it.

HOW TO USE IT

    python audit_split.py --sample 100        # draw a sample, write a worksheet
    python audit_split.py --score             # after labelling, compute the rate

The sample is stratified across companies and fiscal years and uses a fixed
seed, so it is reproducible and so a re-run after a fix scores the SAME records
-- otherwise an apparent improvement could just be a different sample.

LABELS

    C  CORRECT     heading is a real risk factor and the body belongs to it
    F  FRAGMENT    heading is furniture, a cross-reference, or a stray phrase
    T  TRUNCATED   body is cut off mid-risk, or starts mid-sentence
    M  MERGED      two or more distinct risk factors in one record
    ?  UNSURE      cannot tell without the source document

Label honestly. A splitter measured at 91% with a documented failure taxonomy
is worth more than one asserted to be perfect.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter

from config import DATA

RISK_DIR = DATA / "interim" / "risk_factors"
WORKSHEET = DATA / "interim" / "split_audit.csv"
SEED = 20260729

VALID = {"C": "CORRECT", "F": "FRAGMENT", "T": "TRUNCATED",
         "M": "MERGED", "?": "UNSURE"}


def load_all() -> list[dict]:
    out = []
    for path in sorted(RISK_DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print(f"  unreadable: {path.name}")
    return out


def draw(filings: list[dict], n: int) -> list[dict]:
    """Stratified: spread across companies and years, not clustered."""
    pool = []
    for f in filings:
        for i, r in enumerate(f["risks"]):
            pool.append({
                "cik": f["cik"], "ticker": f["ticker"],
                "fiscal_year": f["fiscal_year"], "risk_index": i,
                "category": r["category"] or "",
                "heading": r["heading"],
                "body_start": r["body"][:200],
                "body_end": r["body"][-120:] if len(r["body"]) > 320 else "",
                "chars": r["chars"],
                "source_url": f["source_url"],
            })
    rng = random.Random(SEED)
    # Shuffle whole filings first so the sample spans the panel rather than
    # over-weighting whichever company happens to have the most risks.
    by_filing: dict = {}
    for row in pool:
        by_filing.setdefault((row["cik"], row["fiscal_year"]), []).append(row)
    keys = list(by_filing)
    rng.shuffle(keys)

    picked: list[dict] = []
    round_no = 0
    while len(picked) < n and round_no < 200:
        for k in keys:
            items = by_filing[k]
            if round_no < len(items):
                picked.append(rng.choice(items) if round_no == 0 else items[round_no])
                if len(picked) >= n:
                    break
        round_no += 1
    return picked[:n]


def cmd_sample(n: int) -> None:
    filings = load_all()
    if not filings:
        raise SystemExit(f"No files in {RISK_DIR}. Run split_risk_factors.py first.")
    total = sum(f["n_risks"] for f in filings)
    rows = draw(filings, n)

    with WORKSHEET.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "label", "note", "ticker", "fiscal_year", "risk_index", "category",
            "heading", "body_start", "body_end", "chars", "cik", "source_url"])
        w.writeheader()
        for r in rows:
            w.writerow({"label": "", "note": "", **r})

    print("=" * 74)
    print(f"  Drew {len(rows)} of {total:,} risk factors from "
          f"{len({(r['cik'], r['fiscal_year']) for r in rows})} filings")
    print("=" * 74)
    print(f"\n  worksheet -> {WORKSHEET}")
    print("\n  Open it in Excel. Put ONE letter in the 'label' column per row:")
    for k, v in VALID.items():
        print(f"      {k}  {v}")
    print("\n  Judge from 'heading', 'body_start' and 'body_end'. If body_start")
    print("  begins lowercase or mid-sentence, that is TRUNCATED. If the heading")
    print("  is a page header or a section name, that is FRAGMENT.")
    print("\n  Save as CSV (not .xlsx), then:  python audit_split.py --score")
    print(f"\n  Seed is fixed at {SEED}, so re-running after a fix scores the")
    print("  same records -- otherwise an improvement could just be a new sample.")


def cmd_score() -> None:
    if not WORKSHEET.exists():
        raise SystemExit("No worksheet. Run --sample first.")
    with WORKSHEET.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    labelled = [r for r in rows if r["label"].strip().upper() in VALID]
    unlabelled = len(rows) - len(labelled)
    if not labelled:
        raise SystemExit("Nothing labelled yet.")

    counts = Counter(r["label"].strip().upper() for r in labelled)
    n = len(labelled)
    correct = counts["C"]

    print("=" * 74)
    print(f"  SPLITTER ACCURACY  {correct}/{n} = {100*correct/n:.1f}%")
    print("=" * 74)
    if unlabelled:
        print(f"  ({unlabelled} rows still unlabelled)")

    print("\n  FAILURE TAXONOMY")
    for code, name in VALID.items():
        c = counts.get(code, 0)
        if c:
            print(f"    {name:<10} {c:>4}  {100*c/n:>5.1f}%  {'#' * int(40*c/n)}")

    # Wilson score interval: at n=100 the naive +/- is misleadingly tight.
    import math
    p, z = correct / n, 1.96
    denom = 1 + z*z/n
    centre = (p + z*z/(2*n)) / denom
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denom
    print(f"\n  95% confidence interval: "
          f"{100*(centre-half):.1f}% to {100*(centre+half):.1f}%")
    print(f"  (Wilson interval, n={n}. A wider sample narrows this.)")

    bad = [r for r in labelled if r["label"].strip().upper() in ("F", "T", "M")]
    if bad:
        by_ticker = Counter(r["ticker"] for r in bad)
        print("\n  ERRORS BY COMPANY (concentrated = one fixable cause):")
        for t, c in by_ticker.most_common(10):
            print(f"    {t:<8} {c}")
        print("\n  SAMPLE ERRORS:")
        for r in bad[:8]:
            print(f"    [{r['label'].upper()}] {r['ticker']} FY{r['fiscal_year']}  "
                  f"{r['heading'][:66]}")
            if r["note"].strip():
                print(f"          note: {r['note'][:70]}")

    print("\n  Put this number and the taxonomy in EVALUATION.md. Reporting")
    print("  accuracy per pipeline stage -- parse, split, extract -- is what")
    print("  makes the end-to-end figure meaningful, because errors compound.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, metavar="N")
    ap.add_argument("--score", action="store_true")
    args = ap.parse_args()
    if args.sample:
        cmd_sample(args.sample)
    elif args.score:
        cmd_score()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
