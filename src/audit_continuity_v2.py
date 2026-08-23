"""
Data-quality check: does every company in the universe have a continuous
corporate identity across the study window?

VERSION 2. Version 1 flagged 36 of 55 companies and was therefore useless --
a signal that fires on two-thirds of the population carries no information.
Three things were wrong with it, and the fixes are the whole point of this file.

  1. NO DATE FILTER. EDGAR's formerNames field is the complete history of every
     name a registrant has ever held. M&T Bank was First Empire State Corp in
     the 1990s; Ally was GMAC until 2010. Neither has any bearing on a
     FY2021-FY2025 study. Now only changes dated inside the window count.

  2. NO NAME NORMALISATION. EDGAR rewrites the registrant record for reasons
     that are not name changes -- punctuation, a state suffix, a corporate
     form. Version 1 read all of these as renames:
         HUNTINGTON BANCSHARES INC /MD/  ->  HUNTINGTON BANCSHARES INC /MD/
         CULLEN/FROST BANKERS, INC.      ->  CULLEN FROST BANKERS INC
     Now names are normalised before comparison, so cosmetic edits are silent.

  3. WRONG FIELD FOR SHORT HISTORY. Version 1 compared the earliest filing of
     ANY type against the window start. A bank that IPO'd in early 2021 filed
     an S-1 in 2020 and passed -- while still having FY2021 as its first-ever
     10-K. That is precisely the case worth catching: a first 10-K has no prior
     year to carry forward, so counsel writes Item 1A from scratch and it comes
     out long. The following year they trim it, and year-over-year matching
     reads the trimming as a wave of DROPPED risks that never existed. Now the
     check uses the earliest 10-K.

The general lesson, which belongs in EVALUATION.md: a check that fires on
everything and a check that fires on nothing are equally worthless. Calibrate
against a population you have already reasoned about before trusting output.

Usage:
    python audit_continuity.py
    python audit_continuity.py --verbose        # show every company
    python audit_continuity.py --show-ignored   # show suppressed historical renames
"""

from __future__ import annotations

import argparse
import csv
import re

from config import SUBMISSIONS_URL, TARGET_FISCAL_YEARS, UNIVERSE_DIR
from sec_client import SECClient

WINDOW_START = f"{min(TARGET_FISCAL_YEARS)}-01-01"

# Entities created to effect a transaction. A match here is never cosmetic.
SHELL_MARKERS = ("newco", "merger sub", "mergersub", "acquisition corp", "holdco")

# Stripped before comparing two names. Order matters: longest first.
SUFFIXES = (
    "NATIONAL ASSOCIATION", "INCORPORATED", "CORPORATION", "COMPANY",
    "HOLDINGS", "HOLDING", "CORP", "INC", "LLC", "LTD", "CO", "NA", "ET AL",
)


def normalise(name: str) -> str:
    """
    Reduce a registrant name to something comparable.

    EDGAR names carry noise that has nothing to do with the underlying entity:
    a state suffix (/MD/, /NEW/), punctuation, and a corporate form that gets
    written five different ways. Strip all of it, then compare.
    """
    n = name.upper()
    # State/qualifier suffixes only ever appear at the END: "INC /MD/",
    # "KEYCORP/NEW", "FNB CORP/PA". Anchoring matters -- an unanchored pattern
    # eats the slash inside names like CULLEN/FROST BANKERS.
    while True:
        stripped = re.sub(r"/[A-Z]{2,4}/?\s*$", "", n).strip()
        if stripped == n:
            break
        n = stripped
    n = re.sub(r"[^A-Z0-9 ]", " ", n)          # remaining punctuation
    n = re.sub(r"\s+", " ", n).strip()
    changed = True
    while changed:                              # peel repeatedly: "CORP INC"
        changed = False
        for suffix in SUFFIXES:
            if n.endswith(" " + suffix):
                n = n[: -len(suffix) - 1].strip()
                changed = True
    return n


