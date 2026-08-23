"""
Keyboard labeller for the gold set. One risk factor on screen, one keypress
per field, auto-advance, auto-save.

WHY THIS EXISTS

Labelling 300 items in a spreadsheet means scrolling, clicking cells, typing
words, and mistyping them. This shows one risk at a time and takes single
keypresses, which is roughly four times faster and removes the invalid-value
problem entirely -- you cannot type "credti" here.

It writes back to the SAME gold_set.csv, after every single item. Close the
window whenever you like; nothing is lost and you resume where you stopped.

WHAT IT DOES NOT DO

Suggest labels. There is no default, no pre-fill, no "most likely" hint. The
gold set is the ground truth an LLM is measured against, so it has to be formed
independently -- a suggested answer you accept is not an independent judgement,
and anchoring is real even when you know it is happening.

Usage:
    python label_gold_set.py
    python label_gold_set.py --review        # step through what you labelled
    python label_gold_set.py --file ../data/gold/gold_relabel.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import textwrap

from config import DATA

DEFAULT_FILE = DATA / "gold" / "gold_set.csv"

# FOUR categories, not eight.
#
# With 300 gold-set items, eight categories yields roughly 37 items each and
# fewer than ten in the smallest -- too few to support a defensible per-category
# precision or recall figure. Eight also multiplies the chances of annotator
# drift on umbrella risks: the very common "a bad economy may hurt us" risk can
# plausibly land in credit, market, strategic or other, and a single annotator
# labelling 300 items will not resolve it the same way every time.
#
# Four categories give ~75 items each and far fewer places to drift. Coarser
# labels applied consistently are worth more than finer labels applied unevenly,
# because self-agreement is the ceiling on how accurate any model can look.
#
# Nothing is lost for the headline analysis: sub-type questions ("which banks
# newly disclosed a deposit-concentration risk in FY2023?") are answered by
# searching risk HEADING TEXT, which is retained in full, not by the category.
CATEGORY_KEYS = {
    "f": "financial",     # money: bad loans, funding, rates, securities values
    "o": "operational",   # things breaking: cyber, systems, vendors, people, fraud
    "r": "regulatory",    # rules and courts: laws, supervisors, capital, litigation
    "s": "strategic",     # the plan failing: competition, M&A, reputation, tech
}
MATERIALISED_KEYS = {"1": "speculative", "2": "materialised"}
SPECIFICITY_KEYS = {"1": "specific", "2": "generic"}
SPLIT_KEYS = {"y": "y", "n": "n"}


def getkey() -> str:
    """One keypress, no Enter. Falls back to line input where unavailable."""
    try:
        import msvcrt                                    # Windows
        ch = msvcrt.getch()
        try:
            return ch.decode("utf-8", errors="ignore").lower()
        except Exception:                                # noqa: BLE001
            return ""
    except ImportError:
        pass
    try:
        import termios, tty                              # macOS / Linux
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1).lower()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:                                    # noqa: BLE001
        line = sys.stdin.readline()
        return line[:1].lower() if line else "q"


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def wrap(text: str, width: int, limit: int) -> list[str]:
    lines = textwrap.wrap(text[:limit], width=width) or [""]
    if len(text) > limit:
        lines[-1] += " ..."
    return lines


def save(path, rows, fields) -> None:
    """
    Write after every item. A crash or a closed window must cost nothing.

    Writes to a temp file then replaces, so a crash mid-write cannot leave a
    half-written worksheet. On Windows the replace fails with PermissionError
    if the CSV is open in Excel -- Excel holds an exclusive lock. Rather than
    crash and lose the current item, wait and retry, then ask the user to close
    it. Labels already on disk are never at risk; only the newest item is.
    """
    import time
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    for attempt in range(30):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 0:
                print("\n  Cannot save -- the CSV is open in another program.")
                print(f"  {path}")
                print("  Close Excel. Retrying automatically; nothing is lost.")
            time.sleep(2)
    print("\n  Still locked. Your labels are safe in:")
    print(f"    {tmp}")
    print("  Close Excel, then rename that file to gold_set.csv.")


def ask(prompt: str, keymap: dict, extra: dict | None = None) -> str | None:
    """Return a value, or a control string, or None if the key was invalid."""
    while True:
        k = getkey()
        if k in keymap:
            return keymap[k]
        if extra and k in extra:
            return extra[k]
        if k in ("\x03", "\x04"):
            return "__QUIT__"


def header(done: int, total: int, row: dict, width: int) -> None:
    clear()
    pct = 100 * done / max(total, 1)
    bar = "#" * int(30 * done / max(total, 1))
    print("=" * width)
    print(f"  {done}/{total}  [{bar:<30}] {pct:.0f}%")
    print(f"  {row['ticker']} FY{row['fiscal_year']}"
          f"{'   [company retained with a caveat]' if row.get('flagged') else ''}")
    print("=" * width)
    if row.get("filer_category"):
        print(f"\n  filer's own grouping: {row['filer_category'][:70]}")
    print("\n  HEADING")
    for ln in wrap(row["heading"], width - 6, 400):
        print(f"    {ln}")
    print("\n  BODY")
    for ln in wrap(row["body"], width - 6, 1100):
        print(f"    {ln}")
    print()


def label_extra_fields(rows, i, row, done, total, width) -> bool:
    """
    materialised + specificity, only in --full mode.

    Returns False to quit. On undo it blanks the fields set so far and returns
    True, so the caller's `if not row["category"]: continue` re-presents the item.
    """
    header(done, total, row, width)
    print(f"  category: {row['category']}")
    print("\n  HAPPENED?   past tense and concrete events mean it materialised")
    print("    1 speculative  (could happen)      2 materialised  (has happened)")
    print("\n    u undo this item   q save and quit")
    v = ask("", MATERIALISED_KEYS, {"u": "__UNDO__", "q": "__QUIT__"})
    if v == "__QUIT__":
        return False
    if v == "__UNDO__":
        row["category"] = ""
        return True
    row["materialised"] = v

    header(done, total, row, width)
    print(f"  category: {row['category']}   |   {row['materialised']}")
    print("\n  SPECIFIC?   cover the bank's name -- could you still tell who wrote it?")
    print("    1 specific  (names a real event, place, regulator, number)")
    print("    2 generic   (any bank could have written this)")
    print("\n    u undo this item   q save and quit")
    v = ask("", SPECIFICITY_KEYS, {"u": "__UNDO__", "q": "__QUIT__"})
    if v == "__QUIT__":
        return False
    if v == "__UNDO__":
        row["category"] = row["materialised"] = ""
        return True
    row["specificity"] = v

    header(done, total, row, width)
    print(f"  category: {row['category']}   |   {row['materialised']}"
          f"   |   {row['specificity']}")
    print("\n  SPLIT CORRECTLY?   one whole risk, heading matching body?")
    print("    y yes      n no  (fragment, truncated, or two risks merged)")
    print("\n    u undo this item   q save and quit")
    v = ask("", SPLIT_KEYS, {"u": "__UNDO__", "q": "__QUIT__"})
    if v == "__QUIT__":
        return False
    if v == "__UNDO__":
        row["category"] = row["materialised"] = row["specificity"] = ""
        return True
    row["split_ok"] = v
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="also label materialised and specificity (4 fields "
                         "instead of 2)")
    args = ap.parse_args()

    from pathlib import Path
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"{path} not found. Run build_gold_set.py --draw first.")

    with path.open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        rows = list(reader)

    width = min(shutil.get_terminal_size((100, 30)).columns, 110)
    total = len(rows)
    history: list[int] = []

    while True:
        todo = [i for i, r in enumerate(rows) if not r["category"].strip()]
        done = total - len(todo)
        if not todo:
            clear()
            print("=" * width)
            print(f"  ALL {total} ITEMS LABELLED")
            print("=" * width)
            print(f"\n  saved to {path}")
            print("\n  Next:  python build_gold_set.py --status")
            print("  In a week:  python build_gold_set.py --relabel 50")
            return

        i = todo[0]
        row = rows[i]

        # ---------------------------------------------------------- category
        header(done, total, row, width)
        print("  CATEGORY   what is this risk FUNDAMENTALLY about?")
        print()
        print("    f  FINANCIAL     money      bad loans, deposits leaving,")
        print("                                interest rates, investments falling")
        print("    o  OPERATIONAL   breaking   hackers, systems, suppliers,")
        print("                                staff, fraud")
        print("    r  REGULATORY    rules      laws, regulators, lawsuits,")
        print("                                capital requirements, fines")
        print("    s  STRATEGIC     the plan   competitors, acquisitions,")
        print("                                reputation, falling behind")
        print("\n    u undo last    q save and quit")
        v = ask("", CATEGORY_KEYS, {"u": "__UNDO__", "q": "__QUIT__", "\x03": "__QUIT__"})
        if v == "__QUIT__":
            break
        if v == "__UNDO__":
            if history:
                j = history.pop()
                for f in ("category", "materialised", "specificity", "split_ok", "notes"):
                    rows[j][f] = ""
                save(path, rows, fields)
            continue
        row["category"] = v

        # Two-field mode is the default. Four judgements per item across 300
        # items degrades consistency, and consistency is the entire point of a
        # gold set -- a tired annotator's labels are worth less than fewer
        # careful ones. Category carries most of the analytical weight;
        # split_ok is nearly free because you are reading the item anyway.
        # --full restores materialised and specificity.
        if not args.full:
            header(done, total, row, width)
            print(f"  category: {row['category']}")
            print("\n  SPLIT CORRECTLY?   one whole risk, heading matching body?")
            print("    y yes      n no  (fragment, truncated, or two risks merged)")
            print("\n    u undo this item   q save and quit")
            v = ask("", SPLIT_KEYS, {"u": "__UNDO__", "q": "__QUIT__"})
            if v == "__QUIT__":
                break
            if v == "__UNDO__":
                row["category"] = ""
                continue
            row["split_ok"] = v
        else:
            if not label_extra_fields(rows, i, row, done, total, width):
                break
            if not row["category"]:
                continue

        # ------------------------------------------------------------- notes
        header(done, total, row, width)
        summary = f"  category: {row['category']}"
        if row.get("materialised"):
            summary += f"   |   {row['materialised']}   |   {row['specificity']}"
        summary += f"   |   split_ok: {row['split_ok']}"
        print(summary)
        print("\n  NOTE?   only if something made you hesitate -- these are the")
        print("          hard cases, and they matter more than the easy ones.")
        print("\n    n add a note      any other key to continue")
        k = getkey()
        if k == "n":
            print("\n  note (Enter alone to skip): ", end="", flush=True)
            try:
                note = sys.stdin.readline().strip()
            except Exception:                             # noqa: BLE001
                note = ""
            row["notes"] = "" if note.lower() in ("u", "q") else note

        history.append(i)
        save(path, rows, fields)

        if (done + 1) % 50 == 0:
            clear()
            print("=" * width)
            print(f"  {done + 1} DONE. TAKE A BREAK.")
            print("=" * width)
            print("\n  Tired labelling is inconsistent labelling, and your")
            print("  consistency is a number you will have to publish.")
            print("\n  Progress is saved. Close the window if you want.")
            print("\n  Press any key to carry on.")
            getkey()
        continue

    save(path, rows, fields)
    done = sum(1 for r in rows if r["category"].strip())
    clear()
    print(f"  Saved. {done}/{total} labelled.")
    print(f"  {path}")
    print("\n  Run this again to carry on where you stopped.")


if __name__ == "__main__":
    main()
