# DECISIONS

Every non-obvious choice in this project, what was rejected, and why.

Written as decisions are made, not reconstructed afterwards. A decision recorded
a week late is a justification.

---

## D1 — Industry: US depository institutions, FY2021–FY2025

**Decision.** SIC major group 60 (codes 6020, 6021, 6022, 6035, 6036), five
fiscal years, FY2021 through FY2025.

**Why.** The study measures how risk disclosure changes year over year, so the
industry needs a period where disclosure demonstrably changed. Silicon Valley
Bank failed in March 2023. FY2022 10-Ks were filed in February 2023, before it;
FY2023 10-Ks in February 2024, after it. Every regional bank rewrote its
liquidity, deposit-concentration and unrealised-loss language between those two
filings, at different speeds and to different degrees. That gives the
year-over-year matching component a known-true signal to be validated against,
rather than only unknown cases.

Bank holding companies also use 31 December fiscal year ends almost universally,
which removes a confounder — see D5.

**Rejected.** Prepackaged software (SIC 7372): too many mid-window IPOs, so a
large share of the universe has no FY2021 baseline. Pharmaceutical preparations
(SIC 2834): dominated by clinical-stage small caps with erratic filing histories
and company-specific risk factors that are not peer-comparable.

**Restricting to one 4-digit SIC code was also rejected.** Bank holding
companies register under several codes in group 60 fairly arbitrarily, so a
single code would silently drop genuine peers.

---

## D2 — Universe rule: mechanical, with a published exclusion log

**Decision.** A company is in the universe if and only if:

1. it reported `us-gaap:Assets` between $10B and $250B in the `CY2025Q4I` XBRL
   frame, **and**
2. its EDGAR SIC code is in {6020, 6021, 6022, 6035, 6036}, **and**
3. it filed an original 10-K covering each of FY2021–FY2025 with no gaps.

If more than 55 qualify, the largest by total assets are kept. Companies failing
(3) are written to `exclusions.csv` with the specific missing years.

**Result.** 828 filers in the asset band → 88 qualified → 55 selected.
33 dropped at the size-rank cap, 3 for incomplete history.

**Why mechanical.** Hand-picking companies makes every finding vulnerable to the
same objection: that the sample was chosen to produce the result. A stated rule
plus a log of what it excluded is checkable by a reader; a curated list is not.

**Why the size band.** The $10B floor excludes community banks whose Item 1A is
a few pages of boilerplate. The $250B ceiling excludes the largest banks, whose
risk factors are dominated by systemic and capital-regime concerns with no peer
group inside this universe, and whose filings are several times longer — which
would distort every per-company metric.

**Cost of the 55 cap.** 33 qualifying banks were dropped solely for being
smaller. The panel is therefore the *larger* end of the mid-size band, not a
random sample of it.

---

## D3 — Survivorship bias, structural and unavoidable

**Limitation, not a decision.** The asset filter reads a December 2025 balance
sheet. A bank must have been alive and filing in early 2026 to appear in it at
all. Silicon Valley Bank, Signature Bank and First Republic — the banks that
actually failed in 2023 — cannot be in this universe, and were excluded before
the completeness check ever ran.

**Consequence.** This is a study of *disclosure behaviour among surviving
peers*. It cannot support claims about the regional banking sector as a whole,
and any finding about the 2023 period must be stated with that scope.

**Why not fix it.** Reconstructing a point-in-time universe from historical
filings is feasible but is a project of its own. The honest scope statement is
cheaper and does not overclaim.

---

## D4 — Company identity is CIK; corporate reorganisations break this

**Decision.** A company is identified by its SEC CIK. Predecessor histories are
not stitched across CIK changes.

**Evidence from the data.** Pinnacle Financial Partners appears under CIK
2082866, formerly *Steel Newco Inc.* until 2025-12-31 — an entity created to
effect a merger. It has exactly one 10-K. The pre-merger history lives under a
different CIK. Correctly excluded by rule D2(3), but for a reason the rule does
not name.

**Cost.** Companies undergoing structural reorganisation are absent from the
panel. This compounds D3.

**The residual risk this does not cover.** A CIK can *survive* a transaction
that materially changes the business. Such a company passes every check while
its FY2021 and FY2025 filings describe different institutions. Addressed
partially by the continuity audit — see D6.

---

## D5 — Only 31 December fiscal year ends

