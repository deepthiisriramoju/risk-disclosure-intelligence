
# Gold set labelling rubric

300 risk factors, labelled by hand. This is the ground truth every accuracy
number in the project is measured against.

Fill in the first six columns. Leave the rest alone.

---

## category  (pick exactly one)

| Key | Value | The risk is about |
|---|---|---|
| `f` | `financial` | **money.** Borrowers not repaying; loan losses; collateral values; deposits leaving; funding costs; access to capital markets; interest rates; securities values; trading |
| `o` | `operational` | **things breaking.** Cyber attacks; system failures; vendors; staff; fraud; failed technology projects; business continuity |
| `r` | `regulatory` | **rules and courts.** Laws and legislation; supervisors and examinations; capital requirements; litigation; fines; compliance |
| `s` | `strategic` | **the plan failing.** Competition; acquisitions and integration; growth plans; falling behind on technology; reputation and public perception |

**Four categories, not eight.** With 300 items, eight categories leaves roughly
37 items each and under ten in the smallest — too few for a per-category metric
anyone should trust. Four gives ~75 each, and far fewer opportunities to drift.

**Sub-types are not lost.** Questions like "which banks newly disclosed a
deposit-concentration risk in FY2023?" are answered by searching risk *heading
text*, which is kept in full. The category label does not have to carry them.

### The rule for the commonest hard case

Roughly one risk in eight is some version of *"a bad economy may hurt us."*
It lists consequences across every category, so it can be argued into any of them.

> **Umbrella macroeconomic risks → `f` (financial).**

Write it down. Apply it every time without rereading the body. You will meet
this risk forty times or more, and flip-flopping on it will damage your
self-agreement score more than any other single thing.

### When two categories both fit

Pick what the risk is *fundamentally* about, not everything it mentions. A cyber
risk that mentions regulatory fines is `operational`. Then add a note. Those
notes are the confusion analysis — they show where the taxonomy is under strain,
and they are worth more than a tidy-looking label set.

## materialised  (`speculative` / `materialised`)

- `speculative` — framed as something that *could* happen
- `materialised` — states something that *has* happened

Look for past tense and concrete events: *"has adversely impacted us in the
past"*, *"in the third quarter of 2022, United Bank received a Needs to Improve
rating"*. If a risk says both, label `materialised` — the disclosure of an
actual event is the more informative fact.

## specificity  (`specific` / `generic`)

- `specific` — names a real event, counterparty, regulator action, place, or number
- `generic` — could appear in any bank's filing with the name swapped

Test: cover the company name. If you cannot tell which bank wrote it, `generic`.

## entities

Named organisations, regulators, laws, or places, semicolon-separated.
Example: `FDIC; Dodd-Frank; Puerto Rico`. Leave blank if none. Do not include
the filing company itself.

## split_ok  (`y` / `n`)

Is this record correctly split — one whole risk factor, heading matching body?
`n` if it is a fragment, truncated, or two risks merged.

This gives you a second, larger measurement of splitter accuracy for free,
from reading you are doing anyway.

## notes

Anything that made you hesitate. Especially valuable: a risk that resisted the
category list, or a case where two labels seemed equally right.

---

## How to work

Do it in blocks of 50 with breaks. Tired labelling is inconsistent labelling,
and your consistency is itself a number you have to report.

Do NOT look at what an LLM would say first. Anchoring destroys the independence
that makes this set worth having.

A week after finishing, run `--relabel 50` and label those again without looking
at your first answers. `--agreement` then reports how often you agreed with
yourself. Expect 80-90%. Publishing that number is what separates measurement
from decoration: a single annotator's inconsistency is a real error source, and
naming it is the honest thing to do.