def tenk_report_dates(sub: dict, client: SECClient) -> list[str]:
    """Period-end dates of every 10-K this CIK has ever filed."""
    dates: list[str] = []

    def collect(block: dict) -> None:
        forms = block.get("form", [])
        for i in range(len(forms)):
            if forms[i].upper() == "10-K" and block["reportDate"][i]:
                dates.append(block["reportDate"][i])

    collect(sub["filings"]["recent"])
    for extra in sub["filings"].get("files", []):
        payload = client.get_json(f"https://data.sec.gov/submissions/{extra['name']}")
        if payload:
            collect(payload)
    return sorted(dates)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--show-ignored", action="store_true")
    args = parser.parse_args()

    path = UNIVERSE_DIR / "universe.csv"
    if not path.exists():
        raise SystemExit("universe.csv not found. Run build_universe.py first.")

    with path.open(encoding="utf-8") as fh:
        companies = list(csv.DictReader(fh))

    client = SECClient()
    flagged: list[dict] = []
    ignored: list[str] = []
    clean = 0

    print(f"Auditing {len(companies)} companies. Window starts {WINDOW_START}.\n")

    for row in companies:
        cik = int(row["cik"])
        sub = client.get_json(SUBMISSIONS_URL.format(cik=cik))
        if sub is None:
            flagged.append({"cik": cik, "name": row["name"],
                            "signals": ["no_submissions_json"], "detail": ""})
            continue

        current = sub.get("name", row["name"])
        signals: list[str] = []
        details: list[str] = []

        # -------------------------------------------------- name changes
        for former in sub.get("formerNames") or []:
            old = former.get("name", "")
            to_date = (former.get("to") or "")[:10]

            if normalise(old) == normalise(current):
                ignored.append(f"{current}: cosmetic edit ({old})")
                continue
            if not to_date or to_date < WINDOW_START:
                ignored.append(f"{current}: renamed {to_date or '?'} (was {old})")
                continue

            if any(m in old.lower() for m in SHELL_MARKERS):
                signals.append("SHELL_NAME")
            else:
                signals.append("renamed_in_window")
            details.append(f"was '{old}' until {to_date}")

        # -------------------------------------------------- first 10-K ever
        dates = tenk_report_dates(sub, client)
        if not dates:
            signals.append("no_10k_found")
        else:
            first_fy = int(dates[0][:4])
            if first_fy >= min(TARGET_FISCAL_YEARS):
                signals.append("first_10k_in_window")
                details.append(f"first 10-K ever covers period ending {dates[0]}")

        if signals:
            flagged.append({"cik": cik, "name": current,
                            "signals": sorted(set(signals)),
                            "detail": " | ".join(details)})
        else:
            clean += 1
            if args.verbose:
                since = dates[0][:4] if dates else "?"
                print(f"  ok    {row['ticker']:<6} {current[:46]:<46} 10-Ks since {since}")

    # ------------------------------------------------------------- report
    print()
    print("=" * 78)
    print(f"  {len(flagged)} flagged, {clean} clean, {len(ignored)} signals suppressed")
    print("=" * 78)

    if not flagged:
        print("\n  No in-window discontinuity. Every selected company kept a stable")
        print("  identity across FY2021-FY2025 and was filing 10-Ks before it began.")
    else:
        print()
        for f in sorted(flagged, key=lambda r: "SHELL_NAME" not in r["signals"]):
            marker = "!!" if "SHELL_NAME" in f["signals"] else "  "
            print(f"{marker} {f['name'][:52]}")
            print(f"     cik {f['cik']}   {', '.join(f['signals'])}")
            if f["detail"]:
                print(f"     {f['detail']}")
            print()

    if args.show_ignored and ignored:
        print("-" * 78)
        print(f"  SUPPRESSED ({len(ignored)}) -- historical or cosmetic, outside scope")
        print("-" * 78)
        for line in ignored:
            print(f"  {line}")
        print()

    print("-" * 78)
    print("  A flag is not a verdict. Investigate each with:")
    print("      python inspect_company.py <cik>")
    print()
    print("  renamed_in_window   -> read the 10-K cover page. A rebrand is fine;")
    print("                         a merger means FY2021 and FY2025 may describe")
    print("                         different institutions.")
    print("  first_10k_in_window -> no prior-year Item 1A to carry forward, so the")
    print("                         first one is written from scratch and runs long.")
    print("                         Expect a false DROPPED spike the following year.")
    print("  SHELL_NAME          -> entity created for a transaction. Not comparable.")
    print()
    print("  Record a decision per flagged company in DECISIONS.md, including the")
    print("  ones you keep. 'Kept, rebrand only' is a decision worth writing down.")
    print("-" * 78)

    out = UNIVERSE_DIR / "continuity_flags.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["cik", "name", "signals", "detail"])
        writer.writeheader()
        for f in flagged:
            writer.writerow({**f, "signals": ";".join(f["signals"])})
    print(f"\n  wrote {out}")


if __name__ == "__main__":
    main()
