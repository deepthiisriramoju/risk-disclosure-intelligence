"""
Step 1 of ingestion: decide which companies are in the study, mechanically.

The rule, stated once so it can go straight into DECISIONS.md:

    A company is in the universe if and only if
      (a) it reported us-gaap:Assets between $10B and $250B in the size frame,
      (b) its SIC code on EDGAR is in SIC major group 60 (depository institutions),
      (c) it filed an original 10-K covering every one of FY2021..FY2025.
    Companies passing (a) and (b) but failing (c) are recorded with a reason,
    not silently dropped. If more than MAX_COMPANIES qualify, keep the largest
    by total assets.

Why it is built this way: you must never hand-pick the companies. The moment an
interviewer suspects selection was discretionary, every finding becomes
"you chose the companies that showed the effect." A mechanical rule plus a
written exclusion log is the difference between a study and an anecdote.

Two SEC endpoints do the work:

  XBRL frames  -- one request returns the Assets value for EVERY filer at a
                  given balance-sheet date. This is the cheap way to size-filter
                  ~8,000 registrants without 8,000 requests.
  submissions  -- per company: SIC code, tickers, and the full filing history.

Outputs (data/universe/):
  candidates.csv    every company that passed the size filter, with a status
  universe.csv      the selected companies
  filing_index.csv  one row per company-fiscal-year: what to download
  exclusions.csv    who was dropped and why  <-- do not delete this file
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import defaultdict
from pathlib import Path

from config import (
    ASSET_CEILING,
    ASSET_FLOOR,
    ASSET_FRAME,
    ASSET_FRAME_FALLBACK,
    FRAMES_URL,
    MAX_COMPANIES,
    SIC_CODES,
    SUBMISSIONS_URL,
    TARGET_FISCAL_YEARS,
    UNIVERSE_DIR,
    fiscal_year_from_report_date,
)
from sec_client import SECClient

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", stream=sys.stdout
)
log = logging.getLogger("universe")


# --------------------------------------------------------------- size filter
def size_filtered_ciks(client: SECClient, frame: str) -> dict[int, tuple[str, float]]:
    """Return {cik: (entity_name, assets)} for filers inside the asset band."""
    payload = client.get_json(FRAMES_URL.format(frame=frame))
    if payload is None:
        raise SystemExit(f"Frame {frame} not available. Try an earlier one.")
    out: dict[int, tuple[str, float]] = {}
    for row in payload.get("data", []):
        val = row.get("val")
        if val is None or not (ASSET_FLOOR <= val <= ASSET_CEILING):
            continue
        cik = int(row["cik"])
        # A frame holds one fact per entity, but be defensive: keep the largest.
        if cik not in out or val > out[cik][1]:
            out[cik] = (row.get("entityName", "").strip(), float(val))
    log.info("frame %s: %d filers inside the $%.0fB-$%.0fB band",
             frame, len(out), ASSET_FLOOR / 1e9, ASSET_CEILING / 1e9)
    return out


# --------------------------------------------------------------- submissions
def all_filing_rows(submissions: dict, client: SECClient, cik: int) -> list[dict]:
    """
    Flatten the submissions filing history into dicts.

    filings.recent holds the most recent ~1000 filings as PARALLEL ARRAYS, not
    a list of objects -- accessionNumber[i] belongs with form[i]. Older filings
    spill into filings.files, each a separate JSON to fetch. A bank files enough
    8-Ks that 1000 rarely reaches back five years, so the spill is fetched too.
    """
    rows: list[dict] = []

    def flatten(block: dict) -> None:
        forms = block.get("form", [])
        for i in range(len(forms)):
            rows.append(
                {
                    "form": forms[i],
                    "accession": block["accessionNumber"][i],
                    "filing_date": block["filingDate"][i],
                    "report_date": block["reportDate"][i],
                    "primary_document": block.get("primaryDocument", [""] * len(forms))[i],
                    "is_inline_xbrl": block.get("isInlineXBRL", [0] * len(forms))[i],
                    "size": block.get("size", [0] * len(forms))[i],
                }
            )

    flatten(submissions["filings"]["recent"])
    for extra in submissions["filings"].get("files", []):
        url = f"https://data.sec.gov/submissions/{extra['name']}"
        payload = client.get_json(url)
        if payload:
            flatten(payload)
    return rows


def tenk_by_fiscal_year(rows: list[dict]) -> tuple[dict[int, dict], dict[int, list[dict]]]:
    """
    Split 10-K filings into originals and amendments, keyed by fiscal year.

    If a company filed more than one original 10-K for a fiscal year (it happens
    after a year-end change), keep the earliest-filed one and let the duplicate
    show up as an amendment-shaped anomaly. Amendments are NOT merged here --
    10-K/A usually restates only part of the document and often omits Item 1A
    entirely. Deciding whether an amendment supersedes the original for Item 1A
    purposes is a DECISIONS.md question, not something to bury in a helper.
    """
    originals: dict[int, dict] = {}
    amendments: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        form = row["form"].upper()
        if form not in ("10-K", "10-K/A"):
            continue
        if not row["report_date"]:
            continue
        fy = fiscal_year_from_report_date(row["report_date"])
        if fy not in TARGET_FISCAL_YEARS:
            continue
        if form == "10-K/A":
            amendments[fy].append(row)
        elif fy not in originals or row["filing_date"] < originals[fy]["filing_date"]:
            originals[fy] = row
    return originals, dict(amendments)


# --------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame", default=ASSET_FRAME)
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N candidates; use 25 for a smoke test")
    args = parser.parse_args()

    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    client = SECClient()

    try:
        candidates = size_filtered_ciks(client, args.frame)
    except SystemExit:
        log.warning("falling back to frame %s", ASSET_FRAME_FALLBACK)
        candidates = size_filtered_ciks(client, ASSET_FRAME_FALLBACK)

    ordered = sorted(candidates.items(), key=lambda kv: -kv[1][1])
    if args.limit:
        ordered = ordered[: args.limit]

    qualified: list[dict] = []
    exclusions: list[dict] = []
    filing_rows: list[dict] = []

    log.info("checking SIC and filing history for %d candidates "
             "(~%d seconds at the configured rate)", len(ordered), len(ordered) // 5)

    for n, (cik, (frame_name, assets)) in enumerate(ordered, start=1):
        if n % 100 == 0:
            log.info("  %d/%d checked, %d qualified so far", n, len(ordered), len(qualified))

        sub = client.get_json(SUBMISSIONS_URL.format(cik=cik))
        if sub is None:
            exclusions.append({"cik": cik, "name": frame_name, "reason": "no_submissions_json"})
            continue

        sic = str(sub.get("sic", "")).strip()
        if sic not in SIC_CODES:
            continue  # not a bank; not an exclusion worth logging, just not in scope

        name = sub.get("name", frame_name)
        tickers = sub.get("tickers") or []
        rows = all_filing_rows(sub, client, cik)
        originals, amendments = tenk_by_fiscal_year(rows)

        missing = [fy for fy in TARGET_FISCAL_YEARS if fy not in originals]
        if missing:
            exclusions.append(
                {
                    "cik": cik,
                    "name": name,
                    "sic": sic,
                    "assets": assets,
                    "reason": "incomplete_10k_history",
                    "detail": "missing FY " + ",".join(str(fy) for fy in missing),
                }
            )
            continue

        qualified.append(
            {
                "cik": cik,
                "name": name,
                "ticker": tickers[0] if tickers else "",
                "sic": sic,
                "sic_description": sub.get("sicDescription", ""),
                "state": sub.get("stateOfIncorporation", ""),
                "total_assets_usd": int(assets),
            }
        )
        for fy, row in sorted(originals.items()):
            filing_rows.append(
                {
                    "cik": cik,
                    "ticker": tickers[0] if tickers else "",
                    "name": name,
                    "fiscal_year": fy,
                    "form": row["form"],
                    "accession": row["accession"],
                    "filing_date": row["filing_date"],
                    "report_date": row["report_date"],
                    "primary_document": row["primary_document"],
                    "is_inline_xbrl": row["is_inline_xbrl"],
                    "reported_size_bytes": row["size"],
                    "n_amendments": len(amendments.get(fy, [])),
                    "amendment_accessions": ";".join(
                        a["accession"] for a in amendments.get(fy, [])
                    ),
                }
            )

    qualified.sort(key=lambda r: -r["total_assets_usd"])
    selected = qualified[:MAX_COMPANIES]
    selected_ciks = {r["cik"] for r in selected}
    for r in qualified[MAX_COMPANIES:]:
        exclusions.append({**r, "reason": "below_size_rank_cutoff"})
    filing_rows = [r for r in filing_rows if r["cik"] in selected_ciks]

    _write_csv(UNIVERSE_DIR / "universe.csv", selected)
    _write_csv(UNIVERSE_DIR / "filing_index.csv", filing_rows)
    _write_csv(UNIVERSE_DIR / "exclusions.csv", exclusions)

    log.info("-" * 60)
    log.info("qualified: %d   selected: %d   filings to download: %d",
             len(qualified), len(selected), len(filing_rows))
    log.info("excluded for incomplete history: %d",
             sum(1 for e in exclusions if e.get("reason") == "incomplete_10k_history"))
    log.info("cache hits=%d misses=%d retries=%d", *client.stats.values())
    log.info("wrote %s", UNIVERSE_DIR)

    if len(selected) < 40:
        log.warning(
            "Only %d companies qualified. Widen ASSET_FLOOR/ASSET_CEILING or add "
            "SIC codes before proceeding -- a 5-year panel of under 40 firms makes "
            "every peer-comparison claim statistically thin.", len(selected)
        )


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
