# EVALUATION

How well this pipeline works, measured rather than asserted.

Every number below comes from a run against the real corpus. Where two
measurements of the same thing disagree, both are shown and the more
conservative one is reported.

---

## Summary

| Stage | Metric | Result | Sample |
|---|---|---|---|
| Ingestion | filings downloaded | **275 / 275** | all |
| Extraction | Item 1A located | **275 / 275** = 100% | all |
| Splitting | correctly split risk factors | **89.3%** | n=300, hand-labelled |
| Classification | keyword baseline accuracy | **82.7%** | n=300 |
| Classification | keyword baseline macro F1 | **0.814** | n=300 |
| Classification | LLM accuracy | **93.7%** | n=300 |
| Classification | LLM macro F1 | **0.930** | n=300 |
| Classification | **lift over baseline** | **+11.6 macro F1 points** | n=300 |
| Matching | false match rate | **8.3%** | n=48 |
| Matching | missed match rate | **43.8%** | n=48 |
| Warehouse | rows quarantined with a reason | **1.50%** | all |
| Fault injection | injected faults detected | **7 of 8** | 8 injected |
| Gold set | **annotator self-agreement** | **88.0%** | n=50 |
| **Finding** | banks newly disclosing a deposit risk, FY2023 | **8–13 of 50** | verified by hand |

Corpus after exclusions: **50 companies, 250 filings, 10,585 risk factors.**

---

## 1. Ingestion

275 of 275 filings retrieved from SEC EDGAR, zero failures, zero rate-limit
retries at 6 requests/second against a published ceiling of 10.

Every document is stored exactly as received with source URL, UTC fetch
timestamp, SHA-256 and byte count. Nothing downstream writes to the raw layer,
so any parser-versus-source disagreement is resolvable against the bytes the SEC
actually sent.

**Reproducibility, stated precisely.** The pipeline is reproducible *from the
stored raw layer*, not from the live API. Companies file amendments and EDGAR
reindexes; a re-fetch months later may return different documents. This is why
raw is timestamped and immutable rather than re-fetched on demand.

---

## 2. Item 1A extraction — 100%

**275 / 275 filings**, every fiscal year at 100%.

That figure means every filing produced a section of plausible length
terminating at a real section boundary. **It does not mean every section
contains exactly the right text.** Two filings (both from an excluded company)
still run past their intended terminator.

### How it got there

The first working version scored 32%. The gap was closed by diagnosing
individual documents, not by loosening thresholds — the median section length
barely moved (78,242 → 78,477 chars) while the rate rose, which is what a
genuine fix looks like. Raising a rate by widening acceptance criteria produces
a number that measures the criteria, not the parser.

### Failure taxonomy — five causes, all found by reading documents

| # | Cause | Example | Detection |
|---|---|---|---|
| 1 | Cross-reference mistaken for section **start** | *"The risks listed **below**..."* penalised as a cross-reference | HBAN FY2023/24 failed outright |
| 2 | Cross-reference mistaken for section **end** | *"...refer to Item 1C"* inside the risk factors | FITB FY2025 ended mid-sentence at 23,068 chars |
| 3 | Words split across inline spans | `UNRESOLVED` → `UNRESOLVE D` | WSBC section ran to end of document |
| 4 | Multiple punctuation in headings | `ITEM 1B. - UNRESOLVED STAFF COMMENTS` | AUB FY2021 reached 484,295 chars |
| 5 | Items filed out of numerical order | Item 1A placed **after** Items 1B and 2 | PFS FY2022 had no downstream terminator |

None was a deep parsing problem. All five were assumptions about how filers
punctuate and structure documents, and all five were only findable by reading
the actual bytes.

### Design decisions the measurement forced

Profiling all 275 documents *before* writing the parser falsified two
approaches that looked reasonable:

- **Find the section by HTML structure.** Heading tags appear in **2 of 275**
  filings; median `<b>`/`<strong>` count is **zero**. Emphasis is applied with
  CSS on `<span>` elements. Structural detection is unavailable.
