"""
Evaluation harness. Scores any set of predictions against the hand-labelled
gold set, and compares two systems against each other.

BUILT BEFORE THE LLM RUNS, NOT AFTER

If the scoring code arrives after extraction, the sequence is: run an LLM over
10,500 items, discover the scorer has a bug, fix it, re-run everything. Building
it first also forces the prediction format to be decided before anything
produces predictions.

WHAT IT REPORTS, AND WHY EACH MATTERS

  accuracy            share correct overall. Easy to read, easy to mislead --
                      a classifier that always answers 'financial' scores 41%
                      on this data while being useless. Never report it alone.
  per-class P/R/F1    precision = when it said X, how often was it right.
                      recall = of the true Xs, how many did it find. A class
                      can have high precision and terrible recall; one number
                      hides that.
  macro F1            unweighted mean across classes, so a small class counts
                      as much as a large one. This is the honest headline for
                      an imbalanced problem.
  confusion matrix    WHICH classes get mixed up. The single most useful output
                      here: if regulatory and strategic blur together, that is
                      a taxonomy problem, not a model problem.
  Wilson interval     accuracy is an estimate from n=150. A bare percentage
                      implies a precision the sample size does not support.
  lift vs baseline    the only number that says whether the LLM earned its cost.

PREDICTION FORMAT
    CSV with columns: risk_id, category

Usage:
    python evaluate.py --pred baseline_gold.csv --name keywords
    python evaluate.py --pred llm_gold.csv --name llm --compare baseline_gold.csv
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict

from config import DATA

GOLD = DATA / "gold" / "gold_set.csv"
PRED_DIR = DATA / "predictions"
FIELD = "category"


def wilson(correct: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """
    Wilson score interval.

    Used rather than the textbook normal approximation because that one
    misbehaves at small n and near 0 or 1 -- it can produce bounds outside
    [0, 1], which is visibly wrong in a report.
    """
    if n == 0:
        return (0.0, 0.0)
    p = correct / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def load_gold() -> dict[str, str]:
    if not GOLD.exists():
        raise SystemExit(f"{GOLD} not found.")
    with GOLD.open(encoding="utf-8-sig") as fh:
        return {r["risk_id"]: r[FIELD].strip().lower()
                for r in csv.DictReader(fh) if r[FIELD].strip()}


def load_pred(path) -> dict[str, str]:
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if FIELD not in (rows[0] if rows else {}):
        raise SystemExit(f"{path} has no '{FIELD}' column.")
    return {r["risk_id"]: r[FIELD].strip().lower() for r in rows if r[FIELD].strip()}


def score(gold: dict, pred: dict) -> dict:
    """Per-class precision/recall/F1 plus overall accuracy and a confusion map."""
    ids = [i for i in gold if i in pred]
    classes = sorted(set(gold[i] for i in ids) | set(pred[i] for i in ids))

    tp = Counter(); fp = Counter(); fn = Counter()
    confusion: dict = defaultdict(Counter)
    for i in ids:
        g, p = gold[i], pred[i]
        confusion[g][p] += 1
        if g == p:
            tp[g] += 1
        else:
            fp[p] += 1
            fn[g] += 1

    per_class = {}
    for c in classes:
        prec = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else 0.0
        rec = tp[c] / (tp[c] + fn[c]) if tp[c] + fn[c] else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[c] = {"precision": prec, "recall": rec, "f1": f1,
                        "support": tp[c] + fn[c]}

    correct = sum(tp.values())
    return {
        "n": len(ids), "correct": correct,
        "accuracy": correct / len(ids) if ids else 0.0,
        "macro_f1": (sum(v["f1"] for v in per_class.values()) / len(per_class)
                     if per_class else 0.0),
        "per_class": per_class, "confusion": confusion, "classes": classes,
        "ids": ids,
    }


def majority_baseline(gold: dict, ids: list[str]) -> float:
    """Always answer the commonest class. The floor any system must clear."""
    if not ids:
        return 0.0
    top = Counter(gold[i] for i in ids).most_common(1)[0][1]
    return top / len(ids)


def report(name: str, res: dict, gold: dict) -> None:
    lo, hi = wilson(res["correct"], res["n"])
    print("=" * 74)
    print(f"  {name.upper()}   n = {res['n']}")
    print("=" * 74)
    print(f"  accuracy   {res['accuracy']:.3f}   "
          f"95% CI {lo:.3f} - {hi:.3f}")
    print(f"  macro F1   {res['macro_f1']:.3f}   "
          "<- the headline for an imbalanced problem")

    maj = majority_baseline(gold, res["ids"])
    print(f"\n  always-predict-commonest-class would score {maj:.3f}.")
    if res["accuracy"] <= maj:
        print("  THIS SYSTEM DOES NOT BEAT THAT. It has learned nothing useful.")
    else:
        print(f"  This system is +{100*(res['accuracy']-maj):.1f} points above it.")

    print("\n  " + "-" * 70)
    print(f"  {'class':<14} {'prec':>7} {'recall':>8} {'F1':>7} {'support':>9}")
    print("  " + "-" * 70)
    for c in res["classes"]:
        v = res["per_class"][c]
        print(f"  {c:<14} {v['precision']:>7.3f} {v['recall']:>8.3f} "
              f"{v['f1']:>7.3f} {v['support']:>9}")

    print("\n  CONFUSION MATRIX   rows = truth, columns = prediction")
    print("  " + " " * 14 + "".join(f"{c[:9]:>11}" for c in res["classes"]))
    for g in res["classes"]:
        row = "".join(f"{res['confusion'][g][p]:>11}" for p in res["classes"])
        print(f"  {g:<14}{row}")

    worst = None
    for g in res["classes"]:
        for p in res["classes"]:
            if g != p and (worst is None or res["confusion"][g][p] > worst[2]):
                worst = (g, p, res["confusion"][g][p])
    if worst and worst[2] > 0:
        print(f"\n  Commonest error: true '{worst[0]}' predicted as "
              f"'{worst[1]}' ({worst[2]}x).")
        print("  Repeated confusion between two classes is usually a taxonomy")
        print("  problem rather than a model problem -- worth saying so.")


def compare(name_a: str, a: dict, name_b: str, b: dict) -> None:
    shared = [i for i in a["ids"] if i in b["ids"]]
    if not shared:
        print("\n  No overlapping items to compare.")
        return
    print("\n" + "=" * 74)
    print(f"  {name_a.upper()}  vs  {name_b.upper()}   (n = {len(shared)})")
    print("=" * 74)
    da = 100 * (a["accuracy"] - b["accuracy"])
    df = 100 * (a["macro_f1"] - b["macro_f1"])
    print(f"  accuracy   {a['accuracy']:.3f}  vs  {b['accuracy']:.3f}   "
          f"lift {da:+.1f} points")
    print(f"  macro F1   {a['macro_f1']:.3f}  vs  {b['macro_f1']:.3f}   "
          f"lift {df:+.1f} points")

    print(f"\n  {'class':<14} {'F1 ' + name_a[:8]:>13} {'F1 ' + name_b[:8]:>13} {'lift':>8}")
    print("  " + "-" * 52)
    for c in sorted(set(a["classes"]) | set(b["classes"])):
        fa = a["per_class"].get(c, {}).get("f1", 0.0)
        fb = b["per_class"].get(c, {}).get("f1", 0.0)
        mark = "   <- baseline wins" if fb > fa else ""
        print(f"  {c:<14} {fa:>13.3f} {fb:>13.3f} {100*(fa-fb):>+7.1f}{mark}")

    print("\n  Any class where the baseline wins should use the baseline, and the")
    print("  README should say so. Reporting that a simpler method beat the LLM")
    print("  on a field is more convincing than a uniformly favourable table.")

    if abs(da) < 5:
        print(f"\n  NOTE: a {abs(da):.1f}-point difference at n={len(shared)} is")
        print("  inside the confidence intervals. Do not claim a winner.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True)
    ap.add_argument("--name", default="system")
    ap.add_argument("--compare")
    ap.add_argument("--compare-name", default="baseline")
    args = ap.parse_args()

    from pathlib import Path
    gold = load_gold()
    if not gold:
        raise SystemExit("Gold set has no labels yet.")
    print(f"Gold set: {len(gold)} labelled items\n")

    def resolve(p):
        q = Path(p)
        return q if q.exists() else PRED_DIR / p

    a = score(gold, load_pred(resolve(args.pred)))
    report(args.name, a, gold)

    if args.compare:
        b = score(gold, load_pred(resolve(args.compare)))
        report(args.compare_name, b, gold)
        compare(args.name, a, args.compare_name, b)


if __name__ == "__main__":
    main()
