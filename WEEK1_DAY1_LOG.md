# Week 1, Day 1 — Work Log

Plain-language record of what was built, what the data showed, and what comes next.

---

## What this project is

Every US public company must publish a "Risk Factors" section in its annual report
(Form 10-K). It is 20–60 pages of dense text describing everything that could go
wrong with the business. Companies rewrite it every year — adding new risks,
dropping old ones, quietly rewording others.

Nobody can cheaply answer: **what is an industry newly worried about this year that
it was not worried about last year?**

This project builds a pipeline that reads those sections, turns them into structured
data, and tracks how they change year over year. The distinguishing feature is that
its accuracy is measured and published, not assumed.

---

## Terms you will use constantly

| Term | What it means |
|---|---|
| **10-K** | A company's annual report filed with the SEC. Legally required, public, free. |
| **Item 1A** | The "Risk Factors" section inside a 10-K. Always numbered Item 1A. |
| **EDGAR** | The SEC's public database of every filing ever made. |
| **API** | A way for a program to request data from a website and get back structured data instead of a web page. |
| **CIK** | The ID number the SEC gives each company. Our unique key for a company. |
| **SIC code** | A 4-digit industry code. Group 60 = banks and savings institutions. |
| **XBRL** | Machine-readable financial data tagged inside filings. Lets us pull "total assets" for thousands of companies at once. |
| **Fiscal year (FY)** | A company's own 12-month accounting year. Most banks use 1 Jan – 31 Dec. |
| **Terminal / PowerShell** | A window where you type commands instead of clicking. |
| **Virtual environment (`.venv`)** | A private folder holding this project's own copy of Python and its libraries, so projects cannot break each other. |
| **Library / package** | Pre-written code you install and use. We use `requests` (fetch web data), `pandas` (work with tables), `lxml` (read HTML), `duckdb` (database). |
| **Raw layer** | Downloaded files kept exactly as received, never edited. The source of truth. |
| **Manifest** | A log recording, for every file, where it came from, when, and a fingerprint proving it hasn't changed. |
| **Checksum / SHA-256** | A fingerprint of a file. If one byte changes, the fingerprint changes. |
| **Quarantine** | Setting aside records that failed, with the reason recorded, instead of deleting them. |
| **Parse rate** | The share of documents the code read successfully. A measured number, not a target. |
| **Survivorship bias** | Studying only the survivors, and mistakenly drawing conclusions about everyone. |

---

## Part 1 — Setting up the computer

Installed Python 3.14.6, VS Code (code editor), and five libraries into a virtual
environment. Created the project folder at
`C:\Users\srira\projects\risk-disclosure-intelligence`.

One Windows-specific hurdle: the machine's security setting (`AllSigned`) blocked all
scripts, including the one that switches on the virtual environment. Changed the
setting for your user account only — downloaded scripts still require a signature;
scripts you create locally may run.

**Two things to repeat every new terminal session:**

```powershell
..\.venv\Scripts\Activate.ps1
$env:SEC_USER_AGENT = "Risk Disclosure Intelligence (academic research) deepthi4@iastate.edu"
```

The `(.venv)` prefix in the prompt confirms the first one worked. The SEC requires the
second — they reject anonymous automated requests.

---

## Part 2 — Choosing what to study

**Industry: US regional banks, FY2021–FY2025.**

Chosen because of a natural experiment. Silicon Valley Bank failed in March 2023.
Annual reports for 2022 were filed in February 2023 — *before* it. Reports for 2023
were filed in February 2024 — *after* it. Every bank rewrote its risk language in
between, at different speeds.

That gives the project a known-true signal to validate against, and a defensible
headline finding: *X% of regional banks disclosed a deposit-concentration risk in
FY2023 that was absent in FY2022.*

**Size band: $10B–$250B in total assets.** Below $10B, risk sections are a few pages of
boilerplate. Above $250B, the giant banks face different problems and file documents
several times longer, which would distort every average.

---

## Part 3 — Building the company list

`build_universe.py` applies one mechanical rule:

> In the universe if and only if: (1) total assets between $10B and $250B,
> (2) SIC code in group 60, (3) filed a 10-K for every year FY2021–FY2025.
> If more than 55 qualify, keep the largest.

**Why mechanical matters:** if you pick companies by hand, any finding can be
dismissed as "you chose the ones that showed the effect." A stated rule plus a log of
what it excluded is checkable. This is your best answer to the interview question
*"how did you choose your data?"*