**Decision.** Registrants with a non-December fiscal year end are excluded.

**Evidence.** Axos Financial (CIK 1299709) has 21 years of unbroken 10-Ks and a
30 June year end. Its filing covering July 2022–June 2023 was filed 29 August
2023 — five months *after* the March 2023 failures. A December-year-end peer's
comparable filing was made in February 2023, *before* them. Any fiscal-year
labelling scheme places these in adjacent buckets while the documents were
written under opposite conditions.

**Why exclusion rather than relabelling.** Adopting the filer's own convention
aligns the label but not the period: Axos's twelve months would still be offset
six months from its peers', in a window where six months is the entire subject
of the study.

**Benefit.** Every filing in the panel covers an identical fiscal period and was
filed within a few weeks of its peers.

**Cost.** One company. Universe drops from 55 to 54.

> **TODO:** apply this in `config.py` / `build_universe.py` and re-run.
> Confirm via `filing_index.csv` how many selected filings have non-December
> period ends before assuming Axos is the only case.

---

## D6 — Continuity: which companies stayed the same company?

**Decision.** Every selected company is checked for in-window discontinuity, and
each flag is resolved against primary sources before a verdict is recorded.

**Calibration note.** The first version of this check flagged 36 of 55 companies
and was discarded. It compared raw EDGAR registrant names with no date filter,
so it fired on punctuation edits (`CULLEN/FROST BANKERS, INC.` vs
`CULLEN FROST BANKERS INC`), on records where the name did not change at all,
and on renames from the 1990s. A check that fires on two-thirds of a population
carries no information. The rewritten version normalises names before comparison
and only counts changes dated inside the window: **5 flagged, 50 clean, 48
signals suppressed.**

**Negative result worth stating.** The `first_10k_in_window` check fired zero
times. No company in the panel has FY2021 as its first-ever 10-K, so the
expected first-year artefact -- an unusually long inaugural Item 1A followed by
a false wave of DROPPED risks the next year -- does not occur in this data.
Checked for, absent.

**Verdicts, resolved against SEC filings and company announcements:**

| Company | CIK | Finding | Verdict |
|---|---|---|---|
| Mechanics Bancorp | 1518715 | Reverse merger completed 2 Sept 2025. This is HomeStreet's CIK: FY2021-24 are HomeStreet, Inc. (Seattle); FY2025 is Mechanics Bancorp (Walnut Creek, $22.7B, 166 branches). The 10-K states Mechanics Bank is the accounting acquirer and restates prior periods to Mechanics' history. | **Exclude** |
| Beacon Financial Corp | 1108134 | Merger of equals with Brookline Bancorp completed 1 Sept 2025, on Berkshire Hills Bancorp's CIK. Combined entity $22.8B vs Berkshire standalone. | **Exclude** |
| Flagstar Financial | 910073 | Rename was cosmetic (certificate amendment, Oct 2024). But Flagstar Bank assumed nearly all of failed Signature Bank's deposits in March 2023 -- $38.4B in assets, 40 branches. | **Exclude, or retain and report separately** |
| SouthState Corp | 764038 | Acquired Independent Bank Group 1 Jan 2025; assets to ~$65B; entered Texas and Colorado. | **Retain, flag in results** |
| WAFD Inc | 936528 | Acquired Luther Burbank Corporation, completed early 2024; footprint extended to nine western states. | **Retain, flag in results** |

**Stated limitation of the check.** Name-change detection identifies mergers only
when accompanied by a rebrand. UMB Financial's 2024 acquisition of Heartland
Financial passed unflagged because the registrant name never changed. The check
therefore finds a real class of problem but misses an unknown number of cases.

**Replacement.** Detect acquisitions by discontinuity in total assets rather than
by name: pull `us-gaap:Assets` for CY2021Q4I-CY2025Q4I from the XBRL frames API
and flag year-over-year growth above a stated threshold. Organic bank growth is a
few percent annually; SouthState's jump was ~45% and Mechanics' larger still.
Five requests, no per-company calls, and it detects transactions independent of
naming.

---

## D7 — Data source and access terms

**Decision.** SEC EDGAR only, via the official REST APIs (`data.sec.gov`
submissions, XBRL frames, and the Archives document store). No scraping, no
third-party or commercial data.

