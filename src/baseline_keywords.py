"""
Keyword baseline for risk-factor category classification.

WHY A BASELINE EXISTS

Suppose the LLM scores 87% on category. Is that good?

You cannot answer that without knowing what a dumb approach gets. If a list of
keywords gets 71%, the LLM is worth +16 points and worth its cost. If keywords
get 85%, the LLM is buying two points for an API bill and a dependency, and the
honest conclusion is to use keywords for this field and say so.

Reporting a raw accuracy figure with nothing to compare it against is the most
common weakness in applied-ML portfolio work. This file is the comparison.

DESIGN RULES, AND WHY

  1. HAND-WRITTEN, NOT LEARNED. These rules are written from domain reading, not
     fitted to the gold set. A baseline tuned on the gold set is not a baseline
     -- it is a model evaluated on its own training data, and it will beat the
     LLM for the wrong reason.

  2. HEADINGS COUNT TRIPLE. A risk factor's heading states what the risk IS;
     the body lists consequences that spill across every category. The same
     reasoning drove the labelling rubric, and it should drive the baseline.

  3. TIES GO TO 'financial'. Bank risk factors skew financial, and a fixed
     tie-break keeps the baseline deterministic. An arbitrary but stated rule
     beats an arbitrary and unstated one.

  4. NO MATCH IS STILL A PREDICTION. Every item gets a label. Abstaining would
     make precision look better while hiding coverage, which is exactly the
     dishonesty the evaluation is meant to prevent.

Usage:
    python baseline_keywords.py --gold        # predict for the gold set only
    python baseline_keywords.py --all         # predict for all risk factors
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter

from config import DATA

RISK_DIR = DATA / "interim" / "risk_factors"
GOLD = DATA / "gold" / "gold_set.csv"
OUT_DIR = DATA / "predictions"

HEADING_WEIGHT = 3
BODY_WEIGHT = 1
TIE_BREAK = "financial"

# Terms drawn from reading bank risk factors, grouped by what the risk IS.
# Word-boundary matched, case-insensitive. Stems (regulat, compet) catch
# inflections without a stemmer.
KEYWORDS: dict[str, list[str]] = {
    "financial": [
        # credit
        "loan", "loans", "lending", "borrower", "credit risk", "credit loss",
        "default", "delinquen", "charge-off", "charge off", "nonperforming",
        "non-performing", "allowance for credit", "allowance for loan",
        "collateral", "underwriting", "cecl", "provision for credit",
        "asset quality", "foreclosure", "loan portfolio", "concentration",
        # liquidity and funding
        "liquidity", "funding", "deposit", "deposits", "wholesale funding",
        "borrowings", "capital markets", "cash flow", "dividend",
        # market
        "interest rate", "net interest", "yield curve", "securities",
        "investment portfolio", "fair value", "impairment", "valuation",
        "libor", "sofr", "benchmark rate", "trading", "mortgage banking",
        "economic conditions", "economy", "recession", "inflation",
        "climate change", "market conditions",
    ],
    "operational": [
        "cyber", "cybersecurity", "information security", "data breach",
        "breach", "hacking", "malware", "ransomware", "phishing",
        "information technology", "systems", "system failure", "outage",
        "business continuity", "disaster recovery", "third-party", "third party",
        "vendor", "service provider", "outsourc", "fraud", "theft",
        "employee", "personnel", "talent", "key employees", "human capital",
        "internal control", "operational risk", "model risk", "models",
        "data privacy", "artificial intelligence", "technology systems",
    ],
    "regulatory": [
        "regulat", "supervis", "examination", "compliance", "statute",
        "legislation", "legislative", "law", "laws", "rulemaking",
        "litigation", "legal proceeding", "lawsuit", "enforcement",
        "penalt", "fine", "sanction", "consent order", "cease and desist",
        "dodd-frank", "basel", "capital requirement", "capital adequacy",
        "stress test", "ccar", "cfpb", "fdic", "occ", "federal reserve",
        "sec ", "anti-money laundering", "money laundering", "bank secrecy",
        "community reinvestment", "tax law", "accounting standard",
        "government", "governmental",
    ],
    "strategic": [
        "compet", "competition", "competitor", "market share",
        "acquisition", "acquire", "merger", "integration", "divestiture",
        "strategy", "strategic", "business strategy", "growth",
        "expansion", "new products", "new lines of business",
        "innovat", "disrupt", "fintech", "digital", "branch network",
        "reputation", "reputational", "brand", "public perception",
        "environmental, social", "esg", "stakeholder",
    ],
}

COMPILED = {
    cat: [re.compile(r"\b" + re.escape(t).replace(r"\ ", r"\s+"), re.I) for t in terms]
    for cat, terms in KEYWORDS.items()
}


def classify(heading: str, body: str) -> tuple[str, dict]:
    """Return (category, per-category score) for one risk factor."""
    scores = {c: 0 for c in KEYWORDS}
    for cat, patterns in COMPILED.items():
        for pat in patterns:
            scores[cat] += HEADING_WEIGHT * len(pat.findall(heading))
            scores[cat] += BODY_WEIGHT * len(pat.findall(body))
    best = max(scores.values())
    if best == 0:
        return TIE_BREAK, scores
    winners = [c for c, v in scores.items() if v == best]
    return (TIE_BREAK if TIE_BREAK in winners else sorted(winners)[0]), scores


def load_all_risks() -> list[dict]:
    out = []
    for path in sorted(RISK_DIR.glob("*.json")):
        try:
            f = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for i, r in enumerate(f["risks"]):
            out.append({
                "risk_id": f"{f['cik']}_FY{f['fiscal_year']}_{i:03d}",
                "heading": r["heading"], "body": r["body"],
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", action="store_true", help="gold-set items only")
    ap.add_argument("--all", action="store_true", help="every risk factor")
    args = ap.parse_args()
    if not (args.gold or args.all):
        ap.error("pick --gold or --all")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.gold:
        if not GOLD.exists():
            raise SystemExit("No gold set. Run build_gold_set.py --draw first.")
        with GOLD.open(encoding="utf-8-sig") as fh:
            items = [r for r in csv.DictReader(fh)]
        out_path = OUT_DIR / "baseline_gold.csv"
    else:
        items = load_all_risks()
        out_path = OUT_DIR / "baseline_all.csv"
    if not items:
        raise SystemExit("Nothing to classify.")

    rows, dist, no_match = [], Counter(), 0
    for it in items:
        cat, scores = classify(it["heading"], it.get("body", ""))
        if max(scores.values()) == 0:
            no_match += 1
        rows.append({
            "risk_id": it["risk_id"], "category": cat,
            "margin": max(scores.values()) - sorted(scores.values())[-2],
            **{f"score_{c}": v for c, v in scores.items()},
        })
        dist[cat] += 1

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("=" * 70)
    print(f"  KEYWORD BASELINE -- {len(rows):,} items classified")
    print("=" * 70)
    for cat, n in dist.most_common():
        print(f"    {cat:<14} {n:>6,}  {100*n/len(rows):>5.1f}%  "
              f"{'#' * int(30*n/len(rows))}")
    print(f"\n  no keyword matched at all: {no_match:,} "
          f"({100*no_match/len(rows):.1f}%) -- assigned '{TIE_BREAK}' by rule")
    thin = sum(1 for r in rows if r["margin"] <= 1)
    print(f"  decided by a margin of 1 or less: {thin:,} "
          f"({100*thin/len(rows):.1f}%) -- these are the coin flips")
    print(f"\n  wrote {out_path}")
    print("\n  Score it with:  python evaluate.py --pred "
          f"{out_path.name} --name keywords")


if __name__ == "__main__":
    main()
