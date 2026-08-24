"""
Build the slim data extract the Streamlit app reads.

WHY AN EXTRACT

Streamlit Community Cloud deploys from the GitHub repo and can only read files
that are committed. The pipeline's real outputs are not: raw filings are ~2 GB,
and data/interim/risk_factors/ holds full risk-factor text for 10,585 records.
Committing those to make a dashboard work would put regenerable intermediate
output in version control, which the repo deliberately avoids.

So the app gets one purpose-built file: headings and labels, no bodies. Headings
are what the app displays; bodies are what makes the data large. That keeps the
committed artefact small enough to be reasonable and complete enough to be
honest -- the app shows real risk factors from real filings, not a summary
somebody could have typed by hand.

WHAT IT WRITES

  app/data/risks.csv        one row per risk factor: company, year, heading,
                            category, YoY label, similarity
  app/data/quality.json     the accuracy figures, so the dashboard can display
                            its own error rates rather than implying certainty

Usage:
    python build_app_data.py
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from config import DATA, FLAGGED_CIKS

RISK_DIR = DATA / "interim" / "risk_factors"
MATCHES = DATA / "interim" / "yoy_matches.csv"
LLM_ALL = DATA / "predictions" / "llm_all.csv"
BASELINE_ALL = DATA / "predictions" / "baseline_all.csv"
UNIVERSE = DATA / "universe" / "universe.csv"

APP_DIR = Path(__file__).resolve().parents[1] / "app" / "data"

HEADING_CHARS = 240


def main() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- categories
    # Prefer LLM labels; fall back to the keyword baseline where the LLM run is
    # incomplete. Which source produced each row is recorded, because a chart
    # mixing two classifiers without saying so is misleading.
    cats: dict[str, tuple[str, str]] = {}
    if BASELINE_ALL.exists():
        with BASELINE_ALL.open(encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                cats[r["risk_id"]] = (r["category"], "keywords")
    if LLM_ALL.exists():
        with LLM_ALL.open(encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r.get("category"):
                    cats[r["risk_id"]] = (r["category"], "llm")

    # ---------------------------------------------------------- YoY labels
    yoy: dict[str, tuple[str, str, str]] = {}
    dropped_rows = []
    if MATCHES.exists():
        with MATCHES.open(encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r["curr_id"]:
                    yoy[r["curr_id"]] = (r["label"], r["similarity"], r["review"])
                elif r["label"] == "DROPPED":
                    # A dropped risk has no current-year record, so it would
                    # vanish from a table keyed on current-year risks. Kept as
                    # its own row -- disappearances are half the story.
                    dropped_rows.append(r)

    # ---------------------------------------------------------- flagged
    flagged = {str(cik): v[2] for cik, v in FLAGGED_CIKS.items()}

    rows = []
    for path in sorted(RISK_DIR.glob("*.json")):
        try:
            f = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for i, r in enumerate(f["risks"]):
            rid = f"{f['cik']}_FY{f['fiscal_year']}_{i:03d}"
            cat, src = cats.get(rid, ("", ""))
            lab, sim, rev = yoy.get(rid, ("", "", ""))
            rows.append({
                "risk_id": rid, "cik": f["cik"], "ticker": f["ticker"],
                "company": f["name"], "fiscal_year": f["fiscal_year"],
                "heading": r["heading"][:HEADING_CHARS],
                "filer_category": (r["category"] or "")[:80],
                "category": cat, "category_source": src,
                "yoy_label": lab, "similarity": sim, "review": rev,
                "chars": r["chars"],
                "caveat": "y" if str(f["cik"]) in flagged else "",
                "source_url": f["source_url"],
            })

    for r in dropped_rows:
        # fiscal_year arrives as str from the matches CSV and int from the risk
        # JSONs. Mixing them makes the column unsortable and silently breaks
        # any year filter in the app, so it is normalised here rather than in
        # three places downstream.
        rows.append({
            "risk_id": r["prev_id"], "cik": int(r["cik"]), "ticker": r["ticker"],
            "company": "", "fiscal_year": int(r["fiscal_year"]),
            "heading": r["heading"][:HEADING_CHARS], "filer_category": "",
            "category": "", "category_source": "",
            "yoy_label": "DROPPED", "similarity": r["similarity"],
            "review": r["review"], "chars": 0,
            "caveat": "y" if str(r["cik"]) in flagged else "",
            "source_url": "",
        })

    out = APP_DIR / "risks.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---------------------------------------------------------- quality
    # Hardcoded from EVALUATION.md rather than recomputed. These are
    # hand-measured figures; deriving them here would imply they came from the
    # data automatically, which is the opposite of the point.
    quality = {
        "filings": 250, "companies": 50, "risk_factors": len(rows),
        "parse_rate": 1.00,
        "split_accuracy": 0.893, "split_n": 300,
        "baseline_accuracy": 0.827, "baseline_macro_f1": 0.814,
        "llm_accuracy": 0.937, "llm_macro_f1": 0.930, "classifier_n": 300,
        "match_false_rate": 0.083, "match_missed_rate": 0.438, "match_n": 48,
        "finding_strict": 8, "finding_all": 13, "panel": 50,
        "finding_strict_ci": [0.083, 0.285], "finding_all_ci": [0.159, 0.396],
        "end_to_end": 0.84,
        "llm_coverage": sum(1 for r in rows if r["category_source"] == "llm"),
        "baseline_coverage": sum(1 for r in rows if r["category_source"] == "keywords"),
    }
    (APP_DIR / "quality.json").write_text(json.dumps(quality, indent=2))

    size_kb = out.stat().st_size / 1024
    print("=" * 70)
    print(f"  APP DATA  --  {len(rows):,} rows, {size_kb:,.0f} KB")
    print("=" * 70)
    print(f"  companies      {len({r['ticker'] for r in rows})}")
    print(f"  fiscal years   {sorted({r['fiscal_year'] for r in rows})}")
    src = Counter(r["category_source"] for r in rows)
    print(f"\n  category source:")
    for k, v in src.most_common():
        print(f"    {k or '(none)':<12} {v:>7,}  {100*v/len(rows):>5.1f}%")
    if src.get("keywords"):
        print("    ^ the LLM run is incomplete; the app labels these rows as")
        print("      keyword-classified rather than presenting one blended chart")
    lab = Counter(r["yoy_label"] for r in rows if r["yoy_label"])
    print(f"\n  YoY labels:")
    for k, v in lab.most_common():
        print(f"    {k:<20} {v:>7,}")
    print(f"\n  wrote {out}")
    print(f"  wrote {APP_DIR / 'quality.json'}")


if __name__ == "__main__":
    main()