**Access terms observed.** A descriptive `User-Agent` with a working contact
address, supplied via the `SEC_USER_AGENT` environment variable rather than
hardcoded. Request rate capped at 6/second against SEC's published ceiling of
10/second — the margin costs nothing on a run of this size and avoids a
temporary block. Full 828-candidate build completed with `retries=0`.

**Raw layer is immutable.** Every fetched document is stored exactly as
received, gzipped, with source URL, UTC fetch timestamp, SHA-256 and byte count
in `manifest.jsonl`. Nothing downstream writes to it. This makes any
parser-versus-source disagreement resolvable against the bytes the SEC actually
sent.

**Reproducibility, stated precisely.** The pipeline is reproducible *from the
stored raw layer*, not from the live API. Companies file amendments and EDGAR
reindexes, so a re-fetch months later may return different documents. This is
the reason raw is timestamped and immutable rather than re-fetched on demand.

---

## D8 — Item 1A extraction: designed from a corpus profile, not assumptions

**Decision.** Profile all 275 documents before writing the parser. The profile
(`profile_filings.py`) is a deliverable, not a scratch step.

**Why.** Two parser designs that looked reasonable were falsified by measurement:

1. *Find the section by HTML structure.* Heading tags (`<h1>`-`<h6>`) appear in
   **2 of 275** filings; median `<b>`/`<strong>` count is **zero**. Emphasis is
   applied with CSS on `<span>` elements, and ~163 `<table>` elements per
   document are used for page layout. Structural detection is not available.
2. *Take the last "Item 1A" match.* "Item 1A" occurs 2-11 times per filing. The
   last occurrence sits at a median of 40.8% through the text and as deep as
   85.7%, because it is usually a cross-reference past the real section. Item 1B
   is absent after the last match in **211 of 275** filings.

**The rule adopted.** Score every occurrence against the three shapes the
profile revealed, then require a plausible resulting section length:

| Shape | Signature in the text |
|---|---|
| Table of contents | followed by a page number and the next item's name within ~200 chars |
| Cross-reference | followed by *below*, *of this*, *for further*; or preceded by *see*, *refer to*, *as discussed in*; or the section name in quotes |
| Real section | heading followed by prose that opens a section (*Risk Factor Summary*, *We are subject to*, *You should carefully*) |

**Length as backstop.** A section is accepted only at 8,000-500,000 characters.
A table-of-contents hit terminates a few hundred characters later and is
disqualified on length alone, independent of scoring.

**Styling is preserved, not discarded.** Each extracted section is stored as
text runs carrying a bold/not-bold flag. Because these documents have no heading
tags, styled text is the only available signal for risk-factor boundaries;
flattening to plain text here would make the splitter impossible.

---

## D9 — The section terminator changes mid-window

**Finding.** Item 1C (Cybersecurity) is absent from **111 of 275** filings --
exactly 55 companies x 2 years. The SEC introduced Item 1C for FY2023 annual
reports.

**Consequence.** FY2021-22 documents run Item 1A -> 1B -> 2. FY2023-25 documents
run Item 1A -> 1B -> 1C -> 2. A parser hardcoding one ordering silently
mis-terminates half the panel.

**Decision.** Terminate at the first of Item 1B, 1C or 2 appearing after the
accepted Item 1A position, whichever comes first. No year-specific branching;
the rule is correct for both regimes.

---

## D10 — The parse rate is measured, not targeted

**Decision.** `extract_item1a.py` reports per-filing success, and failures are
quarantined with a reason rather than dropped. The rate is published in
EVALUATION.md alongside the failure taxonomy.

**Rule.** The parse rate is not tuned upward without reading the documents it
fails on. Raising a metric by adjusting thresholds until failures disappear
produces a number that measures the threshold, not the parser.

**The failure mode that matters most.** A parse that succeeds and returns
plausible but wrong text -- a summary table instead of the section -- raises no
error and produces no missing value. Detection is by distribution: sections far
below the median length are inspected manually before the rate is believed.

---

## D11 — Three filers excluded for document structure

**Decision.** UMB Financial (CIK 101382), Texas Capital Bancshares (1077428) and
First Horizon (36966) are excluded from the risk-factor split. 15 filings of
275, 5.5% of the corpus.

**Why each fails.** All three were diagnosed by reading their actual text runs,
not inferred from output counts:

