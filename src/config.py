"""
Universe and ingestion parameters.

Every number in this file is a decision you will have to defend in DECISIONS.md.
Change them here, never inline in the scripts, so the repo always shows one
authoritative definition of "the universe".
"""

from pathlib import Path

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE_DIR = DATA / "cache"          # raw HTTP responses, keyed by URL hash
UNIVERSE_DIR = DATA / "universe"    # candidates.csv, universe.csv, filing_index.csv
RAW_DIR = DATA / "raw"              # downloaded filing documents + manifest.jsonl

# ---------------------------------------------------------------- universe
# SIC major group 60 = Depository Institutions.
# 6020 Commercial banks (generic) / 6021 National commercial banks
# 6022 State commercial banks / 6035-6036 Savings institutions
# Bank holding companies register under several of these fairly arbitrarily,
# so restricting to a single 4-digit code would silently drop real peers.
SIC_CODES = {"6020", "6021", "6022", "6035", "6036"}

# Fiscal years the company must have a 10-K for, all five, no gaps.
TARGET_FISCAL_YEARS = [2021, 2022, 2023, 2024, 2025]

# Size band, in USD of total assets. Floor excludes community banks whose
# Item 1A is three pages of boilerplate. Ceiling excludes the G-SIBs, whose
# risk factors are dominated by systemic/regulatory concerns that have no
# peer group inside this universe.
ASSET_FLOOR = 10_000_000_000
ASSET_CEILING = 250_000_000_000

# XBRL frame used for the size filter. An "I" frame is instantaneous
# (balance-sheet) as opposed to a duration (income-statement) frame.
ASSET_FRAME = "CY2025Q4I"
ASSET_FRAME_FALLBACK = "CY2024Q4I"

# Hard cap on companies. If more qualify, keep the largest by assets.
MAX_COMPANIES = 55

# ---------------------------------------------------------------- http
# SEC's published ceiling is 10 requests/second. Sitting at the ceiling buys
# you nothing here (the whole run is a few thousand requests) and risks a
# temporary IP block, which costs a day.
REQUESTS_PER_SECOND = 6.0
HTTP_TIMEOUT = 30
MAX_RETRIES = 5

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/{frame}.json"
ARCHIVE_DIR_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}"


def fiscal_year_from_report_date(report_date: str) -> int:
    """
    Map a period-end date to a fiscal year label.

    Rule: period ending in Jul-Dec belongs to that calendar year; period ending
    Jan-Jun belongs to the prior one. A January 2026 year-end is FY2025.

    In this universe essentially every filer has a 31 December year-end, so this
    rule almost never fires. That is deliberate -- it removes the fiscal-year
    alignment problem from the project instead of leaving it as an unquantified
    error source. Record it as a scope decision, not as a thing you solved.
    """
    year, month = int(report_date[0:4]), int(report_date[5:7])
    return year if month >= 7 else year - 1


# ---------------------------------------------------------------- exclusions
# Companies removed from the analysis, with the decision that removed each one.
# Kept here rather than deleted from the data: the raw layer stays complete and
# the exclusion is visible, auditable, and reversible. Deleting the files would
# hide the choice.
#
# Every CIK below was read from pipeline output, not recalled.
EXCLUDED_CIKS = {
    1299709: ("AX",   "D5", "Fiscal year ends 30 June, not 31 December. Its FY2022 "
                            "10-K was filed August 2023 -- after the March 2023 bank "
                            "failures -- while every December-year-end peer filed the "
                            "comparable document in February 2023, before them. The "
                            "documents are not comparable whichever fiscal-year label "
                            "is used."),
    1518715: ("MCHB", "D6", "Reverse merger completed 2 September 2025. This CIK is "
                            "HomeStreet's: FY2021-24 describe HomeStreet, Inc. of "
                            "Seattle, FY2025 describes Mechanics Bancorp of California. "
                            "Year-over-year comparison would compare two companies."),
    1108134: ("BHLB", "D6", "Merger of equals with Brookline Bancorp completed 1 "
                            "September 2025 on Berkshire Hills Bancorp's CIK. The "
                            "combined institution is roughly twice the size of the "
                            "company described in the FY2021 filing."),
    101382:  ("UMBF", "D11", "Styles body paragraphs identically to risk-factor "
                             "headings, so the 'styled heading followed by unstyled "
                             "prose' rule cannot separate them. Splitter yields 1-2 "
                             "risk factors for a ~50,000-character section."),
    1077428: ("TCBI", "D11", "Risk-factor headings are unstyled bullet items inside a "
                             "'Summary of Risk Factors' block. With no styling to key "
                             "on, the splitter returns summary bullets rather than the "
                             "full risk factors."),
    36966:   ("FHN",  "D11", "Embeds a second table of contents ('TABLE OF ITEM 1A "
                             "TOPICS') inside Item 1A. Every topic row becomes a "
                             "phantom risk factor: 88-96 per filing against a peer "
                             "median of 40."),
}

# Retained, but carrying a known caveat that must travel with any result.
FLAGGED_CIKS = {
    910073: ("FLG", "D6", "Rename was cosmetic, but Flagstar Bank assumed nearly all "
                          "of failed Signature Bank's deposits in March 2023 -- $38.4B "
                          "in assets. FY2021 and FY2025 describe materially different "
                          "balance sheets."),
    764038: ("SSB", "D6", "Acquired Independent Bank Group January 2025; assets to "
                          "~$65B. Item 1A extraction also over-reaches (218,000 chars "
                          "against a 78,000 median), so its split counts are unreliable."),
    936528: ("WAFD", "D6", "Acquired Luther Burbank Corporation early 2024, extending "
                           "the footprint to nine western states."),
}
