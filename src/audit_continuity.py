"""
Data-quality check: does every company in the universe actually have a
continuous corporate identity across the study window?

WHY THIS EXISTS

build_universe.py identifies a company by its CIK and requires a 10-K for each
target fiscal year. That is necessary but not sufficient. A CIK can survive a
transaction that materially changes the underlying business -- a large
acquisition, a holding-company reorganisation, a spin-off. The filings keep
coming, the completeness check passes, and year-over-year comparison silently
starts comparing two different institutions.

That failure is invisible by construction: nothing errors, nothing is missing,
the numbers just mean something other than what you think. It is exactly the
class of fault the project spec calls the hardest to catch.

This script does not decide anything. It surfaces candidates for a human to
look at, which is the correct division of labour -- no automated rule can tell
you whether a merger changed a company enough to break comparability.

THREE SIGNALS

  former_names     The registrant changed name. Often cosmetic (rebranding),
                   sometimes a merger shell. "Newco", "Holdings", "Merger Sub"
                   in a former name is a strong tell.
  new_cik          CIKs are assigned sequentially. A high number on a company
                   that is supposedly decades old means the registrant is new,
                   whatever the name says.
  short_history    Earliest EDGAR filing of ANY type falls inside or after the
                   study window. A genuinely long-lived filer has 8-Ks and
                   proxies going back years before its oldest in-scope 10-K.

Usage:
    python audit_continuity.py
    python audit_continuity.py --verbose     # list every company, not just flags
"""

from __future__ import annotations

import argparse
import csv
import sys

from config import SUBMISSIONS_URL, TARGET_FISCAL_YEARS, UNIVERSE_DIR
from sec_client import SECClient

# CIKs above this were assigned recently enough to be suspicious on a company
# that claims a long history. Not a hard rule -- a prompt to look.
NEW_CIK_THRESHOLD = 1_900_000

# Words that appear in the names of entities created to effect a transaction.
SHELL_MARKERS = ("newco", "merger sub", "mergersub", "acquisition corp", "holdco")


def earliest_filing_date(sub: dict, client: SECClient) -> str:
    """Oldest filing of any type on record for this CIK."""
    dates: list[str] = []

    def collect(block: dict) -> None:
        dates.extend(d for d in block.get("filingDate", []) if d)

    collect(sub["filings"]["recent"])
    for extra in sub["filings"].get("files", []):
        payload = client.get_json(f"https://data.sec.gov/submissions/{extra['name']}")
        if payload:
            collect(payload)
    return min(dates) if dates else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    path = UNIVERSE_DIR / "universe.csv"
    if not path.exists():
        raise SystemExit("universe.csv not found. Run build_universe.py first.")

    with path.open(encoding="utf-8") as fh:
        companies = list(csv.DictReader(fh))

    client = SECClient()
    window_start = f"{min(TARGET_FISCAL_YEARS)}-01-01"

    flagged: list[dict] = []
    clean = 0

    print(f"Auditing {len(companies)} companies against window start {window_start}\n")

    for row in companies:
        cik = int(row["cik"])
        sub = client.get_json(SUBMISSIONS_URL.format(cik=cik))
        if sub is None:
            flagged.append({"cik": cik, "name": row["name"],
                            "signals": ["no_submissions_json"], "detail": ""})
            continue

        former = sub.get("formerNames") or []
        earliest = earliest_filing_date(sub, client)

        signals: list[str] = []
        details: list[str] = []

        if former:
            names = [f.get("name", "") for f in former]
            if any(m in n.lower() for n in names for m in SHELL_MARKERS):
                signals.append("SHELL_NAME")
            else:
                signals.append("former_names")
            details.append("was: " + "; ".join(names))

        if cik > NEW_CIK_THRESHOLD:
            signals.append("new_cik")

        if earliest and earliest >= window_start:
            signals.append("short_history")
            details.append(f"first EDGAR filing {earliest}")

        if signals:
            flagged.append({"cik": cik, "name": row["name"],
                            "signals": signals, "detail": " | ".join(details)})
        else:
            clean += 1
            if args.verbose:
                print(f"  ok    {row['ticker']:<6} {row['name'][:44]:<44} "
                      f"since {earliest}")

    # ---------------------------------------------------------------- report
    print()
    print("=" * 78)
    print(f"  {len(flagged)} flagged, {clean} clean")
    print("=" * 78)

    if not flagged:
        print("\n  No discontinuity signals. Every selected company has a stable")
        print("  registrant identity spanning the window.")
    else:
        print()
        for f in sorted(flagged, key=lambda r: "SHELL_NAME" not in r["signals"]):
            marker = "!!" if "SHELL_NAME" in f["signals"] else "  "
            print(f"{marker} {f['name'][:50]}")
            print(f"     cik {f['cik']}   signals: {', '.join(f['signals'])}")
            if f["detail"]:
                print(f"     {f['detail']}")
            print()

    print("-" * 78)
    print("  A flag is not a verdict. 'former_names' alone is usually a rebrand.")
    print("  Investigate anything marked SHELL_NAME, and anything carrying two or")
    print("  more signals, with:  python inspect_company.py <cik>")
    print()
    print("  What you are deciding for each: did the underlying business change")
    print("  enough between FY2021 and FY2025 that comparing its risk factors")
    print("  across those years compares two different companies? Record the")
    print("  answer per company in DECISIONS.md -- including the ones you keep.")
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
