"""
Measure how risk-factor boundaries are marked, across all extracted sections.

WHY THIS COMES BEFORE THE SPLITTER

The same reason profile_filings.py came before the extractor. Writing a splitter
on the assumption "bold text marks a risk factor" would work for most filings
and silently produce one enormous blob for the rest. The extraction report
already shows the problem: median 50 bold runs per section, but UBSI has 2
across 60,000 characters and ALLY has 5 across 103,000.

Two things this answers:

  1. For filings WITH bold headings -- what do they look like, and what noise
     comes with them? Already spotted by eye: "Table of Contents" and bare page
     numbers ("28", "29") appear as bold runs and would become phantom risk
     factors. This counts how much of that there is.

  2. For filings WITHOUT bold headings -- what else separates one risk from the
     next? Candidates visible in plain text: ALL CAPS lines, short lines ending
     without a period, sentence-shaped lines that read as headings, or bullet
     markers. This prints raw consecutive blocks so the pattern is visible
     rather than guessed.

Output is a report to read, plus per-filing counts written to CSV.

Usage:
    python profile_headings.py
    python profile_headings.py --show UBSI --fy 2022     # raw blocks, one filing
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import Counter

from config import DATA

SECTION_DIR = DATA / "interim" / "item1a"
OUT = DATA / "interim" / "heading_profile.csv"

# Filings below this many bold runs need a different splitting strategy.
BOLD_POOR = 20

PAGE_NUM = re.compile(r"^\d{1,4}$")
FURNITURE = re.compile(
    r"^(table of contents|index|part\s+[ivx]+|form\s+10-k|"
    r"annual report|glossary|back to contents)\b", re.I)


def classify(text: str) -> str:
    """Rough shape of a text run, for counting."""
    if PAGE_NUM.match(text):
        return "page_number"
    if FURNITURE.match(text):
        return "furniture"
    if len(text) < 4:
        return "tiny"
    if text.isupper():
        return "ALL_CAPS"
    if len(text) > 400:
        return "long_paragraph"
    if text.endswith((".", "?", "!")):
        return "sentence_heading"
    return "short_phrase"


def load_all() -> list[dict]:
    out = []
    for path in sorted(SECTION_DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print(f"  unreadable: {path.name}")
    return out


def show_raw(sec: dict, n: int = 45) -> None:
    """Print consecutive blocks so the boundary signal is visible."""
    print("=" * 78)
    print(f"  {sec['ticker']} FY{sec['fiscal_year']}  --  {sec['name'][:44]}")
    print(f"  {sec['section_chars']:,} chars | {sec['n_blocks']} blocks | "
          f"{sec['n_bold_blocks']} bold")
    print(f"  {sec['source_url']}")
    print("=" * 78)
    print("  B = bold.  Look for what separates one risk factor from the next.\n")
    for b in sec["blocks"][:n]:
        mark = "B" if b["bold"] else " "
        txt = b["text"]
        shown = txt if len(txt) <= 110 else txt[:107] + "..."
        print(f"  [{mark}] {classify(txt):<17} {shown}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show")
    ap.add_argument("--fy", type=int)
    ap.add_argument("--blocks", type=int, default=45)
    args = ap.parse_args()

    sections = load_all()
    if not sections:
        raise SystemExit(f"No sections in {SECTION_DIR}. Run extract_item1a.py first.")

    if args.show:
        picked = [s for s in sections
                  if s["ticker"].upper() == args.show.upper()
                  and (args.fy is None or s["fiscal_year"] == args.fy)]
        if not picked:
            raise SystemExit(f"No section for {args.show} FY{args.fy or '*'}")
        for s in sorted(picked, key=lambda s: s["fiscal_year"])[:1]:
            show_raw(s, args.blocks)
        return

    rich = [s for s in sections if s["n_bold_blocks"] >= BOLD_POOR]
    poor = [s for s in sections if s["n_bold_blocks"] < BOLD_POOR]

    print("=" * 78)
    print(f"  HEADING PROFILE -- {len(sections)} sections")
    print("=" * 78)
    print(f"  bold-rich (>= {BOLD_POOR} bold runs)   {len(rich):>4}   "
          f"bold splitting should work")
    print(f"  bold-poor (<  {BOLD_POOR} bold runs)   {len(poor):>4}   "
          f"need a second strategy")

    # ---------------------------------------------------- bold-rich analysis
    if rich:
        kinds: Counter = Counter()
        lengths: list[int] = []
        for s in rich:
            for b in s["blocks"]:
                if b["bold"]:
                    kinds[classify(b["text"])] += 1
                    lengths.append(len(b["text"]))
        total = sum(kinds.values())
        print("\n" + "-" * 78)
        print(f"  WHAT THE BOLD RUNS ARE  ({total:,} runs across {len(rich)} filings)")
        print("-" * 78)
        for kind, n in kinds.most_common():
            bar = "#" * int(40 * n / max(total, 1))
            print(f"    {kind:<18} {n:>7,}  {100*n/total:>5.1f}%  {bar}")
        noise = kinds["page_number"] + kinds["furniture"] + kinds["tiny"]
        print(f"\n    NOISE (page numbers, furniture, tiny) = {noise:,} "
              f"= {100*noise/max(total,1):.1f}% of all bold runs")
        print("    Every one of these would become a phantom risk factor.")
        if lengths:
            print(f"\n    bold run length: median {statistics.median(lengths):.0f} chars, "
                  f"90th pct {sorted(lengths)[int(.9*len(lengths))]:.0f}")

        print("\n  SAMPLE BOLD RUNS (first bold-rich filing):")
        first = rich[0]
        shown = 0
        for b in first["blocks"]:
            if b["bold"] and shown < 14:
                print(f"    {classify(b['text']):<17} {b['text'][:88]}")
                shown += 1

    # ---------------------------------------------------- bold-poor analysis
    if poor:
        print("\n" + "-" * 78)
        print(f"  BOLD-POOR FILINGS ({len(poor)}) -- these need another signal")
        print("-" * 78)
        by_co: dict = {}
        for s in poor:
            by_co.setdefault(s["ticker"], []).append(s)
        for ticker, items in sorted(by_co.items(), key=lambda kv: -len(kv[1])):
            years = sorted(i["fiscal_year"] for i in items)
            chars = statistics.median(i["section_chars"] for i in items)
            bolds = statistics.median(i["n_bold_blocks"] for i in items)
            blocks = statistics.median(i["n_blocks"] for i in items)
            print(f"    {ticker:<7} {len(items)} yrs {years}  "
                  f"median {chars:>8,.0f} chars, {blocks:>5.0f} blocks, "
                  f"{bolds:.0f} bold")

        # What shapes do their NON-bold blocks take? That is where the
        # boundary signal must be hiding.
        kinds: Counter = Counter()
        for s in poor:
            for b in s["blocks"]:
                if not b["bold"]:
                    kinds[classify(b["text"])] += 1
        total = sum(kinds.values())
        print(f"\n    Shape of their NON-bold blocks ({total:,}):")
        for kind, n in kinds.most_common():
            print(f"      {kind:<18} {n:>7,}  {100*n/max(total,1):>5.1f}%")

        print("\n    Inspect one with:")
        print(f"      python profile_headings.py --show {poor[0]['ticker']} "
              f"--fy {poor[0]['fiscal_year']}")

    # ---------------------------------------------------- csv
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "cik", "ticker", "fiscal_year", "section_chars", "n_blocks",
            "n_bold_blocks", "strategy"])
        w.writeheader()
        for s in sections:
            w.writerow({
                "cik": s["cik"], "ticker": s["ticker"],
                "fiscal_year": s["fiscal_year"],
                "section_chars": s["section_chars"], "n_blocks": s["n_blocks"],
                "n_bold_blocks": s["n_bold_blocks"],
                "strategy": "bold" if s["n_bold_blocks"] >= BOLD_POOR else "NEEDS_FALLBACK",
            })
    print(f"\n  wrote {OUT}")


if __name__ == "__main__":
    main()
