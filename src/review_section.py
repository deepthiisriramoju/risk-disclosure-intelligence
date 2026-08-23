"""
Read the extracted Item 1A sections. Verification, not extraction.

WHY THIS IS A SEPARATE STEP

A 97.8% parse rate says 269 filings produced a section of plausible length. It
does NOT say those sections contain the right text. The failure that matters
most in this project is the one that raises no error: a parse that succeeds and
returns plausible but wrong content -- a summary table, a neighbouring section,
or Item 1A plus everything after it.

Nothing detects that except reading. This tool makes reading cheap: it shows the
first and last few hundred characters of a section, which is where correctness
is visible. A correct Item 1A OPENS with heading text or a risk-factor summary,
and ENDS just before Item 1B / 1C / 2.

Usage:
    python review_section.py --suspicious      # the ones most likely wrong
    python review_section.py --ticker ZION
    python review_section.py --ticker HBAN --fy 2025
    python review_section.py --all-shortest 10
"""

from __future__ import annotations

import argparse
import json
import statistics

from config import DATA

SECTION_DIR = DATA / "interim" / "item1a"


def load_all() -> list[dict]:
    out = []
    for path in sorted(SECTION_DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print(f"  unreadable: {path.name}")
    return out


def show(sec: dict, head: int = 600, tail: int = 400) -> None:
    text = sec["text"]
    print("\n" + "=" * 78)
    print(f"  {sec['ticker']} FY{sec['fiscal_year']}  --  {sec['name'][:44]}")
    print(f"  {sec['section_chars']:,} chars | starts {sec['start_pct']}% | "
          f"ends at {sec.get('stop_by') or 'NO TERMINATOR'} | score {sec['score']}")
    print(f"  reasons: {', '.join(sec['why'])}")
    print(f"  bold runs: {sec['n_bold_blocks']} of {sec['n_blocks']} blocks")
    print(f"  source: {sec['source_url']}")
    print("=" * 78)
    print("\n  --- FIRST %d CHARS (should be the heading, then risk prose) ---" % head)
    print("  " + text[:head].replace("\n", " "))
    print("\n  --- LAST %d CHARS (should be the end of a risk, not mid-sentence) ---" % tail)
    print("  " + text[-tail:].replace("\n", " "))

    # Bold runs are the splitter's raw material. Seeing the first few tells you
    # immediately whether they are risk-factor headings or something else.
    bolds = [b["text"] for b in sec["blocks"] if b["bold"]]
    print(f"\n  --- FIRST 12 BOLD RUNS (candidate risk-factor headings) ---")
    if not bolds:
        print("    (none -- this section has no styled headings at all)")
    for b in bolds[:12]:
        print(f"    * {b[:96]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--fy", type=int)
    ap.add_argument("--suspicious", action="store_true")
    ap.add_argument("--all-shortest", type=int, default=0)
    ap.add_argument("--head", type=int, default=600)
    ap.add_argument("--tail", type=int, default=400)
    args = ap.parse_args()

    sections = load_all()
    if not sections:
        raise SystemExit(f"No sections in {SECTION_DIR}. Run extract_item1a.py first.")

    if args.ticker:
        picked = [s for s in sections
                  if s["ticker"].upper() == args.ticker.upper()
                  and (args.fy is None or s["fiscal_year"] == args.fy)]
        if not picked:
            raise SystemExit(f"No section for {args.ticker} FY{args.fy or '*'}")
        for s in sorted(picked, key=lambda s: s["fiscal_year"]):
            show(s, args.head, args.tail)
        return

    if args.all_shortest:
        for s in sorted(sections, key=lambda s: s["section_chars"])[: args.all_shortest]:
            show(s, args.head, args.tail)
        return

    # --------------------------------------------------- suspicious set
    lengths = [s["section_chars"] for s in sections]
    med = statistics.median(lengths)
    flagged: dict[str, list[dict]] = {}

    for s in sections:
        why = []
        if s["section_chars"] < med * 0.4:
            why.append("much shorter than median")
        if s["section_chars"] > med * 2.5:
            why.append("much longer than median -- may have swallowed later sections")
        if not s.get("stop_by"):
            why.append("no terminator found -- ran to end of document")
        if "xref_after" in s["why"] or "xref_before" in s["why"]:
            why.append("won despite a cross-reference penalty")
        if s["n_bold_blocks"] < 10:
            why.append("almost no styled headings -- splitter will fail here")
        if s["score"] < 50:
            why.append("low confidence score")
        for w in why:
            flagged.setdefault(w, []).append(s)

    print(f"{len(sections)} sections. Median length {med:,.0f} chars.\n")
    print("=" * 78)
    print("  SUSPICIOUS SECTIONS BY CATEGORY")
    print("=" * 78)
    seen: set[str] = set()
    for reason, items in sorted(flagged.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  {reason}  ({len(items)})")
        for s in sorted(items, key=lambda s: s["section_chars"])[:12]:
            key = f"{s['ticker']}_{s['fiscal_year']}"
            print(f"      {s['ticker']:<7} FY{s['fiscal_year']}  "
                  f"{s['section_chars']:>8,} chars  score={s['score']}")
            seen.add(key)

    print("\n" + "-" * 78)
    print(f"  {len(seen)} distinct filings worth reading, of {len(sections)}.")
    print("  Read them with:  python review_section.py --ticker XXX --fy 20YY")
    print()
    print("  What you are checking: does the text OPEN with the risk factors")
    print("  heading or summary, and END just before Item 1B/1C/2? If it opens")
    print("  mid-sentence or ends deep inside another section, the extraction")
    print("  is wrong even though nothing errored.")
    print()
    print("  Record what you find in EVALUATION.md as a failure taxonomy:")
    print("  the distinct ways extraction goes wrong, with counts.")
    print("-" * 78)


if __name__ == "__main__":
    main()
