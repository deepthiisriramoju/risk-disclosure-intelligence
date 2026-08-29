"""
Draw a relabel sample properly, and score self-agreement.

WHY THIS EXISTS

build_gold_set.py --relabel had a bug. Its worksheet writer built blank label
columns and then merged the source row over the top:

    w.writerow({**{k: "" for k in FIELDS[:6]}, **r})

That is fine for --draw, where the source rows carry no labels. It fails
silently for --relabel, where the source IS the labelled worksheet: the
existing labels won over the blanks, the labelling tool saw every row as
already done, and the agreement check compared the file against itself and
reported 100%.

A measurement that cannot come out below 100% is not a measurement.

This version blanks the labels explicitly, verifies the blanking before writing
anything, and refuses to score a file whose labels are identical to the
original on every single row — which is what the bug looked like.

Usage:
    python relabel.py --draw 50
    ... label with: python label_gold_set.py --file ../data/gold/gold_relabel.csv
    python relabel.py --score
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import Counter
from pathlib import Path

from config import DATA

GOLD = DATA / "gold" / "gold_set.csv"
RELABEL = DATA / "gold" / "gold_relabel.csv"
SEED = 20260731
LABEL_FIELDS = ("category", "materialised", "specificity",
                "entities", "split_ok", "notes")


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def cmd_draw(n: int) -> None:
    if not GOLD.exists():
        raise SystemExit(f"{GOLD} not found.")
    rows = read(GOLD)
    done = [r for r in rows if r.get("category", "").strip()]
    if len(done) < n:
        raise SystemExit(f"Only {len(done)} labelled items; need {n}.")

    if RELABEL.exists():
        existing = [r for r in read(RELABEL) if r.get("category", "").strip()]
        if existing:
            raise SystemExit(
                f"{RELABEL} already has {len(existing)} labels — refusing to "
                "overwrite work.\nDelete or rename it to draw a fresh sample.")

    picked = random.Random(SEED).sample(done, n)
    fields = list(rows[0].keys())

    out = []
    for r in picked:
        row = {k: r.get(k, "") for k in fields}
        for k in LABEL_FIELDS:          # blanked LAST, explicitly
            if k in row:
                row[k] = ""
        out.append(row)

    # Verify before writing. The previous bug was silent; this one cannot be.
    still = [r for r in out if r.get("category", "").strip()]
    if still:
        raise SystemExit(f"BUG: {len(still)} rows still carry labels. Aborting.")

    with RELABEL.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    check = [r for r in read(RELABEL) if r.get("category", "").strip()]
    print("=" * 70)
    print(f"  Drew {n} already-labelled items for a second pass")
    print("=" * 70)
    print(f"  labels blanked and verified: {len(check)} rows still labelled "
          f"(must be 0)")
    print(f"  worksheet -> {RELABEL}")
    print("\n  Label them WITHOUT looking at gold_set.csv:")
    print(f"    python label_gold_set.py --file {RELABEL}")
    print("\n  Then:  python relabel.py --score")


def cmd_score() -> None:
    if not RELABEL.exists():
        raise SystemExit("No relabel worksheet. Run --draw 50 first.")
    first = {r["risk_id"]: r for r in read(GOLD)}
    second = [r for r in read(RELABEL) if r.get("category", "").strip()]
    if not second:
        raise SystemExit("The relabel worksheet has no labels yet.")

    print("=" * 70)
    print(f"  SELF-AGREEMENT   n = {len(second)}")
    print("=" * 70)

    perfect = True
    for field in ("category", "split_ok"):
        pairs = [(first[r["risk_id"]].get(field, "").strip().lower(),
                  r.get(field, "").strip().lower())
                 for r in second
                 if r["risk_id"] in first and first[r["risk_id"]].get(field, "").strip()]
        if not pairs:
            continue
        same = sum(a == b for a, b in pairs)
        pct = 100 * same / len(pairs)
        if same != len(pairs):
            perfect = False
        print(f"\n  {field:<12} {same}/{len(pairs)} = {pct:.1f}%")
        for (a, b), c in Counter((a, b) for a, b in pairs if a != b).most_common(6):
            print(f"      first said '{a}', second said '{b}'   ({c}x)")

    if perfect:
        print("\n  " + "!" * 66)
        print("  EVERY label matched on every field. Verify this is real.")
        print("  Perfect self-agreement across 50 judgement calls is unusual,")
        print("  and it is exactly what the earlier bug produced. Confirm the")
        print("  labelling tool actually presented each item and asked for a")
        print("  keypress before reporting this figure.")
        print("  " + "!" * 66)

    print("\n  This is a ceiling on how accurate any model can look against")
    print("  this gold set. Report it beside every precision and recall figure.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draw", type=int, metavar="N")
    ap.add_argument("--score", action="store_true")
    a = ap.parse_args()
    if a.draw:
        cmd_draw(a.draw)
    elif a.score:
        cmd_score()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