| Company | Structure | Result |
|---|---|---|
| UMBF | Styles body paragraphs identically to risk-factor headings | The "styled heading followed by unstyled prose" rule cannot separate them; 1–2 risk factors from a ~50,000-character section |
| TCBI | Risk headings are unstyled bullet items inside a "Summary of Risk Factors" block | With no styling to key on, the splitter returns summary bullets rather than full risk factors |
| FHN | Embeds a second table of contents ("TABLE OF ITEM 1A TOPICS") inside Item 1A | Every topic row becomes a phantom risk factor: 88–96 per filing against a peer median of 40 |

**Why not write filer-specific rules.** Each would be a narrow regex keyed to one
company's formatting, and each adds a failure mode for the other 52. This was
not hypothetical during the build: the risk-summary rule regressed Popular from
26 risk factors to 10, and the fix for Popular in turn disabled the risk-summary
rule. Interacting special cases were the single largest source of defects in
this component. Three more of them to recover 5.5% of filings is a poor trade.

**Cost.** 15 filings. Recorded rather than silently mis-split — which is the
point: these companies previously produced plausible-looking wrong output that
would have entered the gold set undetected.

**Reversible.** Excluded companies are skipped at split time, not deleted. The
raw and extracted layers remain complete, so the decision can be revisited by
removing an entry from `EXCLUDED_CIKS` in `config.py`.

---

## D12 — Retained with a caveat

**Decision.** Three companies stay in the panel but are marked in
`FLAGGED_CIKS`, and every result depending on them must name them.

| Company | CIK | Caveat |
|---|---|---|
| Flagstar Financial | 910073 | Assumed nearly all of failed Signature Bank's deposits in March 2023 — $38.4B in assets. FY2021 and FY2025 describe materially different balance sheets. |
| SouthState | 764038 | Acquired Independent Bank Group January 2025, assets to ~$65B. Item 1A extraction also over-reaches (218,000 chars against a 78,000 median), so its split counts are unreliable. |
| WAFD | 936528 | Acquired Luther Burbank Corporation early 2024, extending to nine western states. |

**Why retain rather than exclude.** Excluding every company that made an
acquisition would empty the panel — consolidation is normal in this industry.
The workable standard is: exclude where the registrant became a *different
company* (D6), flag where the business grew materially but remained itself.

**Obligation this creates.** In Week 4, the headline finding is tested with and
without these three. A result that survives their removal is reported as robust;
one that does not is reported as depending on them.

---

## Compound effect of all exclusions

Stated together, because the individual entries understate it.

| Filter | Removes |
|---|---|
| Asset band read from a Dec-2025 balance sheet (D2, D3) | every bank that failed — SVB, Signature, First Republic |
| Complete FY2021–FY2025 10-K history (D2) | 3 companies: a merger shell, a new registrant, a late filer |
| 31 December fiscal year end (D5) | 1 company |
| Registrant identity unchanged across the window (D6) | 2 companies |
| Parseable document structure (D11) | 3 companies |

**The panel is therefore: banks that survived to 2026, report on a calendar
year, did not restructure, and format their filings conventionally.**

Every one of those is defensible on its own. Together they are a real limit on
what the study can claim, and the limit is not visible from any single decision.
Any finding is a statement about *disclosure behaviour among conventionally-filing
surviving peers*, not about the regional banking sector.

**This appears in the README's known-limitations section, not only here.** A
reader who works it out unaided will assume it was hidden.

---

## D13 — Year-over-year matching: threshold and guards

**Decision.** `MATCH_MIN = 0.25`, `UNCHANGED_MIN = 0.90`, similarity = 0.70 ×
heading cosine + 0.30 × body cosine, matched by iterative mutual-best with a
0.05 runner-up margin.

### Why heading and body are scored separately

A first version concatenated them. Calibration against the real corpus showed
why that fails: risk factors with **identical headings** scored as low as 0.22
because their bodies had been rewritten.

- 0.407 — *"We are subject to extensive government regulation and supervision."*
- 0.225 — *"Deposit insurance premiums could increase further in the future."*

Under a single score those would be counted as NEW plus DROPPED — inflating the
headline with risks that plainly carried forward. The heading is the *identity*
of a risk; the body is its explanation. Weighting the heading at 0.70 floors an
identical-heading pair at 0.70, so it can never read as new, while a genuinely
unrelated pair still scores near zero.

### Why 0.25, argued from two specific pairs

