"""
Show every "Item 1A" candidate in a filing, with its full score breakdown and
the section it would produce.

Companion to debug_terminator.py. That tool explains where a section ENDS; this
one explains where it STARTS. Two failures needed it:

  PFS FY2022  chose a candidate AFTER Item 1B, producing a 349,581-char section
              running to the end of the document, when a valid candidate at
              char 72,848 would have produced 184,060 chars.
  HBAN FY2023 found a candidate of plausible length (64,297 chars) that scored
              exactly 0, just under the > 0 threshold, so nothing was accepted.

Both are scoring problems, not matching problems. Tuning a scorer without
seeing the scores is guessing.

Usage:
    python debug_start.py PFS 2022
    python debug_start.py HBAN 2023 --context 120
"""

from __future__ import annotations

import argparse
import gzip
import json

from config import RAW_DIR
from extract_item1a import (
    ITEM_1A, MAX_SECTION_CHARS, MIN_SECTION_CHARS,
    find_terminator, score_candidate, to_blocks,
)

MANIFEST = RAW_DIR / "manifest.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("fy", type=int)
    ap.add_argument("--context", type=int, default=100)
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
    print(f"  {len(text):,} chars | accept window "
          f"{MIN_SECTION_CHARS:,}-{MAX_SECTION_CHARS:,} chars | need score > 0")
    print("=" * 78)

    matches = list(ITEM_1A.finditer(text))
    print(f"\n  {len(matches)} 'Item 1A' matches\n")

    rows = []
    for i, m in enumerate(matches, 1):
        score, why = score_candidate(text, m.start(), m.end())
        stop, stop_by = find_terminator(text, m.end())
        length = (stop - m.start()) if stop else (len(text) - m.start())
        plausible = MIN_SECTION_CHARS <= length <= MAX_SECTION_CHARS
        if plausible:
            score += 25
            why = why + ["plausible_len"]
        viable = score > 0 and plausible
        rows.append((i, m, score, why, stop, stop_by, length, viable))

    winner = max((r for r in rows if r[7]), key=lambda r: (r[2], -r[1].start()),
                 default=None)

    for i, m, score, why, stop, stop_by, length, viable in rows:
        mark = "<<< WINNER" if winner and m.start() == winner[1].start() else ""
        state = "viable" if viable else "NOT VIABLE"
        print("-" * 78)
        print(f"  {i}. @{100*m.start()/len(text):5.1f}%  char {m.start():>9,}   "
              f"score {score:>4}   {state} {mark}")
        print(f"      would yield {length:>9,} chars, ending at "
              f"{stop_by or 'NOTHING (runs to end of document)'}")
        print(f"      score parts: {', '.join(why) if why else '(none)'}")
        print(f"      before: ...{text[max(0, m.start()-70):m.start()]!r}")
        print(f"      match : {text[m.start():m.end()]!r}")
        print(f"      after : {text[m.end():m.end()+args.context]!r}")

    print("\n" + "=" * 78)
    if winner is None:
        print("  NO VIABLE CANDIDATE -- extraction fails for this filing.")
        best = max(rows, key=lambda r: r[2])
        print(f"  Best was #{best[0]} at score {best[2]}, length {best[6]:,}.")
        print("  If that candidate looks correct above, the scorer is too strict:")
        print("  the signals it rewards are absent from how this filer writes the")
        print("  heading. Read the 'after:' string -- that is the ground truth.")
    else:
        print(f"  Winner: candidate #{winner[0]} at {100*winner[1].start()/len(text):.1f}%, "
              f"{winner[6]:,} chars.")
        print("  Check the 'after:' string on the winner. If it is prose about a")
        print("  DIFFERENT topic, or the winner sits after Item 1B, the scorer")
        print("  preferred the wrong candidate and the section is wrong even")
        print("  though nothing failed.")
    print("=" * 78)


if __name__ == "__main__":
    main()