**Results:**

| | |
|---|---|
| Companies in the asset band | 828 |
| Passed the bank + history filters | 88 |
| Selected (capped at 55) | **55** |
| Filings to download (55 × 5 years) | **275** |
| Excluded — smaller than the top 55 | 33 |
| Excluded — incomplete filing history | 3 |

Verified against the SEC's own industry labels: 31 state commercial banks, 19 national
commercial banks, 5 savings institutions. Nothing else leaked in.

---

## Part 4 — What the data revealed

### The three excluded companies had three different causes

| Company | What happened |
|---|---|
| **Pinnacle Financial Partners** | A merger created a brand-new legal entity (originally named "Steel Newco Inc."). To the SEC it is a company born in 2025 with one filing. The older history sits under a different CIK. |
| **Axos Financial** | 21 years of unbroken filings — but its fiscal year ends 30 June, not 31 December. Its "2022" report was filed *after* the March 2023 bank failures, while every peer's was filed before. Not comparable. |
| **Central Bancompany** | Recently listed. Its first-ever 10-K is FY2025, so there is no earlier history to compare. |

All three appear in the project spec as edge cases to handle deliberately. They were
found in real data on day one, not read about.

### A limitation that cannot be fixed

The size filter reads a December 2025 balance sheet, so a bank must have been alive in
2026 to appear at all. **Silicon Valley Bank, Signature Bank and First Republic — the
banks that actually failed — cannot be in this study.**

This is survivorship bias, and it is structural. The honest response is scope: this
studies *disclosure behaviour among surviving peers*, and cannot describe the sector
as a whole. Stating it first is what makes it a strength rather than something an
interviewer catches.

### Five companies changed identity mid-study

A check on all 55 found five with name changes inside the study window. Looking each
up in real SEC filings:

| Company | What actually happened |
|---|---|
| **Mechanics Bancorp** | Reverse merger, Sept 2025. Years 2021–24 are HomeStreet (Seattle); 2025 is Mechanics Bancorp (California, $22.7B). Two different companies under one ID. **Exclude.** |
| **Beacon Financial** | Merger of equals with Brookline Bancorp, Sept 2025. Roughly doubled in size. **Exclude.** |
| **Flagstar** | The rename was cosmetic — but the bank absorbed nearly all of failed Signature Bank's deposits in March 2023 ($38.4B). **Exclude or flag.** |
| **SouthState** | Acquired Independent Bank Group, Jan 2025. Assets to ~$65B. **Flag.** |
| **WAFD** | Acquired Luther Burbank Corporation, early 2024. **Flag.** |

**The important insight:** all five were caught because they *renamed*. But companies
also merge without renaming — UMB Financial bought Heartland Financial in 2024 and was
never flagged, because its name never changed. So the check finds a real problem but
misses an unknown number of cases. Next step is to detect mergers by a sudden jump in
total assets instead, which works regardless of naming.

---

## Part 5 — Downloading the filings

`download_filings.py` fetched all 275 annual reports.

**275 downloaded, 0 failed, 0 rate-limit retries.**

Each is stored compressed, exactly as the SEC sent it, with its source URL, download
timestamp and SHA-256 fingerprint in a manifest. Nothing later in the pipeline is
allowed to modify these files. If a result looks wrong in three weeks, you can prove
whether the bug is in your code or in the original document.

---

## Part 6 — Measuring the documents before parsing them

`profile_filings.py` read all 275 and reported their structure. Doing this *before*
writing the parser meant the parser was designed from evidence.

| What was measured | Result | Why it mattered |
|---|---|---|
| File size | 3.3–23.7 MB, median 7.5 MB | Large but manageable |
| Actual text | ~680,000 characters median | 91% of each file is invisible formatting code |
| Heading tags | present in **2 of 275** | Cannot find sections by document structure |
| Bold tags | median **0** | Bold is done with styling, not tags — must read style attributes |
| Layout tables | ~163 per document | Tables are used for page layout, not data |
| "Item 1A" appearances | **2 to 11 times** per document | The phrase appears in the contents page and in cross-references, not just at the section |
| Item 1C present | absent in **111 of 275** | Exactly 55 companies × 2 years — the SEC added this section starting FY2023, so the section's end marker changes mid-study |

The profile showed the three places "Item 1A" appears, each with a recognisable shape:

- **Contents page** — followed by a page number and the next section's name
- **Cross-reference** — followed by words like *below*, *of this*, *for further*
- **The real section** — followed by prose that begins a section

