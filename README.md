# Risk Disclosure Intelligence

**Every US bank must publish what it is worried about. Nobody tracks how those
worries change.**

This pipeline reads 250 SEC annual reports from 50 US regional banks across five
years, extracts 10,585 individual risk factors, classifies them, and matches each
one against the prior year to identify what is newly disclosed.

Every stage has a measured accuracy figure. That is the point of the project.

### **[▶ Open the live app](https://risk-disclosure-intelligence-mqfjcxjvd2xbnqzspfjbyt.streamlit.app/)**

*Explore the finding, drill into any of the 50 banks year by year, search all
10,585 risk factors, and see the pipeline's own error rates on the Quality tab.*

> **[ dashboard GIF goes here — record after the LLM run completes ]**

---

## The finding

**Between 8 and 13 of 50 regional banks newly disclosed a deposit-concentration
or FDIC-assessment risk in their FY2023 annual report** — filed after the March
2023 bank failures. The signal appears in one year and disappears.

Share of the panel newly disclosing a risk matching each pattern:

| Signal | FY2022 | FY2023 | FY2024 | FY2025 |
|---|---|---|---|---|
| **Deposits / liquidity** | 4% | **34%** | 2% | 0% |
| Cyber | 14% | 8% | 2% | 2% |
| AI | 0% | 10% | 26% | 26% |
| Interest rates | 16% | 20% | 0% | 0% |

Deposits spike sharply and uniquely in FY2023. Cyber *declines*. AI peaks two
years later. **That divergence is what rules out an artifact** — a filing-format
change or a matching quirk would move every signal in the same year.

**Aggregate counts hid it.** FY2023 shows 163 newly disclosed risks in total, the
second *lowest* of four years. The effect is 19 risks concentrated in one topic.

All 19 were then checked by hand against the full prior-year risk set of the same
company. Four were rejected. The published figure is the verified count:

| Basis | Companies | 95% CI |
|---|---|---|
| Strict — heading names uninsured deposits or 2023 bank failures | **8 / 50** | 8.3–28.5% |
| All verified — including generic liquidity additions | **13 / 50** | 15.9–39.6% |
| Unverified keyword count — *not published* | 17 / 50 | 22.4–47.8% |

---

## Measured accuracy

Full detail, methods and failure taxonomies: **[EVALUATION.md](EVALUATION.md)**

| Stage | Metric | Result | Sample |
|---|---|---|---|
| Ingestion | filings retrieved | 275 / 275 | all |
| Extraction | Item 1A located | **100%** | all |
| Splitting | correctly split risk factors | **89.3%** | n=300, hand-labelled |
| Classification | keyword baseline | 82.7% acc / 0.814 macro F1 | n=300 |
| Classification | **LLM** | **93.7% acc / 0.930 macro F1** | n=300 |
| Classification | **lift over baseline** | **+11.6 macro F1 points** | n=300 |
| Matching | false match rate | 8.3% | n=48 |
| Matching | **missed match rate** | **43.8%** | n=48 |

**Errors compound.** 100% × 89.3% × 93.7% is roughly **84% end to end**.
Stage-level figures are the honest way to expose that; a single headline number
would hide it.

### The ground truth is hand-made

300 risk factors were labelled by hand, stratified across all 50 companies and
250 company-years. Every accuracy figure above is measured against them.

**No LLM was used to build the gold set.** A model graded against another model
measures agreement, not accuracy — two systems sharing a blind spot agree
perfectly and are both wrong.

### The baseline exists so the LLM's number means something

A keyword classifier was written first, by hand, without reference to the gold
set. It scores 82.7%. Reporting an LLM at 93.7% without that comparison would say
nothing about whether the model earned its cost.

A prediction was recorded **before** the LLM ran: *its largest gain will be
operational recall*, because the baseline's errors there were one-directional and
therefore a vocabulary-coverage problem rather than a taxonomy problem.

| | baseline | LLM |
|---|---|---|
| operational recall | 0.689 | **0.934** |

---

## Two things the measurements caught

**A bug that deleted words.** A filter added to strip page headers was removing
any short run repeating three or more times — which, in filers that split text
into thousands of tiny runs, included the words *"of"*, *"we"*, *"at"* and
*"is"*. One filing lost 2,211 runs, another 8,363. Sentences became
*"An impairment goodwill... amortizable affect our condition"*.

Nothing errored. No process failed. It was found only by expanding the records
behind ten flagged audit items.

**An audit that was blind by construction.** The first matching audit sampled
only accepted pairs and reported a comfortable 6.0% false-match rate. But
mutual-best matching never *creates* a sub-threshold pair, so the audit could not
see missed matches — the error direction that inflates the headline. Rebuilt to
carry each unmatched item's best rejected candidate, the missed-match rate came
back at **43.8%**.

Both are documented rather than quietly fixed. They are the argument for
measuring every stage instead of only the model.

---

## For a non-technical reader

**[BRIEF.md](BRIEF.md)** — a one-page brief written for a risk, compliance or
competitive-intelligence audience. What was found, what it means in practice,
and what the data cannot tell you. No methodology.

---

## How the hard decisions were made

Full reasoning: **[DECISIONS.md](DECISIONS.md)** — 15 entries, each with what was
rejected and why.

