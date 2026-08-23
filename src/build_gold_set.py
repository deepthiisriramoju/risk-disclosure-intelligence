"""
Draw and manage the 300-item hand-labelled gold set.

WHY THIS IS THE MOST IMPORTANT FILE IN THE PROJECT

Week 3 asks an LLM to classify ~10,500 risk factors. The only way to know
whether it is any good is to compare it against items a human classified. That
comparison is what makes precision and recall mean anything.

The ground truth therefore CANNOT come from an LLM. Not for convenience, not
for speed. An LLM grading an LLM measures agreement, not accuracy -- two models
sharing the same blind spot agree perfectly and are both wrong. The 300 items
below are the one part of this pipeline that has to be done by hand, and the
project's central claim rests on them.

This tool does the parts that are mechanical:

  --draw       stratified random sample, fixed seed, worksheet with the label
               columns pre-made and the rubric written into the file
  --status     how far through you are, and how balanced the labels look
  --relabel    draw 50 already-labelled items for a second pass a week later,
               to measure your own self-agreement
  --agreement  compare the two passes and report your consistency

WHAT IT WILL NOT DO

Overwrite labels. --draw refuses if a worksheet with labels already exists.
An earlier tool in this project silently destroyed a completed worksheet; that
is not repeatable here.

Usage:
    python build_gold_set.py --draw 300
    python build_gold_set.py --status
    python build_gold_set.py --relabel 50
    python build_gold_set.py --agreement
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from collections import Counter

from config import DATA, FLAGGED_CIKS

RISK_DIR = DATA / "interim" / "risk_factors"
GOLD_DIR = DATA / "gold"
WORKSHEET = GOLD_DIR / "gold_set.csv"
RELABEL = GOLD_DIR / "gold_relabel.csv"
RUBRIC = GOLD_DIR / "RUBRIC.md"
SEED = 20260730

FIELDS = [
    "category", "materialised", "specificity", "entities", "split_ok", "notes",
    "risk_id", "ticker", "fiscal_year", "risk_index", "filer_category",
    "heading", "body", "chars", "cik", "source_url", "flagged",
]

CATEGORIES = ["financial", "operational", "regulatory", "strategic"]

RUBRIC_TEXT = """# Gold set labelling rubric

300 risk factors, labelled by hand. This is the ground truth every accuracy
number in the project is measured against.

Fill in the first six columns. Leave the rest alone.

---

## category  (pick exactly one)

| Key | Value | The risk is about |
|---|---|---|
| `f` | `financial` | **money.** Borrowers not repaying; loan losses; collateral values; deposits leaving; funding costs; access to capital markets; interest rates; securities values; trading |
| `o` | `operational` | **things breaking.** Cyber attacks; system failures; vendors; staff; fraud; failed technology projects; business continuity |
| `r` | `regulatory` | **rules and courts.** Laws and legislation; supervisors and examinations; capital requirements; litigation; fines; compliance |
| `s` | `strategic` | **the plan failing.** Competition; acquisitions and integration; growth plans; falling behind on technology; reputation and public perception |

**Four categories, not eight.** With 300 items, eight categories leaves roughly
37 items each and under ten in the smallest — too few for a per-category metric
anyone should trust. Four gives ~75 each, and far fewer opportunities to drift.

**Sub-types are not lost.** Questions like "which banks newly disclosed a
deposit-concentration risk in FY2023?" are answered by searching risk *heading
text*, which is kept in full. The category label does not have to carry them.

### The rule for the commonest hard case

Roughly one risk in eight is some version of *"a bad economy may hurt us."*
It lists consequences across every category, so it can be argued into any of them.

> **Umbrella macroeconomic risks → `f` (financial).**

Write it down. Apply it every time without rereading the body. You will meet
this risk forty times or more, and flip-flopping on it will damage your
self-agreement score more than any other single thing.

### When two categories both fit

Pick what the risk is *fundamentally* about, not everything it mentions. A cyber
risk that mentions regulatory fines is `operational`. Then add a note. Those
notes are the confusion analysis — they show where the taxonomy is under strain,
and they are worth more than a tidy-looking label set.

## materialised  (`speculative` / `materialised`)

- `speculative` — framed as something that *could* happen
- `materialised` — states something that *has* happened

Look for past tense and concrete events: *"has adversely impacted us in the
past"*, *"in the third quarter of 2022, United Bank received a Needs to Improve
rating"*. If a risk says both, label `materialised` — the disclosure of an
actual event is the more informative fact.