---

## Part 7 — The extractor

`extract_item1a.py` scores every "Item 1A" occurrence against those three shapes,
picks the best one that also produces a sensibly-sized section, and finds where the
section ends — automatically handling the FY2023 change in end markers.

For each filing it saves the section text **plus a bold/not-bold flag on every run of
text**. That matters: since these documents have no heading tags, styled text is the
only clue for finding where one risk factor ends and the next begins. That is next
week's job, and throwing the styling away now would make it impossible.

---

## Corrections made during the session

One line each, as requested.

| # | What went wrong | Why | Fix |
|---|---|---|---|
| 1 | Claimed `.venv` was already in `.gitignore` | I wrote the file and misremembered its contents | Added the line |
| 2 | Said "download the file below" twice with nothing attached | Wrote the instruction before creating the file | Attached both; now verify a file exists before referencing it |
| 3 | Predicted banks (Truist, PNC) that your filter correctly excluded | Recalled company names from memory instead of checking the size band | Verified against the SEC's own SIC labels in your data |
| 4 | Example command outputs contained invented numbers | Written to show the *shape* of a successful run, not labelled clearly enough as illustrative | Every real number in this log comes from your runs |
| 5 | Advised "take the last Item 1A match" | Assumed the layout instead of measuring it | The profile disproved it — last match is usually a cross-reference; replaced with scoring |
| 6 | First continuity check flagged 36 of 55 companies | No date filter and no name normalisation, so 1990s renames and punctuation edits both fired | Rewrote with both; now flags 5 |
| 7 | Bold detection silently returned zero on real-sized documents | Cached results using `id()` of lxml elements, whose IDs are recycled | Removed the cache |
| 8 | Profiler showed undecoded `&#8220;` instead of quote marks | Did not decode HTML entities before matching text | Added decoding in the extractor |
| 9 | Called the fiscal-year problem "out of scope" in a code comment | Assumed all banks use December year-ends without checking | Your data found Axos; now a stated exclusion rule |

The pattern: claims about how systems are *structured* held up; claims recalled from
memory about *specific companies and numbers* did not. Checking output against real
data caught every one of these.

---

## What exists on your computer now

```
risk-disclosure-intelligence\
├── DECISIONS.md                  written record of every choice and its reasoning
├── requirements.txt
├── .gitignore
├── .venv\                        private Python environment
├── src\
│   ├── config.py                 all tunable settings in one place
│   ├── sec_client.py             rate-limited, cached, retrying SEC connection
│   ├── build_universe.py         applies the universe rule
│   ├── download_filings.py       fetches filings into the immutable raw layer
│   ├── inspect_company.py        diagnostic: one company's full filing history
│   ├── audit_continuity_v2.py    checks for identity changes mid-study
│   ├── profile_filings.py        measures document structure
│   └── extract_item1a.py         pulls out the Risk Factors section
└── data\
    ├── cache\                    every SEC response, so nothing is re-downloaded
    ├── universe\                 universe.csv, filing_index.csv, exclusions.csv,
    │                             continuity_flags.csv
    ├── raw\                      275 filings + manifest.jsonl
    └── interim\                  extracted Item 1A sections + parse-rate report
```

---

## Next

1. Run `extract_item1a.py` on all 275 and record the parse rate — this is a graded
   deliverable, and the failures matter more than the successes.
2. Read the shortest extracted sections. A section far below the median probably
   grabbed the wrong text — the failure that reports success.
3. Apply the December-year-end rule, which removes Axos.
4. Decide the five flagged companies and write the verdicts into `DECISIONS.md`.
5. Replace name-based merger detection with a total-assets jump check.
6. Build the splitter: divide each section into individual risk factors, using the
   bold flags. This produces the items you hand-label in Week 2.

---

## What to say about today in an interview

You built a data pipeline against a live government API, applied a stated selection
rule to 828 candidates, and produced a verified 55-company panel. You found three
distinct data-integrity problems — a merger shell, a fiscal-year misalignment, a new
registrant — and diagnosed each to root cause. You identified a structural bias you
cannot remove and scoped the research question around it. You measured your documents
before writing a parser, which killed two approaches that would have failed silently.

The most useful sentence: **"I built a quality check, it flagged two-thirds of my
sample, so I threw it away and rebuilt it — a check that fires on everything carries
no information."** Most candidates cannot describe having tested their own tooling.