- **Take the last "Item 1A" match.** The phrase occurs 2–11 times per filing.
  The last occurrence sits at a median of 40.8% through the text and as deep as
  85.7%, because it is usually a cross-reference past the real section. Item 1B
  is absent after the last match in **211 of 275** filings.

A third finding changed the terminator logic: Item 1C (Cybersecurity) is absent
from **111 of 275** filings — exactly 55 companies × 2 years. The SEC introduced
it for FY2023 filings, so the section's end marker changes halfway through the
study window.

---

## 3. Risk-factor splitting — 89.3%

**n = 300, hand-labelled by the author** while building the gold set.

Each gold-set item carried a `split_ok` judgement, so this measurement came free
from reading already being done.

### Three measurements, one reported

| Measurement | Result | n | Labelled by |
|---|---|---|---|
| Split audit | 90% | 100 | LLM, against a written rubric |
| Gold set, partial | 82.7% | 150 | author |
| **Gold set, complete** | **89.3%** | **300** | **author** |

The 100-item audit was labelled by an LLM. It scored the splitter 7 points above
the partial human count on work the LLM's own tooling had produced. The
human-labelled figure at full sample size is the one reported.

The 82.7% at n=150 reflects over-strict early labelling: display truncation in
the labelling tool was initially recorded as a splitting error. Once the rule was
clarified — *check where the body starts, not where the display ends* — the rate
settled.

### Splitter failure taxonomy

| Cause | Status |
|---|---|
| Running page headers read as risk headings (`27 Fifth Third Bancorp`) | fixed — detected by digit-normalised repetition |
| Risk Factor Summary entries split as risks, duplicating every risk | fixed — summary block detected and skipped (present in 57 of 250 filings) |
| Styled cross-references read as headings (`Industry and Competition`) | fixed — continuation detection |
| Headings not detected where styling is absent | 3 companies excluded — see D11 |
| Bullet-delimited summaries inflate counts | **open** — affects EBC and CUBI |

### A bug the measurement caught that nothing else would have

The furniture filter — added to remove page headers — was deleting any short run
repeating three or more times. In filings that split text into thousands of tiny
runs, that included the words **"of"**, **"we"**, **"at"**, **"is"**.

Popular lost 2,211 runs; Mechanics Bancorp lost 8,363. The result was text like:

> ~~"An impairment **of** goodwill... amortizable **intangible assets could**
> affect our **financial** condition"~~
> → *"An impairment goodwill... amortizable affect our condition"*

**No error was raised. No process failed.** Sentences were quietly corrupted, and
would have reached the gold-set labelling and the LLM as broken text. It was
found only by expanding the underlying records behind ten flagged audit items.

This is the failure mode the project was built to catch: output that is
plausible, complete-looking, and wrong.

---

## 4. Category classification — keyword baseline

**Accuracy 0.827 (95% CI 0.780–0.865), macro F1 0.814, n = 300.**

The baseline is hand-written keyword rules, not fitted to the gold set. A
baseline tuned on the evaluation data is a model evaluated on its own training
data and would beat any comparison for the wrong reason.

### Floors and context

| Reference point | Accuracy |
|---|---|
| Always predict the commonest class (`financial`, 39.3%) | 0.393 |
| **Keyword baseline** | **0.827** |
| **LLM (Gemini 2.5 Flash, prompt v1)** | **0.937** |

Accuracy alone is misleading on imbalanced data, which is why macro F1 — the
unweighted mean across classes, so a small class counts as much as a large one —
is the headline figure.

### Per class

| class | precision | recall | F1 | support |
|---|---|---|---|---|
| financial | 0.823 | 0.907 | 0.863 | 118 |
| operational | 0.955 | 0.689 | 0.800 | 61 |
| regulatory | 0.829 | 0.853 | 0.841 | 68 |
| strategic | 0.732 | 0.774 | 0.752 | 53 |

### Confusion matrix (rows = truth, columns = prediction)

| | financial | operational | regulatory | strategic |
|---|---|---|---|---|
| **financial** | 107 | 0 | 5 | 6 |
| **operational** | **11** | 42 | 2 | 6 |
| **regulatory** | 7 | 0 | 58 | 3 |
| **strategic** | 5 | 2 | 5 | 41 |

