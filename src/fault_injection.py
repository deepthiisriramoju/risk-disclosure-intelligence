"""
Fault injection: break the pipeline on purpose and count what the checks catch.

WHY

Every quality check in this project was written in response to a problem that
had already happened. That is the wrong direction of evidence. A check that has
never been tested against a fault it was not written for is a hope, not a
control.

So faults are introduced deliberately, one at a time, into a COPY of the data,
and each is scored on whether anything noticed. The output is a detection rate
and, more usefully, a list of the faults that pass through silently.

THE FAULTS THAT MATTER MOST

Not the ones that crash. A crash announces itself. The dangerous fault is the
one that leaves the pipeline reporting success on wrong data -- a truncated
filing that still parses, an empty API response counted as a classification, a
duplicated row inflating a count. Those are what this harness is built around.

WHAT COUNTS AS DETECTION

A fault is detected if it raises an error, is quarantined with a reason, fails
an integrity check, or moves a reported metric far enough to be visible. A fault
that only changes a number nobody looks at is NOT detected -- that is the whole
point.

Nothing here touches the real data. Every fault is applied to a temporary copy
and discarded afterwards.

Usage:
    python fault_injection.py
    python fault_injection.py --fault truncate_filing
    python fault_injection.py --list
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import tempfile
from pathlib import Path

from config import DATA

RISK_DIR = DATA / "interim" / "risk_factors"
MATCHES = DATA / "interim" / "yoy_matches.csv"
BASELINE = DATA / "predictions" / "baseline_all.csv"

MIN_BODY_CHARS = 200


# ---------------------------------------------------------------- the faults
# Each returns (description, expected_detector). The expected detector is
# recorded so that a fault caught by SOMETHING ELSE is visible -- being caught
# by luck is different from being caught by design.

def f_truncate_filing(work: Path) -> tuple[str, str]:
    """Half a filing's risk factors disappear. Nothing errors."""
    target = sorted(work.glob("risk_factors/*.json"))[0]
    d = json.loads(target.read_text(encoding="utf-8"))
    keep = len(d["risks"]) // 2
    d["risks"] = d["risks"][:keep]
    d["n_risks"] = keep
    target.write_text(json.dumps(d), encoding="utf-8")
    return (f"{target.stem}: risk factors truncated to {keep}",
            "risk-count distribution / year-over-year DROPPED spike")


def f_empty_bodies(work: Path) -> tuple[str, str]:
    """Bodies blanked. Headings survive, so a heading-only check passes."""
    target = sorted(work.glob("risk_factors/*.json"))[1]
    d = json.loads(target.read_text(encoding="utf-8"))
    for r in d["risks"][:10]:
        r["body"] = ""
        r["chars"] = len(r["heading"])
    target.write_text(json.dumps(d), encoding="utf-8")
    return (f"{target.stem}: 10 bodies emptied", "warehouse quarantine: short_body")


def f_duplicate_rows(work: Path) -> tuple[str, str]:
    """A filing's risk factors duplicated. Counts inflate; nothing fails."""
    target = sorted(work.glob("risk_factors/*.json"))[2]
    d = json.loads(target.read_text(encoding="utf-8"))
    d["risks"] = d["risks"] + d["risks"][:5]
    d["n_risks"] = len(d["risks"])
    target.write_text(json.dumps(d), encoding="utf-8")
    return (f"{target.stem}: 5 risk factors duplicated",
            "warehouse quarantine: duplicate_heading_in_filing")


def f_corrupt_json(work: Path) -> tuple[str, str]:
    """A malformed file. The loud fault, included as a control."""
    target = sorted(work.glob("risk_factors/*.json"))[3]
    target.write_text('{"cik": 123, "risks": [{"heading": "broken"', encoding="utf-8")
    return (f"{target.stem}: truncated JSON", "load error / row-count drop")


def f_empty_classification(work: Path) -> tuple[str, str]:
    """
    An API returns empty categories that are still written as rows.

    The dangerous shape: the pipeline reports 100% coverage, every row has a
    record, and the records say nothing.
    """
    path = work / "baseline_all.csv"
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows[:500]:
        r["category"] = ""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return ("500 classifications blanked but rows retained",
            "warehouse: missing category count")