**ASB, similarity 0.428.** *"We will experience increases in FDIC insurance
assessments due to the bank failures that occurred in 2023"* → *"We could
continue to experience adjustments in FDIC insurance assessments."* Driver
(FDIC), mechanism (assessment rates) and harm (higher expense) are all
unchanged. What changed is that a stale specific dropped out and a commitment
was hedged — the two most common kinds of routine annual edit. If that counts as
a new risk, "newly disclosed" means nothing.

**BKU, similarity 0.271.** *"Failure by us or third parties to detect or prevent
a breach in information security..."* → *"A cybersecurity incident, which is any
unauthorized occurrence, or series of related unauthorized occurrences, on or
conducted through our information systems..."* Same driver, mechanism and harm.
The FY2023 phrasing tracks the definition of "cybersecurity incident" in Item
106 of Regulation S-K, which the SEC made effective for annual reports covering
fiscal years ending on or after 15 December 2023. This is a **legally mandated
definitional rewrite** — the purest possible case of new words with no new risk.

Counting it as a new disclosure would make the dashboard report a sector-wide
cybersecurity spike in FY2023 that is entirely an artifact of SEC drafting.

**Both pairs share the pattern the threshold has to survive: heavy lexical
churn, zero drift in driver, mechanism or harm.**

**Empirical confirmation.** After the threshold was set, cyber NEW disclosures in
FY2023 came in at 4 — *lower* than FY2022's 8 — while cyber MATERIALLY_REVISED
hit 12, the highest of any year. The mandated rewrite landed in REVISED, not NEW.
At `MATCH_MIN = 0.35` those would have been counted as new.

### Why a low threshold is safe: the three guards

0.25 with naive one-to-one matching would be too loose, and an interviewer would
be right to push on it. It is defensible only alongside:

1. **Mutual best.** A pair is accepted only if each side ranks the other first
   among everything still unmatched.
2. **Runner-up margin of 0.05.** Both sides must beat their second choice.
3. **Review flag** on similarity 0.20–0.40 and on ambiguous items — counted, but
   marked as untrusted.

Accepted pairs are removed and the rounds repeat, so sibling risks inside one
bank-year (two commercial-real-estate items, an information-security item beside
a data-privacy item) compete against each other rather than against a cutoff.
`MATCH_MIN` then only decides the fate of items with no strong counterpart
anywhere.

**Empirical confirmation.** United Bankshares FY2022 contains *"United is subject
to credit risk in its loan portfolio"* (0.317) and *"United is subject to
extensive government regulation"* (0.308) — both **above** `MATCH_MIN` against
FY2023's *"United is subject to liquidity risk"*, purely because they share the
filer's *"United is subject to X"* template. Greedy one-to-one matching would
have bound liquidity risk to credit risk. Mutual-best refused, and the item was
correctly labelled NEW.

### Digits are normalised before comparison

`"As of December 31, 2022"` versus `"...2023"`, or `$3.4 billion` versus
`$3.9 billion`, would otherwise register as a change in every risk factor and
make the entire corpus look rewritten annually. This single step matters more
than the threshold.

### Honest caveat

0.25 was set by reasoning about two pairs, then checked against the full
similarity distribution and the cyber control. It is a judgement, not a
derivation, and should be revisited if the panel or industry changes.

---

## D14 — Matching is greedy-free but not error-free

**Measured on 96 hand-checked pairs, 48 accepted and 48 rejected.**

| Error direction | Rate | 95% CI |
|---|---|---|
| False match (accepted, but different risks) | 8.3% | 3.3–19.6% |
| — excluding splitter artifacts | 2.1% | 0.4–10.9% |
| **Missed match** (rejected, but the same risk) | **43.8%** | **30.7–57.7%** |

**The missed-match rate was initially unmeasurable, and that was a design
defect.** Mutual-best matching never creates a sub-threshold pair — a risk that
fails simply becomes NEW or DROPPED with nothing attached — so an audit sampling
only matched pairs is structurally blind to the error direction that *inflates*
the headline. The audit was rebuilt to carry each unmatched item's best rejected
candidate. The first audit reported a reassuring 6% and said nothing about the
real problem.

### Cause of the missed matches

| Cause | Count of 21 |
|---|---|
| Synonym rewrite | 11 |
| COVID-19 rewording | 6 |
| Other same-risk rewrite | 4 |