### What the matrix says

`operational` has precision 0.955 and recall 0.689 — when the baseline says
operational it is nearly always right, but it misses 31% of operational risks
and sends 11 of them to `financial`.

The error is **one-directional**, which points to keyword *coverage* rather than
a category boundary problem: some operational risks use vocabulary the keyword
list does not contain, and the stated tie-break rule assigns unmatched items to
`financial` (3 items, 1.0%, matched no keyword at all).

**Stated prediction, recorded before the LLM was run:** the LLM's largest gain
will be operational recall.

10% of baseline decisions were made on a margin of one keyword hit or fewer.
Those near-ties are where a language model should add the most value.

### Sample-size stability

Measured at n=150 and again at n=300:

| n | accuracy | macro F1 |
|---|---|---|
| 150 | 0.827 | 0.808 |
| 300 | 0.827 | 0.814 |

Identical accuracy to three decimal places; the interval narrowed from ±6 to
±4.3 points. The estimate is stable and labelling did not drift as the annotator
sped up.

---

---

## 5. Category classification — LLM

**Accuracy 0.937 (95% CI 0.903–0.959), macro F1 0.930, n = 300.**

Gemini 2.5 Flash, prompt version `v1`, temperature 0, 50 risk factors per
request with an enforced JSON response schema. **Parse failure rate 0.00%** —
no malformed or mis-sized responses.

**Batch size was tuned against the free-tier daily quota, not against accuracy.**
At 20 per request the corpus needs 530 calls; at 50 it needs 212. Scored on the
same 300 gold items, batch 20 gave 0.913 and batch 50 gave 0.937 — a 2.4-point
difference sitting inside both confidence intervals (0.876–0.940 and
0.903–0.959). **No claim is made that larger batches are better**, only that they
are not worse, which is what the quota constraint required.

### Against the baseline

| | LLM | keywords | lift |
|---|---|---|---|
| accuracy | 0.937 | 0.827 | **+11.0 pts** |
| macro F1 | 0.930 | 0.814 | **+11.6 pts** |

The LLM wins on every class, so there is no field where the simpler method
should be preferred. Had the baseline won anywhere, the baseline would be used
for that field and reported as such.

### Per class

| class | precision | recall | F1 | support | F1 lift |
|---|---|---|---|---|---|
| financial | 0.957 | 0.949 | 0.953 | 118 | +9.0 |
| operational | 0.919 | 0.934 | 0.927 | 61 | **+12.7** |
| regulatory | 0.931 | 0.985 | 0.957 | 68 | +11.7 |
| strategic | 0.918 | 0.849 | 0.882 | 53 | **+13.0** |

### Confusion matrix (rows = truth, columns = prediction)

| | financial | operational | regulatory | strategic |
|---|---|---|---|---|
| **financial** | 112 | 2 | 2 | 2 |
| **operational** | 3 | 57 | 0 | 1 |
| **regulatory** | 0 | 0 | 67 | 1 |
| **strategic** | 2 | 3 | 3 | 45 |

### The prediction, recorded before the run

Section 4 stated, before any LLM output existed:

> *"the LLM's largest gain will be operational recall."*

| | baseline | LLM | change |
|---|---|---|---|
| operational recall | 0.689 | **0.934** | **+24.5 pts** |

Confirmed, and the diagnosed mechanism held. The baseline's error was
one-directional — 11 operational risks assigned to `financial` because the
keyword list lacked the vocabulary and the tie-break rule sends unmatched items
to `financial`. The LLM reduces that to 3. This was a coverage problem, not a
taxonomy problem, and the fix behaved as predicted.

`strategic` gained most in F1 (+13.0). It was the baseline's weakest class at
0.752 and remains the weakest for the LLM at 0.882, with recall 0.849 — the
lowest of the four.

### Where the LLM is weaker

`regulatory` recall is 0.985 against precision 0.931 — the LLM slightly
*over*-predicts regulatory, absorbing 2 financial and 3 strategic items. This is
the mirror image of the baseline's bias toward `financial`. Both systems have a
preferred class; neither is neutral.

### Cost and reproducibility