**When is a reworded risk the same risk?** The similarity threshold determines
every "newly disclosed" number. It was set at 0.25 by reading specific pairs, and
validated against a natural control: the SEC introduced Item 106 of Regulation
S-K for fiscal years ending on or after 15 December 2023, mandating specific
cybersecurity wording. Filers rewrote — new words, no new risk. A correct
threshold must classify that as revision, not disclosure.

| FY | Cyber NEW | Cyber REVISED |
|---|---|---|
| 2022 | 8 | 9 |
| 2023 | **4** | **12** |

It did. At a threshold of 0.35 those rewrites would have been counted as new
disclosures and the dashboard would report a phantom sector-wide cyber spike.

**Which companies are in the panel?** A mechanical rule — asset band, industry
code, complete five-year filing history — with every exclusion logged and
reasoned. Six companies were removed under stated rules.

---

## Known limitations

**The panel is survivors.** The asset filter reads a December 2025 balance sheet,
so a bank had to be alive in 2026 to appear. Silicon Valley Bank, Signature Bank
and First Republic — the banks that actually failed — cannot be in this study.
Every finding is about *surviving, calendar-year, conventionally-filing peers*.

**43.8% of items counted as newly disclosed are reworded existing risks.**
TF-IDF cosine cannot see a pure synonym rewrite: *"increased regulatory
scrutiny"* and *"heightened supervisory attention"* share no words. The rate is
concentrated in one company-year and in COVID-era rewording. **This is why the
headline finding was verified item by item rather than taken from the aggregate.**

**Splitting is imperfect and its errors propagate.** At 89.3%, roughly one risk
factor in nine is mis-split. Those errors are present in the gold set and in
everything scored against it. Three of the four rejected deposit-risk candidates
trace to splitting or keyword breadth, not to the matcher.

**The gold set is one annotator.** Inter-annotator agreement would need two or
more labellers. Intra-annotator agreement has not yet been measured.

**Three filers are excluded for document structure**, and two more over-split on
bullet-delimited risk summaries.

---

## Stack

Python · DuckDB · SQL · Gemini API · Power BI · Streamlit

No scikit-learn. TF-IDF cosine, the evaluation harness and the Wilson intervals
are implemented directly, so every number can be traced to the line that produced
it.

---

## Repository

```
src/
  sec_client.py          rate-limited, cached, retrying EDGAR client
  build_universe.py      applies the universe rule; writes the exclusion log
  download_filings.py    immutable raw layer with checksums and fetch timestamps
  profile_filings.py     measures document structure BEFORE the parser is written
  extract_item1a.py      locates Item 1A; scores candidates against three shapes
  split_risk_factors.py  splits sections into individual risk factors
  build_gold_set.py      draws and manages the 300-item hand-labelled set
  label_gold_set.py      keyboard labelling tool
  baseline_keywords.py   hand-written keyword classifier
  extract_llm.py         batched LLM classification, versioned prompts
  evaluate.py            precision/recall/F1, confusion matrix, Wilson intervals
  match_yoy.py           year-over-year matching, calibration, audit
  find_signal.py         quantifies a disclosure signal across the panel
  verify_new.py          verifies specific NEW risks against the prior year
  build_warehouse.py     DuckDB warehouse: raw -> clean -> mart + quarantine
  build_app_data.py      slim extract the deployed app reads
app/
  streamlit_app.py       the live dashboard
  data/                  headings and labels only, no bodies -- committed so
                         Streamlit Cloud can deploy from the repo
sql/
  analysis.sql           the questions the project set out to answer
prompts/
  category_v1.txt        versioned; every output row records which version ran
data/
  gold/                  the 300 hand-labels — the ground truth
  universe/              who is in, who is out, and why
```

Raw filings and intermediate outputs are excluded and regenerable.

---

## Reproducing

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name your@email.com"   # SEC requires this
export GEMINI_API_KEY="..."

cd src
python build_universe.py          # 828 candidates -> 50 companies
python download_filings.py        # 250 filings, ~6 req/sec
python extract_item1a.py          # 100% parse rate
python split_risk_factors.py      # 10,585 risk factors
python baseline_keywords.py --gold && python evaluate.py --pred baseline_gold.csv --name keywords
python extract_llm.py --gold && python evaluate.py --pred llm_gold.csv --name llm --compare baseline_gold.csv
python match_yoy.py --calibrate   # read the pairs before setting thresholds
python match_yoy.py --run
python find_signal.py --preset deposits --show
python build_warehouse.py         # raw -> clean -> mart, with quarantine
```

The warehouse is DuckDB. Rows failing a validation rule are moved to a
`quarantine` table with a reason rather than dropped — a discarded row is
invisible, a quarantined row is a number you can report. Four integrity checks
run against the warehouse itself rather than against the Python that built it,
because a count computed by the loader cannot reveal a bug in the loader.

The pipeline is reproducible **from the stored raw layer**, not from the live
API. Companies file amendments and EDGAR reindexes, so a re-fetch months later
may return different documents. That is why raw is timestamped and immutable.

---

**[▶ Open the live app](https://risk-disclosure-intelligence-mqfjcxjvd2xbnqzspfjbyt.streamlit.app/)** · **[EVALUATION.md](EVALUATION.md)** ·
**[DECISIONS.md](DECISIONS.md)**

*Data: SEC EDGAR, public filings, accessed under the SEC's fair-access rules
(descriptive User-Agent, rate limit observed).*