## specificity  (`specific` / `generic`)

- `specific` — names a real event, counterparty, regulator action, place, or number
- `generic` — could appear in any bank's filing with the name swapped

Test: cover the company name. If you cannot tell which bank wrote it, `generic`.

## entities

Named organisations, regulators, laws, or places, semicolon-separated.
Example: `FDIC; Dodd-Frank; Puerto Rico`. Leave blank if none. Do not include
the filing company itself.

## split_ok  (`y` / `n`)

Is this record correctly split — one whole risk factor, heading matching body?
`n` if it is a fragment, truncated, or two risks merged.

This gives you a second, larger measurement of splitter accuracy for free,
from reading you are doing anyway.

## notes

Anything that made you hesitate. Especially valuable: a risk that resisted the
category list, or a case where two labels seemed equally right.

---

## How to work

Do it in blocks of 50 with breaks. Tired labelling is inconsistent labelling,
and your consistency is itself a number you have to report.

Do NOT look at what an LLM would say first. Anchoring destroys the independence
that makes this set worth having.

A week after finishing, run `--relabel 50` and label those again without looking
at your first answers. `--agreement` then reports how often you agreed with
yourself. Expect 80-90%. Publishing that number is what separates measurement
from decoration: a single annotator's inconsistency is a real error source, and
naming it is the honest thing to do.
"""


def load_risks() -> list[dict]:
    out = []
    for path in sorted(RISK_DIR.glob("*.json")):
        try:
            f = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  unreadable: {path.name}")
            continue
        for i, r in enumerate(f["risks"]):
            out.append({
                "risk_id": f"{f['cik']}_FY{f['fiscal_year']}_{i:03d}",
                "cik": f["cik"], "ticker": f["ticker"],
                "fiscal_year": f["fiscal_year"], "risk_index": i,
                "filer_category": r["category"] or "",
                "heading": r["heading"], "body": r["body"], "chars": r["chars"],
                "source_url": f["source_url"],
                "flagged": FLAGGED_CIKS[f["cik"]][2][:40] if f["cik"] in FLAGGED_CIKS else "",
            })
    return out


def stratified(pool: list[dict], n: int, seed: int) -> list[dict]:
    """
    Spread the sample across companies AND fiscal years.

    A simple random draw over-weights whichever company discloses the most risk
    factors -- one filer with 105 records would contribute five times as many
    items as one with 20. Sampling round-robin over (company, year) cells keeps
    the gold set representative of the panel rather than of its most verbose
    members.
    """
    rng = random.Random(seed)
    cells: dict = {}
    for r in pool:
        cells.setdefault((r["cik"], r["fiscal_year"]), []).append(r)
    for items in cells.values():
        rng.shuffle(items)

    keys = sorted(cells)
    rng.shuffle(keys)
    picked, depth = [], 0
    while len(picked) < n and depth < 400:
        progressed = False
        for k in keys:
            if depth < len(cells[k]):
                picked.append(cells[k][depth])
                progressed = True
                if len(picked) >= n:
                    break
        if not progressed:
            break
        depth += 1
    return picked[:n]


def has_labels(path) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8-sig") as fh:
        return any(row.get("category", "").strip() for row in csv.DictReader(fh))


def write_worksheet(path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**{k: "" for k in FIELDS[:6]}, **r})


def cmd_draw(n: int) -> None:
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    if has_labels(WORKSHEET):
        raise SystemExit(
            f"{WORKSHEET} already contains labels. Refusing to overwrite.\n"
            "Rename or move it first if you really want a new sample.")

    pool = load_risks()
    if not pool:
        raise SystemExit(f"No risk factors in {RISK_DIR}. Run split_risk_factors.py first.")
    rows = stratified(pool, n, SEED)
    write_worksheet(WORKSHEET, rows)
    RUBRIC.write_text(RUBRIC_TEXT, encoding="utf-8")

    cells = {(r["cik"], r["fiscal_year"]) for r in rows}
    print("=" * 74)
    print(f"  Drew {len(rows)} of {len(pool):,} risk factors")
    print("=" * 74)
    print(f"  spanning {len({r['cik'] for r in rows})} companies, "
          f"{len(cells)} company-year cells")
    print(f"  fiscal years: {Counter(r['fiscal_year'] for r in rows).most_common()}")
    flagged = sum(1 for r in rows if r["flagged"])
    if flagged:
        print(f"  {flagged} items come from companies retained with a caveat (D12)")
    print(f"\n  worksheet -> {WORKSHEET}")
    print(f"  rubric    -> {RUBRIC}")
    print("\n  Read the rubric first. Label in blocks of 50 with breaks.")
    print("  Save as CSV, not .xlsx. Check progress with --status.")


def cmd_status() -> None:
    if not WORKSHEET.exists():
        raise SystemExit("No worksheet. Run --draw 300 first.")
    with WORKSHEET.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    done = [r for r in rows if r["category"].strip()]

    print("=" * 74)
    print(f"  LABELLED {len(done)}/{len(rows)}  ({100*len(done)/max(len(rows),1):.0f}%)")
    print("=" * 74)
    if not done:
        return

    for field, valid in (("category", CATEGORIES),
                         ("materialised", ["speculative", "materialised"]),
                         ("specificity", ["specific", "generic"]),
                         ("split_ok", ["y", "n"])):
        c = Counter(r[field].strip().lower() for r in done if r[field].strip())
        print(f"\n  {field}")
        for k, v in c.most_common():
            flag = "" if k in valid else "   <-- not in the rubric"
            print(f"    {k:<15} {v:>4}  {100*v/len(done):>5.1f}%  "
                  f"{'#' * int(30*v/len(done))}{flag}")

    bad = sum(1 for r in done if r["split_ok"].strip().lower() == "n")
    if bad:
        print(f"\n  splitter errors seen so far: {bad}/{len(done)} = "
              f"{100*bad/len(done):.1f}%")
        print("  (this is a second, larger measurement of splitter accuracy)")

    notes = [r for r in done if r["notes"].strip()]
    if notes:
        print(f"\n  {len(notes)} items carry notes -- these are your hard cases.")


def cmd_relabel(n: int) -> None:
    if not WORKSHEET.exists():
        raise SystemExit("No worksheet. Run --draw 300 first.")
    if has_labels(RELABEL):
        raise SystemExit(f"{RELABEL} already contains labels. Refusing to overwrite.")
    with WORKSHEET.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    done = [r for r in rows if r["category"].strip()]
    if len(done) < n:
        raise SystemExit(f"Only {len(done)} items labelled; need {n}.")

    rng = random.Random(SEED + 1)
    picked = rng.sample(done, n)
    write_worksheet(RELABEL, picked)     # labels blanked deliberately
    print(f"  Drew {n} already-labelled items for a second pass.")
    print(f"  worksheet -> {RELABEL}")
    print("\n  Label these WITHOUT looking at your first answers. The point is to")
    print("  measure how consistent you are, and peeking destroys the measurement.")
    print("  Then run --agreement.")


def cmd_agreement() -> None:
    if not (WORKSHEET.exists() and RELABEL.exists()):
        raise SystemExit("Need both gold_set.csv and gold_relabel.csv.")
    with WORKSHEET.open(encoding="utf-8-sig") as fh:
        first = {r["risk_id"]: r for r in csv.DictReader(fh)}
    with RELABEL.open(encoding="utf-8-sig") as fh:
        second = [r for r in csv.DictReader(fh) if r["category"].strip()]
    if not second:
        raise SystemExit("Relabel worksheet has no labels yet.")

    print("=" * 74)
    print(f"  SELF-AGREEMENT  (n={len(second)})")
    print("=" * 74)
    for field in ("category", "materialised", "specificity", "split_ok"):
        pairs = [(first[r["risk_id"]][field].strip().lower(), r[field].strip().lower())
                 for r in second if r["risk_id"] in first
                 and first[r["risk_id"]][field].strip()]
        if not pairs:
            continue
        same = sum(a == b for a, b in pairs)
        print(f"\n  {field:<14} {same}/{len(pairs)} = {100*same/len(pairs):.1f}%")
        disagreements = Counter((a, b) for a, b in pairs if a != b)
        for (a, b), c in disagreements.most_common(5):
            print(f"      {a} -> {b}   ({c}x)")

    print("\n  This number is a ceiling on how accurate any model can look against")
    print("  this gold set. If you agree with yourself 85% of the time, an LLM")
    print("  scoring 85% may be as good as the ground truth allows. Report it")
    print("  alongside every precision and recall figure.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draw", type=int, metavar="N")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--relabel", type=int, metavar="N")
    ap.add_argument("--agreement", action="store_true")
    a = ap.parse_args()
    if a.draw:
        cmd_draw(a.draw)
    elif a.status:
        cmd_status()
    elif a.relabel:
        cmd_relabel(a.relabel)
    elif a.agreement:
        cmd_agreement()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