Batching 20 items per request turns 10,585 individual calls into roughly 530,
which fits inside a free-tier daily quota. Temperature is 0 so the same input
returns the same label, and every output row records the prompt version, model
name and run timestamp — without which rows produced by different prompt
versions would be silently incomparable.

---

## 6. Year-over-year matching

Each risk factor is matched to its counterpart in the prior year and labelled
NEW, CARRIED FORWARD, MATERIALLY REVISED, DROPPED or AMBIGUOUS. Thresholds and
guards are in DECISIONS.md D13.

| Label | Count | Share |
|---|---|---|
| CARRIED_FORWARD | 6,789 | 74.6% |
| MATERIALLY_REVISED | 1,044 | 11.5% |
| NEW | 630 | 6.9% |
| DROPPED | 624 | 6.9% |
| AMBIGUOUS | 19 | 0.2% |

1.5% of rows carry a review flag. Banks copy forward with light edits and change
a handful of risks a year, which is the expected shape.

### Accuracy — measured in both error directions

96 pairs hand-checked, deliberately half accepted and half rejected.

| Direction | Rate | 95% CI |
|---|---|---|
| False match | 8.3% (4/48) | 3.3–19.6% |
| — matcher only, excluding splitter artifacts | 2.1% (1/48) | 0.4–10.9% |
| **Missed match** | **43.8% (21/48)** | **30.7–57.7%** |

**A missed match is the expensive error.** It counts one risk as both NEW and
DROPPED, inflating the headline in the direction the project's claim runs.

**It was initially unmeasurable, and that was a design defect.** Mutual-best
matching never produces a sub-threshold pair, so an audit sampling only matched
pairs is structurally blind to it. A first audit reported a comfortable 6.0%
false-match rate and said nothing about the real problem. The audit was rebuilt
to carry each unmatched item's best rejected candidate.

| Cause of missed match | Count of 21 |
|---|---|
| Synonym rewrite | 11 |
| COVID-19 rewording | 6 |
| Other same-risk rewrite | 4 |

TF-IDF cosine cannot see a pure synonym rewrite — *"increased regulatory
scrutiny"* and *"heightened supervisory attention"* share no words. Embeddings
would fix it and would introduce an unmeasurable component into the one part of
the pipeline whose accuracy is the central claim.

**Concentration:** Atlantic Union FY2021→2022 supplied 6 of the 21 from 10
sampled pairs, and 25 of the 48 rejected pairs came from the FY2022 transition.
The rate is not evenly distributed.

### The threshold, validated against a natural control

The SEC introduced Item 106 of Regulation S-K for fiscal years ending on or after
15 December 2023, mandating specific "cybersecurity incident" wording. Filers
rewrote their cyber risk factors to track it — new words, no new risk.

| FY | Cyber NEW | Cyber MATERIALLY_REVISED |
|---|---|---|
| 2022 | 8 | 9 |
| 2023 | **4** | **12** |

The mandated rewrite landed in REVISED, and cyber NEW disclosures **fell** in
FY2023. At `MATCH_MIN = 0.35` those rewrites would have been counted as new
disclosures and the dashboard would report a phantom sector-wide cyber spike.

---

## 7. The finding

**Between 8 and 13 of 50 regional banks newly disclosed a deposit-concentration
or FDIC-assessment risk in their FY2023 10-K.**

### The signal is specific to deposits, and specific to FY2023

Share of the panel newly disclosing a risk matching each pattern:

| Signal | FY2022 | FY2023 | FY2024 | FY2025 |
|---|---|---|---|---|
| **Deposits / liquidity** | 4% | **34%** | 2% | 0% |
| Cyber | 14% | 8% | 2% | 2% |
| AI | 0% | 10% | 26% | 26% |
| Interest rates | 16% | 20% | 0% | 0% |

Deposits spike sharply and uniquely in FY2023 and then vanish. Cyber *declines*.
AI peaks two years later. Rates are elevated across FY2022–23, consistent with
the tightening cycle rather than a single event.

**That divergence is what rules out an artifact.** A filing-format change or a
matching quirk would move every signal in the same year.