def f_shifted_labels(work: Path) -> tuple[str, str]:
    """
    Every classification shifted by one row.

    Plausible output, correct row count, correct category distribution --
    and every single label attached to the wrong risk. A count-based check
    cannot see this at all.
    """
    path = work / "baseline_all.csv"
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    cats = [r["category"] for r in rows]
    for r, c in zip(rows, cats[1:] + cats[:1]):
        r["category"] = c
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return ("all classifications shifted by one row",
            "gold-set accuracy collapse -- nothing else")


def f_broken_match_ids(work: Path) -> tuple[str, str]:
    """Match rows pointing at risk ids that do not exist."""
    path = work / "yoy_matches.csv"
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    n = 0
    for r in rows:
        if r["prev_id"] and n < 50:
            r["prev_id"] = r["prev_id"] + "_GHOST"
            n += 1
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return (f"{n} match rows point at non-existent prior risks",
            "warehouse integrity: orphan prior_risk_id")


def f_wrong_fiscal_year(work: Path) -> tuple[str, str]:
    """
    One filing relabelled to the wrong year.

    Everything still parses, the row count is unchanged, and a whole year of
    one company's disclosure is attributed to the wrong period.
    """
    target = sorted(work.glob("risk_factors/*.json"))[5]
    d = json.loads(target.read_text(encoding="utf-8"))
    d["fiscal_year"] = 2099
    target.write_text(json.dumps(d), encoding="utf-8")
    return (f"{target.stem}: fiscal year set to 2099",
            "fiscal-year domain check")


FAULTS = {
    "truncate_filing": f_truncate_filing,
    "empty_bodies": f_empty_bodies,
    "duplicate_rows": f_duplicate_rows,
    "corrupt_json": f_corrupt_json,
    "empty_classification": f_empty_classification,
    "shifted_labels": f_shifted_labels,
    "broken_match_ids": f_broken_match_ids,
    "wrong_fiscal_year": f_wrong_fiscal_year,
}


