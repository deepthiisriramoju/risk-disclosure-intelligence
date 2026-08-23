"""
Diagnostic tool. Prints everything the SEC knows about one company's 10-K history.

This is not part of the pipeline -- it exists to answer "why did this company get
excluded?" without guessing. Build one of these early in any data project. The
alternative is adding print statements to your real code and forgetting to
remove them.

Usage:
    python inspect_company.py 1234567
    python inspect_company.py 1234567 --all-forms

What to look for:
  * "period end" dates that are not 31 December  -> non-calendar fiscal year,
    so the FY label my rule assigns may disagree with what the company calls it.
  * Filings listed under "OVERFLOW BATCH" -> older history that did NOT fit in
    the API's most-recent block. If a company is missing old filings AND has no
    overflow batches, the overflow fetch is the suspect.
  * A non-empty "former names" list -> the entity was renamed or restructured.
    Combined with a short filing history, that usually means the older filings
    live under a DIFFERENT CIK and this one only exists post-reorganisation.
"""

from __future__ import annotations

import argparse
import sys

from config import SUBMISSIONS_URL, TARGET_FISCAL_YEARS, fiscal_year_from_report_date
from sec_client import SECClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cik", type=int, help="CIK number, with or without leading zeros")
    parser.add_argument("--all-forms", action="store_true",
                        help="show every form type, not just 10-K and 10-K/A")
    args = parser.parse_args()

    client = SECClient()
    sub = client.get_json(SUBMISSIONS_URL.format(cik=args.cik))
    if sub is None:
        print(f"No submissions record for CIK {args.cik}. Check the number.")
        sys.exit(1)

    # ---------------------------------------------------------------- identity
    print("=" * 78)
    print(f"  {sub.get('name', '?')}")
    print("=" * 78)
    print(f"  CIK              {args.cik}")
    print(f"  SIC              {sub.get('sic', '?')}  {sub.get('sicDescription', '')}")
    print(f"  tickers          {sub.get('tickers') or '(none - no listed common stock)'}")
    print(f"  exchanges        {sub.get('exchanges') or '(none)'}")
    print(f"  fiscal year end  {sub.get('fiscalYearEnd', '?')}   (MMDD)")
    print(f"  entity type      {sub.get('entityType', '?')}")

    former = sub.get("formerNames") or []
    if former:
        print(f"  former names     ({len(former)})")
        for f in former:
            print(f"                   {f.get('name')}  until {f.get('to', '?')[:10]}")
    else:
        print("  former names     (none)")

    # ---------------------------------------------------------------- filings
    rows: list[dict] = []

    def collect(block: dict, source: str) -> None:
        forms = block.get("form", [])
        for i in range(len(forms)):
            rows.append({
                "source": source,
                "form": forms[i],
                "accession": block["accessionNumber"][i],
                "filing_date": block["filingDate"][i],
                "report_date": block["reportDate"][i],
                "primary_document": block.get("primaryDocument", [""] * len(forms))[i],
            })

    collect(sub["filings"]["recent"], "recent")
    overflow = sub["filings"].get("files", [])
    print(f"\n  recent block     {len(rows)} filings of all types")
    print(f"  overflow batches {len(overflow)}")

    for extra in overflow:
        url = f"https://data.sec.gov/submissions/{extra['name']}"
        before = len(rows)
        payload = client.get_json(url)
        if payload:
            collect(payload, f"OVERFLOW BATCH {extra['name']}")
            print(f"                   {extra['name']}: {len(rows) - before} filings")
        else:
            print(f"                   {extra['name']}: FETCH FAILED  <-- this is a bug")

    if not args.all_forms:
        rows = [r for r in rows if r["form"].upper() in ("10-K", "10-K/A")]

    rows.sort(key=lambda r: r["filing_date"], reverse=True)

    print()
    print("-" * 78)
    label = "all forms" if args.all_forms else "10-K and 10-K/A only"
    print(f"  FILING HISTORY  ({label}) -- {len(rows)} rows")
    print("-" * 78)
    print(f"  {'form':<9} {'period end':<12} {'filed':<12} {'FY':<6} {'in scope':<9} accession")
    print("  " + "-" * 74)

    for r in rows:
        if r["report_date"]:
            fy = fiscal_year_from_report_date(r["report_date"])
            fy_str = str(fy)
            in_scope = "yes" if fy in TARGET_FISCAL_YEARS else "no"
        else:
            fy_str, in_scope = "?", "no"
        print(f"  {r['form']:<9} {r['report_date'] or '?':<12} {r['filing_date']:<12} "
              f"{fy_str:<6} {in_scope:<9} {r['accession']}")

    # ---------------------------------------------------------------- verdict
    found = {
        fiscal_year_from_report_date(r["report_date"])
        for r in rows
        if r["report_date"] and r["form"].upper() == "10-K"
    }
    missing = [fy for fy in TARGET_FISCAL_YEARS if fy not in found]

    print()
    print("-" * 78)
    if missing:
        print(f"  MISSING under the current FY rule: {missing}")
        print("  Cross-check the 'period end' column above. If 10-Ks exist for those")
        print("  periods but got a different FY label, the rule is the problem.")
        print("  If no filing exists at all, the history genuinely is not under this CIK.")
    else:
        print("  Complete FY2021-FY2025 history. This company should NOT be excluded.")
    print("-" * 78)

    # Non-calendar year-end is worth calling out explicitly.
    ends = {r["report_date"][5:7] for r in rows if r["report_date"]}
    if ends and ends != {"12"}:
        print(f"\n  NOTE: period-end months seen: {sorted(ends)}")
        print("  This filer does not use a 31 December year-end. My rule maps")
        print("  Jan-Jun period ends to the PRIOR year, which may disagree with")
        print("  the fiscal year the company itself uses in its filings.")


if __name__ == "__main__":
    main()
