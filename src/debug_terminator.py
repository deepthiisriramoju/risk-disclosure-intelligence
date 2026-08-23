"""
Show every Item 1B / 1C / 2 candidate in a filing and why it was accepted or
rejected as the end of Item 1A.

When a section runs to the end of the document, the terminator validation
rejected every real heading. Guessing why produces a fix that works on one
company and breaks another. This prints the evidence instead.

Usage:
    python debug_terminator.py WSBC 2023
    python debug_terminator.py WSBC 2023 --window 60
"""

from __future__ import annotations

import argparse
import gzip
import json

from config import RAW_DIR
from extract_item1a import (
    ITEM_1A, TERMINATORS, TERMINATOR_TITLES, judge_terminator, to_blocks,
)

MANIFEST = RAW_DIR / "manifest.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("fy", type=int)
    ap.add_argument("--window", type=int, default=45,
                    help="chars of following text to show")
    args = ap.parse_args()

    rec = None
    with MANIFEST.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (r.get("ticker", "").upper() == args.ticker.upper()
                    and r.get("fiscal_year") == args.fy):
                rec = r
                break
    if rec is None:
        raise SystemExit(f"No filing for {args.ticker} FY{args.fy}")

    raw = gzip.decompress((RAW_DIR / rec["stored_path"]).read_bytes())
    text, _ = to_blocks(raw)

    print("=" * 78)
    print(f"  {rec['name']}  FY{args.fy}")
    print(f"  {rec['url']}")
    print(f"  {len(text):,} chars of text")
    print("=" * 78)

    # Where does Item 1A most plausibly start? Use the deepest early match so
    # the terminator listing begins after the table of contents.
    starts = [m.start() for m in ITEM_1A.finditer(text)]
    after = starts[0] if starts else 0
    print(f"\n  {len(starts)} 'Item 1A' matches; scanning terminators from char {after:,}\n")

    for pat in TERMINATORS:
        title = TERMINATOR_TITLES.get(pat.pattern)
        print("-" * 78)
        print(f"  PATTERN {pat.pattern}")
        print("-" * 78)
        hits = list(pat.finditer(text, after))
        if not hits:
            print("    no matches at all")
            continue
        for i, m in enumerate(hits[:8], 1):
            before = text[max(0, m.start() - 80): m.start()]
            following = text[m.end(): m.end() + args.window]

            # Same function the extractor uses. Never reimplement it here.
            accepted, reasons = judge_terminator(text, m, pat)
            verdict = "ACCEPTED" if accepted else "rejected"

            print(f"\n  {i}. @{100*m.start()/len(text):.1f}%  char {m.start():,}   "
                  f"[{verdict}]{'  ' + '; '.join(reasons) if reasons else ''}")
            print(f"      before: ...{before[-60:]!r}")
            print(f"      match : {text[m.start():m.end()]!r}")
            print(f"      after : {following!r}")
            if title and "not followed by expected title" in reasons:
                print(f"      wanted: {title.pattern}  (compared with spaces removed)")

    print("\n" + "-" * 78)
    print("  If every real heading says 'rejected', the title patterns in")
    print("  TERMINATOR_TITLES do not match how this filer writes them. Look at")
    print("  the 'after:' strings above -- that is the ground truth.")
    print("-" * 78)


if __name__ == "__main__":
    main()