### Aggregate counts hid it

FY2023 shows 163 newly disclosed risks in total — the second *lowest* of four
years. The effect is 19 risks out of 163.

This is the argument for retaining full risk **heading text** rather than only a
four-way category label.

### Verified, not asserted

All 19 keyword hits were checked by hand against the full prior-year risk set of
the same company. Four were rejected (DECISIONS.md D15).

| Basis | Companies | Share | 95% CI |
|---|---|---|---|
| Strict — heading names uninsured deposits or 2023 bank failures | 8 / 50 | 16.0% | 8.3–28.5% |
| All verified — including generic liquidity additions | 13 / 50 | 26.0% | 15.9–39.6% |
| Unverified keyword count, **not published** | 17 / 50 | 34.0% | 22.4–47.8% |

Only 1 of the 4 rejections was a matcher error. **Only 1 of 19 was a genuine
missed match, against a corpus-wide rate of 43.8%** — evidence that the general
rate is driven by rewrites of existing risks rather than new-topic additions.

### A secondary observation

Five banks — ASB, EWBC, FNB, UCB, WBS — filed near-identical wording:

> *"The proportion of our deposit account balances that exceed FDIC insurance
> limits may expose [the Bank] to enhanced liquidity risk in times of financial
> distress."*

Template propagation through the industry, visible in the data. Querying the
warehouse for headings shared across three or more banks shows this is the
normal state rather than an anomaly: seven banks share a vendor-dependency
sentence, seven an information-accuracy sentence, five an anti-takeover sentence.
**Peer-disclosure benchmarking is therefore measuring drafting conventions as
much as underlying exposure.**

---

## 8. The warehouse, and what it discarded

Three layers in DuckDB: **raw** loads every source verbatim, **clean** types and
validates, **mart** is a star schema of one fact table and three dimensions.

Rows failing a validation rule move to a `quarantine` table with a reason rather
than being deleted. **A discarded row is invisible; a quarantined row is a number
you can report.**

| Quarantine reason | Records | Share of raw |
|---|---|---|
| `prior_risk_quarantined` | 63 | 0.60% |
| `duplicate_heading_in_filing` | 62 | 0.59% |
| `empty_heading` | 34 | 0.32% |
| **Total** | **159** | **1.50%** |

`short_body` returned **zero** — independently confirming the splitter's report
that no risk factor has a body under 200 characters.

### Integrity checks, run against the warehouse

Four checks that must return zero, executed as SQL against the built database
rather than against the Python that filled it — a count computed by the loader
cannot reveal a bug in the loader.

**The first run failed.** `orphan prior_risk_id` returned 4: matched pairs
pointing at a prior-year risk that quarantine had removed. Fixed by nulling the
dangling pointer, quarantining the row with its reason, and adding a
`prior_risk_missing` flag.

| Check | Result |
|---|---|
| orphan `prior_risk_id` | 0 |
| match with a quarantined prior risk | 4 *(known, flagged)* |
| duplicate `risk_id` | 0 |
| excluded company leaked into the mart | 0 |
| NEW label with a prior risk attached | 0 |

### The headline finding, reproduced independently

The deposit result was computed a second time in SQL against the warehouse,
using a pattern written separately from the Python. Result: **12 banks, FY2023
only.** Nothing in any other year.

That sits between the strict hand-verified count (8) and the broad one (13).
**Two implementations in different languages agree on the shape**, which rules
out a bug in either path having produced the effect.

---

## 9. Fault injection

Every quality check in this project was written in response to a problem that had
already occurred. That is the wrong direction of evidence: a check never tested
against a fault it was not written for is a hope, not a control.

Eight faults were introduced deliberately into a copy of the data, one at a time.

**Detection rate: 7 of 8 (88%).**

| Fault | Detected by |
|---|---|
| Filing truncated to half its risk factors | orphan match count 63 → 93 |
| Ten bodies emptied, headings kept | quarantine `short_body` 0 → 10 |
| Five risk factors duplicated | quarantine `duplicate_heading` 40 → 45 |
| Malformed JSON | load error, filing count drop |
| 500 classifications blanked, rows retained | missing-category count 0 → 500 |
| 50 matches pointing at non-existent risks | orphan count 63 → 113 |
| Fiscal year set to 2099 | domain check |
| **All classifications shifted by one row** | **nothing** |