# ---------------------------------------------------------------- the checks
def run_checks(work: Path) -> dict:
    """
    The checks the pipeline actually performs, run against the damaged copy.

    Deliberately re-implemented here in the same terms the real checks use,
    rather than importing them: the question is whether the CHECK catches the
    fault, not whether a particular function does.
    """
    result = {"errors": [], "quarantine": {}, "metrics": {}}

    risks, seen = [], set()
    for path in sorted(work.glob("risk_factors/*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            result["errors"].append(f"unreadable {path.name}: {type(e).__name__}")
            continue
        if "ticker" not in d or "fiscal_year" not in d:
            result["errors"].append(f"missing fields in {path.name}")
            continue
        for i, r in enumerate(d.get("risks", [])):
            rid = f"{d['cik']}_FY{d['fiscal_year']}_{i:03d}"
            risks.append({"risk_id": rid, "cik": d["cik"], "ticker": d["ticker"],
                          "fiscal_year": d["fiscal_year"],
                          "heading": r.get("heading", ""),
                          "body": r.get("body", "")})

    q: dict = {}
    kept = []
    for r in risks:
        key = (r["cik"], r["fiscal_year"], r["heading"])
        if len(r["heading"].strip()) < 10:
            q["empty_heading"] = q.get("empty_heading", 0) + 1
        elif len(r["body"]) < MIN_BODY_CHARS:
            q["short_body"] = q.get("short_body", 0) + 1
        elif key in seen:
            q["duplicate_heading_in_filing"] = q.get("duplicate_heading_in_filing", 0) + 1
        else:
            seen.add(key)
            kept.append(r)
    result["quarantine"] = q

    years = {r["fiscal_year"] for r in kept}
    bad_years = {y for y in years if not (2000 <= int(y) <= 2030)}
    if bad_years:
        result["errors"].append(f"fiscal year outside plausible range: {bad_years}")

    ids = {r["risk_id"] for r in kept}
    cats = {}
    path = work / "baseline_all.csv"
    if path.exists():
        with path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                cats[row["risk_id"]] = row["category"]
    result["metrics"]["missing_category"] = sum(
        1 for r in kept if not cats.get(r["risk_id"]))

    orphans = 0
    path = work / "yoy_matches.csv"
    if path.exists():
        with path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if row["prev_id"] and row["prev_id"] not in ids:
                    orphans += 1
    result["metrics"]["orphan_prior_risk"] = orphans

    result["metrics"]["risk_factors"] = len(kept)
    per_filing = {}
    for r in kept:
        per_filing[(r["cik"], r["fiscal_year"])] = per_filing.get(
            (r["cik"], r["fiscal_year"]), 0) + 1
    counts = sorted(per_filing.values())
    result["metrics"]["min_risks_per_filing"] = counts[0] if counts else 0
    result["metrics"]["filings"] = len(per_filing)
    return result


def compare(base: dict, after: dict) -> list[str]:
    """What noticed? Returns the signals that fired."""
    signals = []
    for e in after["errors"]:
        if e not in base["errors"]:
            signals.append(f"ERROR: {e}")
    for reason, n in after["quarantine"].items():
        before = base["quarantine"].get(reason, 0)
        if n > before:
            signals.append(f"quarantine {reason}: {before} -> {n}")
    for k, v in after["metrics"].items():
        b = base["metrics"].get(k, 0)
        if b and abs(v - b) / max(b, 1) > 0.02:
            signals.append(f"metric {k}: {b:,} -> {v:,}")
        elif not b and v:
            signals.append(f"metric {k}: {b:,} -> {v:,}")
    return signals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", choices=sorted(FAULTS))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name, fn in sorted(FAULTS.items()):
            print(f"  {name:<24} {(fn.__doc__ or '').strip().splitlines()[0]}")
        return

    if not RISK_DIR.exists():
        raise SystemExit("Run split_risk_factors.py first.")

    faults = {args.fault: FAULTS[args.fault]} if args.fault else FAULTS
    tmp = Path(tempfile.mkdtemp(prefix="faultinj_"))

    try:
        clean = tmp / "clean"
        (clean / "risk_factors").mkdir(parents=True)
        for p in RISK_DIR.glob("*.json"):
            shutil.copy(p, clean / "risk_factors" / p.name)
        for src, name in ((MATCHES, "yoy_matches.csv"), (BASELINE, "baseline_all.csv")):
            if src.exists():
                shutil.copy(src, clean / name)

        print("=" * 74)
        print("  FAULT INJECTION")
        print("=" * 74)
        print("  Establishing the clean baseline...")
        base = run_checks(clean)
        print(f"    {base['metrics']['risk_factors']:,} risk factors, "
              f"{base['metrics']['filings']} filings, "
              f"{sum(base['quarantine'].values())} quarantined\n")

        caught, missed = [], []
        for name, fn in faults.items():
            work = tmp / name
            shutil.copytree(clean, work)
            desc, expected = fn(work)
            signals = compare(base, run_checks(work))

            print("-" * 74)
            print(f"  {name}")
            print(f"    injected : {desc}")
            print(f"    expected : {expected}")
            if signals:
                caught.append(name)
                print("    DETECTED:")
                for s in signals:
                    print(f"      {s}")
            else:
                missed.append(name)
                print("    *** NOT DETECTED — passed through silently ***")

        print("\n" + "=" * 74)
        print(f"  DETECTION RATE  {len(caught)}/{len(faults)} = "
              f"{100*len(caught)/len(faults):.0f}%")
        print("=" * 74)
        if missed:
            print("\n  UNDETECTED:")
            for m in missed:
                print(f"    {m}")
            print("\n  These are the faults that would reach a dashboard as")
            print("  plausible numbers. Each is either a check worth adding, or a")
            print("  limitation worth stating. Silence is not the same as safety.")
        else:
            print("\n  Every injected fault produced a visible signal.")
        print("\n  Report this rate in EVALUATION.md, with the undetected list.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
