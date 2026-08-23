"""
Split each Item 1A section into individual risk factors.

This is the piece week 2 depends on. You cannot hand-label 300 risk factors
until risk factors exist as separate records.

DESIGNED FROM THE HEADING PROFILE, NOT FROM ASSUMPTIONS

Profiling the 275 extracted sections settled the design:

  * Styled runs per section: min 26, median 64, max 445. Roughly one heading
    per 1,200 characters. Every filing has enough signal -- there is no need
    for a per-company fallback strategy.
  * WHICH style varies. UMBF and GBCI use bold with almost no italic; Ally and
    UBSI use italic for risk headings and bold only for category headers. So
    the signal is "bold OR italic OR underline", not any single attribute.
  * Style alone is not sufficient. Ally renders the cross-reference
    "Regulation and Supervision" in italic mid-sentence. Position matters too.

FOUR STAGES, IN THIS ORDER

  1. STRIP PAGE FURNITURE. Page numbers, "Table of Contents", and running
     headers like "Ally Financial Inc. - Form 10-K" appear as styled runs and
     would each become a phantom risk factor. They are detectable because they
     repeat near-identically on every page.

  2. MERGE ADJACENT RUNS SHARING A STYLE. Filers split headings across several
     runs -- UBSI emits a heading then a lone "." block, and breaks words like
     "re-execute" out for emphasis mid-sentence. Ally splits one heading across
     a page break, resuming with a lowercase fragment. Merging must happen
     AFTER stripping furniture, or page numbers get glued into headings.

  3. CLASSIFY each merged run as CATEGORY header, RISK heading, or BODY.
     Category headers are short, often ALL CAPS or Title Case, and are followed
     by another heading rather than by prose. Risk headings are one sentence,
     styled, and followed by a paragraph. Body text is long and unstyled.

  4. ASSEMBLE. Each risk heading opens a new risk factor; following body runs
     belong to it; the enclosing category is carried as a field.

Category headers are KEPT as metadata rather than discarded. They are free
labels produced by the filers themselves and are worth comparing against the
risk taxonomy built later.

OUTPUT
  data/interim/risk_factors/{cik}_FY{year}.json   the split risks
  data/interim/risk_factors_report.csv            counts per filing
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import Counter

from config import DATA, EXCLUDED_CIKS, FLAGGED_CIKS

SECTION_DIR = DATA / "interim" / "item1a"
OUT_DIR = DATA / "interim" / "risk_factors"
REPORT = DATA / "interim" / "risk_factors_report.csv"

# A risk factor's body. Below this, the "risk" is probably a stray heading
# fragment; the count of these is reported rather than hidden.
MIN_BODY_CHARS = 200
# A heading is a sentence, not a paragraph.
MAX_HEADING_CHARS = 400
# A category header is short and label-like.
MAX_CATEGORY_CHARS = 80

PAGE_NUM = re.compile(r"^\d{1,4}\.?$")

# --------------------------------------------------------------- risk summary
# Many filers open Item 1A with a one-page overview -- "Risk Factor Summary",
# "Summary of Material Risk Factors", "Summary of Risk Factors" -- listing every
# risk in a sentence before the full versions appear below.
#
# Splitting it double-counts: each risk appears once as a summary stub and again
# as the real risk factor. That inflates per-company counts, and in week 4 the
# year-over-year matcher sees two near-identical texts for one risk and has to
# guess which is which. The audit found four such records in a 100-item sample,
# and it is why FHN reports 88-96 risks where its peers report ~40.
#
# The summary is bounded: it starts at its own heading near the top of the
# section, and ends where the first REAL risk factor begins -- recognisable
# because the summary's entries are bare category labels ("Credit Risks",
# "Liquidity Risks") followed by run-on sentences, while a real risk factor
# heading is a full sentence followed by an explanatory paragraph.
# The optional "Item 1A ... Risk Factors" prefix matters: merge_runs joins
# consecutive runs sharing a style, so the section heading and the summary
# heading often arrive as one run -- "Item 1A. Risk Factors Risk Factor
# Summary". Anchoring strictly to the start missed every one of those.
SUMMARY_HEADING = re.compile(
    r"^\s*(item\s*1\s*a\b[.:\-—–\s]*(risk\s*factors?\b)?[.:\-—–\s]*)?"
    r"(risk\s*factors?\s+summary|summary\s+of\s+(material\s+)?risk\s*factors?|"
    r"summary\s+of\s+(our\s+)?(principal|material|key)\s+risks?)\b", re.I)
# The summary never runs past this share of the section.
SUMMARY_MAX_FRACTION = 0.35
DIGITS = re.compile(r"\d+")
# The section's own heading is not a risk factor.
SECTION_HEADING = re.compile(r"^item\s*1\s*a\b", re.I)
# A body that continues the previous sentence: starts lowercase, or with
# punctuation that cannot open a sentence. Distinguishes a real heading from an
# emphasised phrase inside a sentence -- Ally italicises section names such as
# "Industry and Competition", and the text after them resumes with
# "in Part I, Item 1 of this report."
CONTINUATION = re.compile(r"^\s*([a-z]|[,.;:)\]]|and\b|or\b|in\b|of\b|for\b|to\b)")
FURNITURE = re.compile(
    r"^(table of contents|index|back to contents|form\s*10-k|part\s+[ivx]+\b|"
    r"annual report( on form 10-k)?|glossary of (acronyms|terms))\W*$", re.I)
# "Ally Financial Inc. - Form 10-K", "Huntington Bancshares Incorporated"
RUNNING_HEADER = re.compile(r"(form\s*10-k|\|\s*\d+\s*$|^\s*\d+\s*\|)", re.I)
PUNCT_ONLY = re.compile(r"^[\W_]*$")


def style_key(b: dict) -> tuple:
    """Runs merge only if these match."""
    return (b["bold"], b.get("italic", False), b.get("underline", False),
            b.get("font_size"))


def is_styled(b: dict) -> bool:
    return bool(b["bold"] or b.get("italic") or b.get("underline"))


def strip_furniture(blocks: list[dict], repeat_threshold: int = 3) -> tuple[list[dict], int]:
    """
    Remove page numbers, contents links and running headers.

    Running headers are found by FREQUENCY, with digits normalised away first.
    Fifth Third emits its running header as a single run combining the page
    number and the company name -- "27 Fifth Third Bancorp", "29 Fifth Third
    Bancorp", "30 Fifth Third Bancorp". Matching on exact text misses all of
    them because each is unique; matching on the digit-normalised form
    ("# Fifth Third Bancorp") catches every one.

    This mattered: those runs survived the filter, were treated as risk-factor
    headings, and split one real risk into two -- a fragment carrying the
    header as its heading, and a truncated remainder. Roughly 9 of Fifth
    Third's 53 risks were affected.

    A genuine risk heading is never repeated verbatim across a section, so
    frequency is a safe signal for short runs.
    """
    def header_shaped(t: str) -> bool:
        """
        Could this run be a running page header?

        Deliberately narrow. Two earlier versions were too broad and destroyed
        real content:

          v1 normalised digits on EVERY short run, so genuine headings that
             differed only by a number collapsed to one key and a whole
             section was reduced to zero risk factors.

          v2 accepted any run under 60 characters with 8 or fewer words. In
             filings that split text into thousands of tiny runs -- BPOP and
             MCHB do -- the words "of", "we", "at", "is" are each their own
             run, appear hundreds of times, and were deleted as furniture.
             BPOP lost 2,211 runs and MCHB 8,363, leaving text like
             "An impairment goodwill" (missing "of") and "amortizable affect"
             (missing "intangible assets could"). Nothing errored; the words
             were simply gone, and would have reached the hand-labelling and
             the LLM as broken sentences.

        A real running header has SEVERAL words and usually a digit or a
        company suffix. Requiring at least three words and at least fifteen
        characters puts ordinary words permanently out of reach of this filter,
        which matters more than catching every last header.
        """
        words = t.split()
        if not (3 <= len(words) <= 8):
            return False
        if not (15 <= len(t) <= 60):
            return False
        if t.rstrip().endswith((".", "?", "!", ":", ";")) and not re.match(r"^\s*\d", t):
            return False
        # Must look like page furniture: carry a page number, or name the
        # registrant with a corporate suffix.
        return bool(re.search(r"\d", t)) or bool(
            re.search(r"\b(inc|corp|corporation|bancorp|bancshares|company|"
                      r"financial|holdings|n\.a|form\s*10-k)\b", t, re.I))

    counts = Counter(DIGITS.sub("#", b["text"]).strip().lower()
                     for b in blocks if header_shaped(b["text"].strip()))
    kept, dropped = [], 0
    for b in blocks:
        t = b["text"].strip()
        key = DIGITS.sub("#", t).strip().lower()
        if (PAGE_NUM.match(t) or FURNITURE.match(t) or PUNCT_ONLY.match(t)
                or (len(t) <= 90 and RUNNING_HEADER.search(t))
                or (header_shaped(t) and counts[key] >= repeat_threshold)):
            dropped += 1
            continue
        kept.append(b)
    return kept, dropped


def is_continuation(prev_text: str, text: str) -> bool:
    """
    Does `text` continue `prev_text`, or start something new?

    merge_runs exists to repair headings that filers split across runs:
      ["Our exposure may be affected", "."]        orphan punctuation
      ["...titled ", "Regulation and Supervision", " in Part I"]  emphasis
      ["Requirements under U.S. Basel III...", "minimum ratios. Failure..."]
                                                   split across a page break

    In every real case the second piece is punctuation, starts lowercase, or is
    a very short fragment. It is NEVER a second full heading.

    Merging on shared styling alone was too eager. Where a filer puts the
    section heading, a category header and the first risk heading back to back
    with identical styling and no text between -- "Item 1A. Risk Factors"
    "CREDIT RISKS" "Deteriorating credit quality..." -- all three merged into
    one run, which was then dropped as the section heading, silently deleting
    the FIRST RISK FACTOR of that section. Popular's
    "ECONOMIC AND MARKET RISKS Weakness in the economy..." is the same welding
    surviving in the output.
    """
    if not text:
        return True
    if text[0] in ".,;:)]!?":
        return True                       # orphan punctuation
    if text[0].islower():
        return True                       # resumes mid-sentence
    if len(text) < 15 and not text[0].isalpha():
        return True                       # short non-word fragment, e.g. "2016-13,"
    if prev_text.rstrip().endswith(("(", "-", "[", "\u2014", "\u2013")):
        return True                       # prev clearly incomplete
    return False                          # two separate headings


def merge_runs(blocks: list[dict]) -> list[dict]:
    """
    Join consecutive same-styled runs.

    The is_continuation restriction applies ONLY between styled runs, because
    that is the only place two separate headings can be welded together.

    Applying it to unstyled runs as well was a regression: filers such as
    Popular split body prose into many small runs, which previously merged into
    one paragraph. With the restriction applied everywhere they stayed
    fragmented, each under MIN_BODY_CHARS, so the "styled heading followed by a
    substantial paragraph" test stopped firing and Popular fell from 26 risk
    factors to 10. Two adjacent unstyled runs are always body text; joining
    them is both safe and necessary.
    """
    merged: list[dict] = []
    for b in blocks:
        if merged and style_key(merged[-1]) == style_key(b) and (
                not (is_styled(merged[-1]) or is_styled(b))
                or is_continuation(merged[-1]["text"], b["text"])):
            prev = merged[-1]
            join = "" if b["text"][:1] in ".,;:)]" or prev["text"][-1:] in "(-[" else " "
            prev["text"] = (prev["text"] + join + b["text"]).strip()
            prev["end"] = b["end"]
        else:
            merged.append(dict(b))
    return merged


def classify(runs: list[dict], i: int) -> str:
    """CATEGORY, RISK, or BODY for run i."""
    b = runs[i]
    text = b["text"].strip()
    n = len(text)

    if not is_styled(b):
        return "BODY"
    if n > MAX_HEADING_CHARS:
        return "BODY"                      # a styled paragraph is still body
    if SECTION_HEADING.match(text):
        return "DROP"                      # "Item 1A. Risk Factors" is not a risk

    nxt = runs[i + 1] if i + 1 < len(runs) else None
    followed_by_prose = bool(nxt and not is_styled(nxt) and len(nxt["text"]) >= MIN_BODY_CHARS)
    followed_by_heading = bool(nxt and is_styled(nxt))
    # A heading opens a new sentence. If the following text resumes mid-sentence,
    # this styled run is an emphasised phrase inside a sentence, not a heading.
    continues_sentence = bool(nxt and CONTINUATION.match(nxt["text"]))

    # Short label followed by another heading -> a grouping label.
    if n <= MAX_CATEGORY_CHARS and followed_by_heading and not text.endswith("."):
        return "CATEGORY"
    # All-caps short label is a category even at the end of a group.
    if n <= MAX_CATEGORY_CHARS and text.isupper():
        return "CATEGORY"
    if followed_by_prose and not continues_sentence:
        return "RISK"
    # Styled, heading-length, but not followed by prose: most likely an
    # emphasised phrase inside a sentence, e.g. Ally's italic cross-reference
    # "Regulation and Supervision".
    return "BODY"


def is_label_like(text: str) -> bool:
    """
    A bare category label ("Credit Risks", "Liquidity Risks") rather than a
    risk-factor heading ("Deteriorating credit quality has adversely...").

    Few words and no sentence-ending punctuation. This is the discriminator
    between a Risk Factor Summary entry and a real risk factor, and it is
    stable where body length is not.
    """
    t = text.strip()
    return len(t.split()) <= 6 and not t.rstrip().endswith((".", "?", "!"))


def find_summary_span(runs: list[dict], labels: list[str]) -> tuple[int, int] | None:
    """
    Locate the Risk Factor Summary block, if the filing has one.

    Returns (start, end) indices into `runs`; `end` is the first run belonging
    to the real risk factors. Returns None when there is no summary.

    END DETECTION, AND WHY IT IS NOT BASED ON LENGTH

    A first version looked for the first heading followed by a body of at least
    600 characters, on the assumption that summary entries are short and real
    risks are long. That assumption broke as soon as adjacent unstyled runs
    merged freely: summary entries then exceeded 600 characters too, the very
    first entry was mistaken for the first real risk, and the whole block
    collapsed to its heading -- the rule silently stopped doing anything while
    still reporting that it had fired.

    Heading SHAPE is the stable signal. Summary entries are bare category
    labels; real risk factors are full sentences. Walk forward while headings
    stay label-like and stop at the first sentence-shaped one.
    """
    start = None
    for i, r in enumerate(runs):
        if SUMMARY_HEADING.match(r["text"].strip()):
            start = i
            break
    if start is None:
        return None

    limit = min(len(runs), max(int(len(runs) * SUMMARY_MAX_FRACTION), start + 2))
    for j in range(start + 1, limit):
        if labels[j] not in ("RISK", "CATEGORY"):
            continue
        if not is_label_like(runs[j]["text"]):
            return (start, j)          # first real risk factor
    # No sentence-shaped heading found inside the window. Drop only the summary
    # heading rather than risk deleting real content.
    return (start, start + 1)


def split_section(sec: dict) -> dict:
    raw = sec["blocks"]
    kept, dropped = strip_furniture(raw)
    runs = merge_runs(kept)

    labels = [classify(runs, i) for i in range(len(runs))]

    summary = find_summary_span(runs, labels)
    summary_dropped = 0
    if summary:
        lo, hi = summary
        summary_dropped = hi - lo
        for k in range(lo, hi):
            labels[k] = "DROP"

    risks: list[dict] = []
    category: str | None = None
    current: dict | None = None

    for run, label in zip(runs, labels):
        text = run["text"].strip()
        if label == "DROP":
            continue
        if label == "CATEGORY":
            category = text
            continue
        if label == "RISK":
            if current:
                risks.append(current)
            current = {"heading": text, "category": category, "body_parts": [],
                       "start_char": run["start"]}
            continue
        if current:
            current["body_parts"].append(text)
    if current:
        risks.append(current)

    out = []
    for r in risks:
        body = " ".join(r["body_parts"]).strip()
        out.append({
            "heading": r["heading"],
            "category": r["category"],
            "body": body,
            "chars": len(r["heading"]) + len(body),
            "start_char": r["start_char"],
        })

    return {
        "cik": sec["cik"], "ticker": sec["ticker"], "name": sec["name"],
        "fiscal_year": sec["fiscal_year"], "accession": sec["accession"],
        "source_url": sec["source_url"], "sha256": sec["sha256"],
        "section_chars": sec["section_chars"],
        "furniture_dropped": dropped,
        "summary_runs_dropped": summary_dropped,
        "has_risk_summary": bool(summary),
        "runs_after_merge": len(runs),
        "n_categories": len({r["category"] for r in out if r["category"]}),
        "n_risks": len(out),
        "risks": out,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", help="ticker: print the split for one filing")
    ap.add_argument("--fy", type=int)
    args = ap.parse_args()

    sections, excluded = [], []
    for path in sorted(SECTION_DIR.glob("*.json")):
        try:
            sec = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  unreadable: {path.name}")
            continue
        # Excluded companies are skipped here, not deleted from disk. The raw
        # and extracted layers stay complete so the choice remains auditable.
        if sec["cik"] in EXCLUDED_CIKS:
            excluded.append(sec)
            continue
        sections.append(sec)
    if not sections:
        raise SystemExit(f"No sections in {SECTION_DIR}. Run extract_item1a.py first.")

    if args.show:
        picked = [s for s in sections + excluded
                  if s["ticker"].upper() == args.show.upper()
                  and (args.fy is None or s["fiscal_year"] == args.fy)]
        if not picked:
            raise SystemExit(f"No section for {args.show} FY{args.fy or '*'}")
        res = split_section(picked[0])
        print("=" * 78)
        print(f"  {res['ticker']} FY{res['fiscal_year']}  --  {res['name'][:44]}")
        print(f"  {res['n_risks']} risks | {res['n_categories']} categories | "
              f"{res['furniture_dropped']} furniture runs dropped")
        print("=" * 78)
        for i, r in enumerate(res["risks"], 1):
            print(f"\n  {i:>3}. [{r['category'] or 'no category'}]  ({r['chars']:,} chars)")
            print(f"       {r['heading'][:100]}")
            print(f"       body: {r['body'][:100]}...")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for sec in sections:
        res = split_section(sec)
        (OUT_DIR / f"{res['cik']}_FY{res['fiscal_year']}.json").write_text(
            json.dumps(res, indent=1), encoding="utf-8")
        short = sum(1 for r in res["risks"] if len(r["body"]) < MIN_BODY_CHARS)
        rows.append({
            "flagged": FLAGGED_CIKS[res["cik"]][2] if res["cik"] in FLAGGED_CIKS else "",
            "cik": res["cik"], "ticker": res["ticker"], "name": res["name"],
            "fiscal_year": res["fiscal_year"], "section_chars": res["section_chars"],
            "n_risks": res["n_risks"], "n_categories": res["n_categories"],
            "furniture_dropped": res["furniture_dropped"],
            "summary_runs_dropped": res["summary_runs_dropped"],
            "has_risk_summary": res["has_risk_summary"],
            "short_bodies": short,
            "median_risk_chars": (statistics.median(r["chars"] for r in res["risks"])
                                  if res["risks"] else 0),
        })

    with REPORT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ------------------------------------------------------------- report
    counts = [r["n_risks"] for r in rows]
    total = sum(counts)
    print("=" * 78)
    print(f"  SPLIT {len(rows)} sections into {total:,} risk factors")
    print("=" * 78)

    if excluded:
        by_cik: dict = {}
        for sec in excluded:
            by_cik.setdefault(sec["cik"], []).append(sec["fiscal_year"])
        n_filings = sum(len(v) for v in by_cik.values())
        print(f"\n  EXCLUDED: {len(by_cik)} companies, {n_filings} filings "
              f"({100 * n_filings / (n_filings + len(rows)):.1f}% of the corpus)")
        for cik, years in sorted(by_cik.items()):
            ticker, ref, why = EXCLUDED_CIKS[cik]
            print(f"      {ticker:<6} FY{min(years)}-{max(years)}  [{ref}]  {why[:60]}...")

    flagged_rows = [r for r in rows if r["flagged"]]
    if flagged_rows:
        by_t: dict = {}
        for r in flagged_rows:
            by_t.setdefault(r["ticker"], 0)
            by_t[r["ticker"]] += 1
        print(f"\n  RETAINED WITH A CAVEAT: {len(by_t)} companies, "
              f"{len(flagged_rows)} filings")
        for t, c in sorted(by_t.items()):
            cik = next(r["cik"] for r in flagged_rows if r["ticker"] == t)
            print(f"      {t:<6} {c} filings  {FLAGGED_CIKS[cik][2][:66]}...")
        print("      These must be named wherever a result depends on them.")
    print(f"  risks per filing   min {min(counts)}   median "
          f"{statistics.median(counts):.0f}   max {max(counts)}")
    print(f"  furniture dropped  median {statistics.median(r['furniture_dropped'] for r in rows):.0f}"
          f" runs per filing")
    print(f"  categories found   median {statistics.median(r['n_categories'] for r in rows):.0f}")
    with_sum = [r for r in rows if r["has_risk_summary"]]
    print(f"  risk summary block found and skipped in {len(with_sum)}/{len(rows)} filings"
          f"   (median {statistics.median([r['summary_runs_dropped'] for r in with_sum]):.0f}"
          f" runs each)" if with_sum else "  no risk summary blocks found")
    med_chars = [r["median_risk_chars"] for r in rows if r["median_risk_chars"]]
    if med_chars:
        print(f"  risk size          median {statistics.median(med_chars):,.0f} chars")

    print("\n  A bank 10-K typically discloses 20-60 risk factors. Filings far")
    print("  outside that range are split wrong, not unusual.")

    print("\n  FEWEST RISKS -- likely under-split (headings not detected):")
    for r in sorted(rows, key=lambda r: r["n_risks"])[:8]:
        print(f"      {r['ticker']:<7} FY{r['fiscal_year']}  {r['n_risks']:>4} risks  "
              f"{r['section_chars']:>8,} chars")

    print("\n  MOST RISKS -- likely over-split (body text read as headings):")
    for r in sorted(rows, key=lambda r: -r["n_risks"])[:8]:
        print(f"      {r['ticker']:<7} FY{r['fiscal_year']}  {r['n_risks']:>4} risks  "
              f"{r['section_chars']:>8,} chars")

    shorts = sum(r["short_bodies"] for r in rows)
    print(f"\n  risks with body under {MIN_BODY_CHARS} chars: {shorts:,} of {total:,} "
          f"({100*shorts/max(total,1):.1f}%)")
    print("  Those are usually heading fragments rather than real risk factors.")

    print(f"\n  risks   -> {OUT_DIR}")
    print(f"  report  -> {REPORT}")
    print("\n  Inspect one with:  python split_risk_factors.py --show TICKER --fy 20YY")
    print("  Read at least three before trusting these counts. The splitter can")
    print("  produce a plausible number of plausible-looking wrong risks.")


if __name__ == "__main__":
    main()