The faults were chosen to be quiet rather than loud. A crash announces itself;
the dangerous fault leaves the pipeline reporting success on wrong data.

### The one that escaped, and why it matters

**`shifted_labels` passed through in complete silence.** Every classification
moved down one row: correct row count, correct category distribution, plausible
output — and every single label attached to the wrong risk factor.

No structural check can see this. Nothing about the counts changed. Completeness
is unaffected. Referential integrity is intact.

**The only thing that catches it is the gold set.** Scoring predictions against
hand-labelled ground truth is the one check that asks whether a label is
*correct* rather than whether it *exists*.

That is the argument this project is built on, demonstrated rather than asserted:
structural checks verify that data is *well-formed*; only ground truth verifies
that it is *right*.

## 10. The gold set

**300 risk factors, hand-labelled by the author.**

Stratified across all 50 companies and 250 company-year cells, so the sample
reflects the panel rather than its most verbose members — one filer discloses
105 risk factors per year against a median of 40, and naive random sampling
would let it dominate.

| Category | Count | Share |
|---|---|---|
| financial | 118 | 39.3% |
| regulatory | 68 | 22.7% |
| operational | 61 | 20.3% |
| strategic | 53 | 17.7% |

### Four categories, not eight

The original taxonomy had eight (credit, liquidity, market, operational,
regulatory, strategic, reputational, other). It was cut to four during
labelling.

**Why:** with 300 items, eight categories yields ~37 items each and fewer than
ten in the smallest — too few to support a per-category precision or recall
figure anyone should trust. Eight also multiplies the chances of annotator drift
on umbrella risks: the very common *"a bad economy may hurt us"* risk can be
argued into credit, market, strategic or other, and a single annotator will not
resolve it identically 40 times.

**What is lost:** the panel cannot be described as "liquidity concerns rose while
credit concerns fell" — only as "financial risks rose."

**What is not lost:** sub-type questions — *which banks newly disclosed a
deposit-concentration risk in FY2023?* — are answered by searching risk heading
text, which is retained in full. The category label does not have to carry them.

### Standing rules used during labelling

Written before labelling and applied without revisiting, because consistency
matters more than case-by-case optimality:

- Umbrella macroeconomic risks → `financial`
- Umbrella climate risks → `financial`; climate **regulation** → `regulatory`
- Government policy and shutdown risks → `regulatory`
- Judge from the heading; read the body only when the heading is genuinely
  ambiguous

### Intra-annotator agreement — the ceiling on every number above

**88.0% (44/50) on category. 90.0% (45/50) on split correctness.**

Fifty gold-set items were relabelled twelve days after the original pass,
without reference to the first answers, using the same written rules.

#### Why this matters more than it looks

The gold set is not truth. It is one annotator's judgement, and this measures how
reliable that judgement is.

| | |
|---|---|
| LLM accuracy against the gold set | **93.7%** |
| Annotator agreement with themselves | **88.0%** |

**The model is more consistent with the labels than the labeller is.** That means
93.7% is at or near the ceiling this evaluation can measure — pushing the score
higher would increasingly mean fitting the noise in the ground truth rather than
improving the classification.

Reporting a model score without the annotator's own error rate implies the
ground truth is perfect. It is not, and the size of its imperfection is now
known.

#### Where the disagreements fall

| First pass | Second pass | Count |
|---|---|---|
| strategic | operational | 2 |
| strategic | financial | 1 |
| financial | regulatory | 1 |
| regulatory | strategic | 1 |
| operational | strategic | 1 |

**Four of the five involve `strategic`**, on one side or the other.

That is the fourth independent measurement pointing at the same place:

| Measurement | `strategic` result |
|---|---|
| Keyword baseline F1 | 0.752 — lowest of four classes |
| LLM F1 | 0.882 — lowest of four classes |
| LLM recall | 0.849 — lowest of four classes |
| Annotator self-agreement | 4 of 5 disagreements involve it |