**TF-IDF cosine cannot detect a pure synonym rewrite.** *"Increased regulatory
scrutiny"* → *"heightened supervisory attention"* share no words, so similarity
is near zero regardless of threshold or guard. Embeddings would address this at
the cost of introducing an unmeasurable component into the one part of the
pipeline whose accuracy is the project's central claim; that trade was declined.

### Concentration

Atlantic Union FY2021→2022 produced **6 of the 21** missed matches from 10
sampled pairs. It is the highest-churn company-year in the corpus (0.70) and
rewrote its Item 1A wholesale. **The structural-rewrite detector failed to catch
it** — an open defect, recorded rather than hidden. 25 of the 48 rejected pairs
came from the FY2022 transition, so the audit is also over-weighted toward the
COVID-rewording year.

---

## D15 — The headline finding is verified item by item, not asserted

**Decision.** Every risk contributing to the published finding was checked by
hand against the full prior-year risk set of the same company, and the published
number is the verified count.

**Why a corpus-wide rate is not enough.** The 43.8% missed-match rate does not
tell you whether *this* finding survives. It is dominated by synonym rewrites of
existing risks, concentrated in one company-year. The deposit risks are different
in kind — new vocabulary about a new event. That is a plausible argument, and
plausible is not evidence.

**Method.** For each of the 19 FY2023 NEW risks matching a deposit/liquidity
pattern, every risk factor the same company disclosed in FY2022 was listed and
read. Similarity ranking is deliberately not relied on: a synonym rewrite scores
0.000 and sits indistinguishably among unrelated risks, which is precisely the
error being checked for.

**Result: 4 of 19 rejected.**

| Company | Why rejected |
|---|---|
| BANC | *"Funding and Liquidity Risks"* is a category header, not a risk — a splitter artifact. BANC already disclosed a liquidity risk in FY2022 |
| FLG | Category IV prudential standards — a regulatory risk caught by the keyword "liquidity risk" |
| RNST | FY2022 already contained *"We may be adversely affected by the soundness of other financial institutions"*. Same driver, mechanism and harm. **A genuine missed match** |
| SSB | About Basel capital proposals, not deposits. Also a D12-flagged filer with known over-extraction |

Only **one of the four** is the matcher failing. The others are keyword breadth
and a splitter artifact.

**Published numbers, both reported:**

| Basis | Companies | Share | 95% CI |
|---|---|---|---|
| Strict — heading explicitly names uninsured deposits or 2023 bank failures | 8 / 50 | 16.0% | 8.3–28.5% |
| All verified — including generically-worded liquidity additions | 13 / 50 | 26.0% | 15.9–39.6% |
| Unverified keyword count (**not published**) | 17 / 50 | 34.0% | 22.4–47.8% |

Reporting the sensitivity is stronger than choosing one. The unverified 34% is
shown only to make the size of the correction visible.

**What this establishes about D14.** Only 1 of 19 was a genuine missed match,
against a corpus-wide rate of 43.8%. That is evidence the general rate is driven
by rewrites of *existing* risks rather than by new-topic additions — which is
what the headline depends on, and it is now demonstrated rather than assumed.

---

## Open decisions

- **D6 replacement check not yet built** (total-assets discontinuity).
- **Structural-rewrite detector misses Atlantic Union FY2022** (0.70 churn, the
  highest in the corpus). See D14. Name-based
  merger detection missed UMB Financial's 2024 acquisition of Heartland Financial
  because the registrant name never changed.
- **Debt-only registrants.** HSBC USA and Santander Holdings USA have no listed
  common stock; they file because of registered debt. Their Item 1A addresses
  bondholders and references parent-company dynamics no domestic peer has.
  Candidate rule: exclude registrants with no publicly traded common equity.
  Not yet decided.
- **Splitter accuracy re-measurement.** The 90% figure (n=100) predates the
  risk-summary fix and the D11 exclusions. Re-measure on a fresh sample during
  gold-set labelling.
- **Business-model outliers.** Ally Financial (auto lender) and Northern Trust
  (custody bank) sit inside a commercial-bank panel. No mechanical rule cleanly
  separates them, and inventing one to exclude named companies is selection bias
  in disguise. Provisional plan: keep them, and test in Week 4 whether the
  headline finding survives their removal. Reporting that a result holds without
  them is stronger than never having included them.
- **Amended filings (10-K/A).** Recorded in `filing_index.csv` but not merged.
  A 10-K/A frequently restates only part of a document and often omits Item 1A.
  Whether an amendment supersedes the original for this purpose is undecided.