**This is a taxonomy problem, not a model problem.** The boundary between
"the plan failing" and the other three categories is genuinely ambiguous —
a competition risk that mentions reduced earnings can be argued into
`financial`, and a reputational risk into `operational`. Both the human and both
classifiers struggle in the same place, which is what a definitional problem
looks like rather than a learning one.

#### The split_ok result reveals a labelling drift

All five disagreements run **the same direction**: `n` on the first pass, `y` on
the second. Never the reverse.

The cause is known. The labelling tool truncates long bodies at roughly 1,100
characters for display, so a correctly-split risk factor appears to end
mid-sentence. Early in the first pass that was read as a splitting error; the
rule was clarified partway through — *check where the body starts, not where the
display ends* — and applied consistently thereafter.

**So the reported splitter accuracy of 89.3% is likely a slight
underestimate.** It is reported unchanged rather than revised upward, because
adjusting a measurement after seeing which direction the error runs is how
metrics stop being measurements.

#### Limitation

This is *intra*-annotator agreement — one person against themselves. The proper
measure is *inter*-annotator agreement across two or more labellers, which would
also allow disagreements to be adjudicated into a cleaner gold set. With a single
annotator, consistency is measurable but correctness is not.

---

## 11. Known limitations

**The gold set is not truth.** It is one annotator's judgement, and that
judgement is 88.0% self-consistent (section 10). The LLM agrees with the labels
more often (93.7%) than the labeller agrees with themselves, so the reported
accuracy is at or near the ceiling this evaluation can measure.

**Single annotator.** Inter-annotator agreement — the proper measure — would
need two or more labellers. Intra-annotator agreement is the weaker substitute.

**No confusion notes were recorded.** The labelling tool offered a free-text note
on every item; zero were used across 300. Without them, no claim can be made that
the taxonomy was tested for strain. The `strategic` boundary in particular
(F1 0.752, lowest of the four) is unexamined.

**Splitting is imperfect and its errors propagate.** At 89.3%, roughly one risk
factor in nine is mis-split. Those errors are present in the gold set and in
everything scored against it.

**Two filers still over-split.** EBC and CUBI use bullet-delimited risk
summaries the summary-detection rule does not catch, inflating their counts to
76–87 against a panel median of 40. Documented rather than fixed, because each
additional filer-specific rule has historically regressed other filers.

**Errors compound.** A 100% parse rate feeding an 89.3% splitter feeding a
91.3% classifier is roughly **82% end to end**. Stage-level figures are the
honest way to expose that; a single headline number would hide it. Note that
the classifier was scored on gold-set items that include mis-split records, so
its 91.3% already absorbs some splitting error rather than sitting cleanly on
top of it.

---

## 12. Corpus scope

Six companies excluded, each under a stated rule (see DECISIONS.md D5, D6, D11):

| Reason | Companies |
|---|---|
| Non-December fiscal year end | Axos |
| Registrant became a different company mid-window | Mechanics Bancorp, Beacon Financial |
| Document structure the splitter cannot handle | UMB Financial, Texas Capital, First Horizon |

Three companies retained with a caveat (D12): Flagstar, SouthState, WAFD — each
made a material acquisition inside the window.

**The compound effect matters more than any single exclusion.** The asset filter
reads a December 2025 balance sheet, so a bank had to be alive in 2026 to appear
at all: Silicon Valley Bank, Signature Bank and First Republic — the banks that
actually failed — cannot be in this study.

**The panel is therefore banks that survived to 2026, report on a calendar year,
did not restructure, and format their filings conventionally.** Every finding is
a statement about disclosure behaviour among conventionally-filing surviving
peers, not about the regional banking sector.

---

## 13. What is not yet measured

- LLM classification of the full corpus — gold set only so far (300 of 10,585)
- LLM classification of the full corpus — 2,850 of 10,585 done; the free-tier
  daily quota is the binding constraint
- Structural-rewrite detection: Atlantic Union FY2022 (0.70 churn) is missed
- A shifted-label fault is undetectable by any structural check; only the gold
  set catches it, and the gold set covers 300 of 10,585 risk factors
