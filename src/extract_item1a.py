"""
Extract Item 1A (Risk Factors) from every downloaded 10-K.

DESIGNED FROM THE CORPUS PROFILE, NOT FROM ASSUMPTIONS

profile_filings.py measured all 275 filings first. Four findings drove every
decision below, and each one killed an approach that looked reasonable:

  1. HTML STRUCTURE IS USELESS. Heading tags appear in 2 of 275 filings. Median
     <b>/<strong> count is zero. Bold is done with CSS on <span> elements, and
     there are ~163 <table> elements per document doing layout, not tabulation.
     So: no tag-based section detection. Boldness must be read from inline
     style attributes.

  2. "LAST MATCH" IS WRONG. "Item 1A" occurs 2-11 times per filing. The last
     occurrence sits at a median of 40.8% through the text and as deep as 85.7%
     -- because it is usually a cross-reference well past the real section.
     Evidence: Item 1B is absent after the last match in 211 of 275 filings.

  3. THE TERMINATOR CHANGES MID-WINDOW. Item 1C (Cybersecurity) is absent from
     111 filings -- exactly 55 companies x 2 years. The SEC introduced it for
     FY2023 filings. So FY2021-22 run Item 1A -> 1B -> 2, and FY2023-25 run
     Item 1A -> 1B -> 1C -> 2. A rule hardcoding one order breaks half the panel.

  4. ENTITIES ARE NOT DECODED. The profile showed raw &#8220; in output. HTML
     entities must be unescaped before any text matching, or quoted
     cross-references like Item 1A "Risk Factors" are invisible to the pattern.

THE DISAMBIGUATION RULE

Every occurrence of "Item 1A" is scored, using the three shapes visible in the
profiled context strings:

  TOC             "Item 1A Risk Factors 14 Item 1B Unresolved Staff Comments"
                  -> a page number, then the NEXT item name, within ~200 chars
  CROSS-REFERENCE "Item 1A. Risk Factors below for additional information"
                  "Item 1A <<Risk Factors>>. For additional information"
                  -> connective words after, or a referring verb before, or the
                     section name in quotes
  REAL SECTION    "ITEM 1A. RISK FACTORS We are subject to a number of risks"
                  "Item 1A. Risk Factors Risk Factor Summary We are subject to"
                  -> heading followed by prose that opens a section

The winner is the highest-scoring candidate that also yields a plausible
section length. Length is the backstop: a TOC hit terminates a few hundred
characters later, which is disqualifying on its own.

OUTPUT

  data/interim/item1a/{cik}_FY{year}.json   text, blocks with bold flags, diagnostics
  data/interim/item1a_report.csv            one row per filing, for the parse rate
  quarantine reasons are recorded, never silently dropped

The blocks carry per-run bold flags because the SPLITTER needs them. Risk
factor boundaries in these documents are styled headings, and stripping to
plain text here would throw away the only signal the splitter has.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html as html_module
import json
import re
import statistics
import sys
from dataclasses import dataclass, asdict

from lxml import html as lxml_html

from config import DATA, RAW_DIR

MANIFEST = RAW_DIR / "manifest.jsonl"
OUT_DIR = DATA / "interim" / "item1a"
REPORT = DATA / "interim" / "item1a_report.csv"

# A real Item 1A in a bank 10-K runs tens of thousands of characters. Anything
# shorter is a table-of-contents hit or a truncated parse. Anything much longer
# has swallowed the following sections.
MIN_SECTION_CHARS = 8_000
MAX_SECTION_CHARS = 500_000

ITEM_1A = re.compile(r"item\s*1\s*a\b[.\s:—–-]*(risk\s*factors)?", re.I)
# Item order is not guaranteed. Provident Financial FY2022 places Item 1A
# AFTER Items 1B and 2, so none of the usual terminators exists downstream and
# the section ran to the end of the document (349,581 chars). Later items are
# checked as fallbacks; find_terminator takes the earliest valid hit, so on a
# normally-ordered filing these never fire.
TERMINATORS = (
    re.compile(r"item\s*1\s*b\b", re.I),   # Unresolved Staff Comments
    re.compile(r"item\s*1\s*c\b", re.I),   # Cybersecurity, FY2023+
    re.compile(r"item\s*2\b", re.I),       # Properties
    re.compile(r"item\s*3\b", re.I),       # Legal Proceedings
    re.compile(r"item\s*4\b", re.I),       # Mine Safety Disclosures
    re.compile(r"item\s*5\b", re.I),       # Market for Registrant's Common Equity
)

# Words that follow a cross-reference but never follow a section heading.
XREF_AFTER = re.compile(
    r"\b(below|above|herein|hereof|thereof|of this|in this|to this|for further|"
    r"for additional|for a more|for more|and elsewhere|contained in|included in|"
    r"beginning on|set forth)\b", re.I)
# Verbs that introduce a reference, looking backwards.
XREF_BEFORE = re.compile(
    r"\b(see|refer|referred|described|discussed|disclosed|set forth|included|"
    r"contained|identified|listed|under|in)\s*(in|to|under|at)?\s*$", re.I)
# Referring language anywhere in the preceding window. XREF_BEFORE is anchored
# to the end of the window and misses "for more information regarding the
# Company's process ..., refer to Item 1C".
REFERRING = re.compile(
    r"\b(see|refer\s+to|referred\s+to|described\s+in|discussed\s+in|"
    r"disclosed\s+in|set\s+forth\s+in|included\s+in|contained\s+in|"
    r"for\s+more\s+(information|detail)|for\s+(further|additional)|"
    r"as\s+described|as\s+discussed|please\s+see)\b", re.I)

# Another item name appearing right after -> table of contents.
TOC_AFTER = re.compile(r"item\s*(1\s*b|1\s*c|2|3|4)\b", re.I)
PAGE_NUM = re.compile(r"\brisk\s*factors\s*\.{0,}\s*\d{1,3}\b", re.I)
# Prose that opens a real risk factors section.
SECTION_OPENER = re.compile(
    r"\b(risk factor summary|we are subject to|you should carefully|"
    r"the following|our business|an investment in|investing in|"
    r"in addition to the other|before (making|investing)|"
    r"careful consideration|set forth below are|described below are)\b", re.I)

# The title that follows a REAL item heading. A cross-reference to the same
# item is usually followed by prose instead, or preceded by a referring verb.
# Compared against the following text with ALL whitespace stripped out, so
# word-splitting across element boundaries cannot defeat the match.
# Separator class is [.:\-—–\u2013\u2014|] repeated ZERO OR MORE times, not
# zero-or-one. Filers punctuate headings with whatever they like: Atlantic
# Union writes "ITEM 1B. - UNRESOLVED STAFF COMMENTS" -- a period AND a hyphen.
# A single-separator pattern matched the period, then failed on the hyphen, so
# the real heading was rejected and the section ran to the end of the document
# (484,295 chars for AUB FY2021).
_SEP = r"[.:\-—–|\u2010-\u2015]*"
TERMINATOR_TITLES = {
    r"item\s*1\s*b\b": re.compile(rf"^{_SEP}(unresolvedstaff|none)", re.I),
    r"item\s*1\s*c\b": re.compile(rf"^{_SEP}cybersecurit", re.I),
    r"item\s*2\b": re.compile(rf"^{_SEP}propert", re.I),
    r"item\s*3\b": re.compile(rf"^{_SEP}legalproceeding", re.I),
    r"item\s*4\b": re.compile(rf"^{_SEP}(minesafety|submissionofmatters|reserved)", re.I),
    r"item\s*5\b": re.compile(rf"^{_SEP}market(for)?(registrant|the)", re.I),
}


def find_terminator(text: str, after: int) -> tuple[int | None, str | None]:
    """
    Find where the Item 1A section actually ends.

    Naively taking the first "Item 1B|1C|2" match after Item 1A truncates the
    section at CROSS-REFERENCES inside the risk factors themselves. Observed in
    the corpus: Fifth Third FY2025 ended at "...refer to" and Huntington FY2025
    at "...for more information regarding ... refer to", both pointing at Item
    1C. The section was cut mid-sentence and thousands of characters were lost,
    with no error raised -- the extraction looked fine and was wrong.

    So a terminator candidate is accepted only if BOTH hold:
      * it is not preceded by referring language ("see", "refer to",
        "described in", "for more information regarding ... "), and
      * it is followed by the expected section title -- a real "Item 1B" heading
        is followed by "Unresolved Staff Comments", not by prose.

    Rejected candidates do not stop the search; scanning continues forward.
    """
    best_pos, best_pat = None, None
    for pat in TERMINATORS:
        for m in pat.finditer(text, after):
            ok, _ = judge_terminator(text, m, pat)
            if not ok:
                continue
            if best_pos is None or m.start() < best_pos:
                best_pos, best_pat = m.start(), pat.pattern
            break                             # first valid hit for this pattern
    return best_pos, best_pat


def judge_terminator(text: str, m, pat) -> tuple[bool, list[str]]:
    """
    Decide whether one regex hit is a real item heading or a cross-reference.

    Single source of truth: both find_terminator and debug_terminator.py call
    this. An earlier version duplicated the logic in the debug tool, which then
    drifted out of sync and reported 'rejected' for headings the extractor was
    correctly accepting -- a diagnostic that lies is worse than no diagnostic.
    """
    reasons: list[str] = []
    before = text[max(0, m.start() - 80): m.start()]
    if XREF_BEFORE.search(before) or REFERRING.search(before):
        reasons.append("preceded by referring language")

    title = TERMINATOR_TITLES.get(pat.pattern)
    if title:
        # Whitespace removed: filers split words across inline spans, so
        # "UNRESOLVE D STAFF" and "UNRESOLVED STAFF" must compare equal.
        following = re.sub(r"[\s\u00a0]+", "", text[m.end(): m.end() + 60])
        if not title.match(following):
            reasons.append("not followed by expected title")
    return (not reasons), reasons


BOLD_STYLE = re.compile(r"font-weight\s*:\s*(bold|[6-9]\d\d)", re.I)
ITALIC_STYLE = re.compile(r"font-style\s*:\s*italic", re.I)
UNDERLINE_STYLE = re.compile(r"text-decoration\s*:[^;]*underline", re.I)
FONTSIZE_STYLE = re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)\s*(pt|px|em|rem)", re.I)
ALIGN_STYLE = re.compile(r"text-align\s*:\s*(center|right|justify|left)", re.I)
# Tags that do NOT imply a word boundary. Filers split words across inline
# spans -- for kerning, for inline-XBRL tagging, for no reason at all -- so
# joining every element with a space corrupts the text itself. Observed in
# WesBanco: "UNRESOLVED" became "UNRESOLVE D" and "resources" became
# "resource s". That breaks terminator detection, and worse, it would feed
# broken words to the hand-labelling in week 2 and the LLM in week 3.
INLINE_TAGS = {
    "span", "b", "strong", "i", "em", "u", "font", "a", "sub", "sup",
    "small", "big", "tt", "code", "label", "nobr", "abbr", "cite", "q",
    "s", "strike", "mark", "time", "var", "samp", "kbd", "bdi", "bdo", "wbr",
}
SKIP_TAGS = {"script", "style", "head", "title"}
WS_CHARS = " \t\r\n\u00a0\u2007\u202f\u2009\u200a"


@dataclass
class Block:
    """
    One run of text with the styling that produced it.

    Bold alone is not enough. Profiling the extracted sections showed that
    UBSI marks risk-factor headings with 2 bold runs across 60,000 characters
    and Ally with 5 across 103,000 -- yet both clearly have headings. In both
    cases the headings are italic, and only the CATEGORY headers are bold.
    Recording only `bold` discarded the signal the splitter needs, so italic,
    underline, font size and alignment are captured too.

    font_size is in points where the source gave a unit it could convert;
    None when absent. A heading is often a point or two larger than body text,
    which is another usable signal when style attributes are inconsistent.
    """
    text: str
    bold: bool
    italic: bool
    underline: bool
    font_size: float | None
    align: str | None
    start: int
    end: int


# Points per unit, for normalising font-size to a single scale.
_UNIT_PT = {"pt": 1.0, "px": 0.75, "em": 12.0, "rem": 12.0}


def read_style(el) -> dict:
    """
    Collect styling by walking the ancestor chain.

    Emphasis (bold/italic/underline) is inherited: if any ancestor sets it,
    the text has it. Font size and alignment are NOT -- the nearest ancestor
    that specifies one wins, so the walk stops at the first hit for those.

    NOT memoised, deliberately. lxml creates element proxy objects on demand
    and their id() values are recycled -- within a single iteration the same
    id can refer to two different elements. A dict keyed on id(el) therefore
    returns another element's answer, and does so silently: bold detection
    quietly collapsed to False on large documents while working perfectly on
    small test cases. Walking the chain each time is correct and fast enough.
    """
    bold = italic = underline = False
    size: float | None = None
    align: str | None = None

    node = el
    while node is not None:
        tag = node.tag if isinstance(node.tag, str) else ""
        if tag in ("b", "strong"):
            bold = True
        if tag in ("i", "em"):
            italic = True
        if tag == "u":
            underline = True

        style = node.get("style") if hasattr(node, "get") else None
        if style:
            if BOLD_STYLE.search(style):
                bold = True
            if ITALIC_STYLE.search(style):
                italic = True
            if UNDERLINE_STYLE.search(style):
                underline = True
            if size is None:
                m = FONTSIZE_STYLE.search(style)
                if m:
                    size = round(float(m.group(1)) * _UNIT_PT.get(m.group(2).lower(), 1.0), 1)
            if align is None:
                m = ALIGN_STYLE.search(style)
                if m:
                    align = m.group(1).lower()
        node = node.getparent()

    return {"bold": bold, "italic": italic, "underline": underline,
            "font_size": size, "align": align}


def squash(text: str) -> str:
    """Collapse whitespace without deciding word boundaries."""
    return re.sub(r"[\s\u00a0]+", " ", text)


def to_blocks(raw_bytes: bytes) -> tuple[str, list[Block]]:
    """
    Flatten the document into text runs, each tagged with whether it is bold.

    Takes BYTES, not str. Every filing here is inline XBRL and the newer ones
    open with <?xml version="1.0" encoding="utf-8"?>. lxml refuses a decoded
    string carrying an encoding declaration -- it cannot trust a stated
    encoding on already-decoded text -- and raises ValueError. Handing it the
    original bytes lets it read the declaration and decode correctly itself.

    A space is inserted between two runs only when the source justifies one:
    either the original text had whitespace at the join, or the intervening
    tag is block-level. Inline tags (span, b, i, ...) join with nothing, so a
    word split across spans stays one word.
    """
    root = lxml_html.fromstring(raw_bytes)
    for bad in root.xpath("//script|//style"):
        bad.getparent().remove(bad)

    blocks: list[Block] = []
    parts: list[str] = []
    cursor = 0
    pending_space = True          # nothing emitted yet; no leading space wanted

    def emit(raw: str, el, forced_boundary: bool) -> None:
        nonlocal cursor, pending_space
        if not raw:
            return
        lead_ws = raw[0] in WS_CHARS
        trail_ws = raw[-1] in WS_CHARS
        text = squash(raw).strip()
        if not text:
            # Whitespace-only run still separates its neighbours.
            pending_space = True
            return

        sep = " " if (parts and (forced_boundary or lead_ws or pending_space)) else ""
        if sep:
            parts.append(sep)
            cursor += 1

        st = read_style(el) if el is not None else {
            "bold": False, "italic": False, "underline": False,
            "font_size": None, "align": None}
        blocks.append(Block(text, st["bold"], st["italic"], st["underline"],
                            st["font_size"], st["align"], cursor, cursor + len(text)))
        parts.append(text)
        cursor += len(text)
        pending_space = trail_ws

    for el in root.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag in SKIP_TAGS:
            continue
        if el.text:
            emit(el.text, el, forced_boundary=tag not in INLINE_TAGS)
        if el.tail:
            emit(el.tail, el.getparent(), forced_boundary=tag not in INLINE_TAGS)

    return "".join(parts), blocks


def score_candidate(text: str, pos: int, end: int) -> tuple[int, list[str]]:
    """Positive score means 'looks like the real section heading'."""
    before = text[max(0, pos - 90): pos]
    after = text[end: end + 200]
    # 30, not 70. A real cross-reference puts the connective word immediately
    # after the section name -- "Risk Factors below for additional information".
    # A 70-char window also caught ordinary prose: Huntington FY2023 opens
    # "The risks and uncertainties listed below present risks...", where "below"
    # sits 39 chars in. That cost the correct candidate 60 points and made the
    # whole filing fail.
    near = text[end: end + 30]
    score, why = 0, []

    if TOC_AFTER.search(after[:200]) or PAGE_NUM.search(text[pos: pos + 60]):
        score -= 100
        why.append("toc")
    if XREF_AFTER.search(near):
        score -= 60
        why.append("xref_after")
    if XREF_BEFORE.search(before):
        score -= 60
        why.append("xref_before")
    if re.search(r"[\"“”'']\s*risk\s*factors", text[pos: pos + 40], re.I):
        score -= 40
        why.append("quoted")

    heading = text[pos: end]
    if re.search(r"risk\s*factors", heading, re.I):
        score += 30
        why.append("names_section")
    if heading.isupper():
        score += 25
        why.append("allcaps")
    if SECTION_OPENER.search(near):
        score += 45
        why.append("opener")
    if re.match(r"\s*[A-Z]", after):
        score += 5
    return score, why


def find_section(text: str) -> dict:
    """Locate Item 1A. Returns a diagnostics dict; 'ok' says whether it worked."""
    candidates = []
    for m in ITEM_1A.finditer(text):
        s, why = score_candidate(text, m.start(), m.end())

        # Where would this candidate's section end? Validated, not the first
        # regex hit -- see find_terminator.
        stop, stop_by = find_terminator(text, m.end())
        length = (stop - m.start()) if stop else (len(text) - m.start())

        plausible = MIN_SECTION_CHARS <= length <= MAX_SECTION_CHARS
        if plausible:
            s += 25
            why.append("plausible_len")
        candidates.append({
            "pos": m.start(), "end": m.end(), "score": s, "why": why,
            "stop": stop, "stop_by": stop_by, "length": length,
            "pct": round(100 * m.start() / max(len(text), 1), 1),
        })

    if not candidates:
        return {"ok": False, "reason": "no_item_1a_match", "candidates": []}

    viable = [c for c in candidates
              if c["score"] > 0 and MIN_SECTION_CHARS <= c["length"] <= MAX_SECTION_CHARS]
    if not viable:
        best = max(candidates, key=lambda c: c["score"])
        return {"ok": False,
                "reason": f"no_viable_candidate(best_score={best['score']},"
                          f"len={best['length']})",
                "candidates": candidates}

    # Highest score; ties broken by the earlier position, since the real
    # section precedes the cross-references that discuss it.
    winner = max(viable, key=lambda c: (c["score"], -c["pos"]))
    return {"ok": True, "winner": winner, "candidates": candidates,
            "n_candidates": len(candidates)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not MANIFEST.exists():
        raise SystemExit("No manifest. Run download_filings.py first.")

    records = []
    with MANIFEST.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("http_status") == 200 and rec.get("stored_path"):
                records.append(rec)
    if args.limit:
        records = records[: args.limit]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, failures = [], []

    print(f"Extracting Item 1A from {len(records)} filings.\n")

    for i, rec in enumerate(records, 1):
        base = {
            "cik": rec["cik"], "ticker": rec["ticker"], "name": rec["name"],
            "fiscal_year": rec["fiscal_year"], "accession": rec["accession"],
            "doc_chars": 0, "n_candidates": 0, "ok": False, "section_chars": 0,
            "start_pct": "", "stop_by": "", "score": "", "why": "",
            "n_bold_blocks": 0, "n_italic_blocks": 0, "n_styled_blocks": 0,
            "reason": "",
        }

        path = RAW_DIR / rec["stored_path"]
        if not path.exists():
            # A row is appended for EVERY filing, including the ones that blow
            # up. Earlier this used `continue`, which dropped failures out of
            # the denominator entirely and reported 87/87 = 100% while 188
            # filings were crashing. A rate that excludes its own failures is
            # not a rate.
            rows.append({**base, "reason": "file_missing"})
            failures.append((rec, "file_missing"))
            continue
        try:
            raw = gzip.decompress(path.read_bytes())
            text, blocks = to_blocks(raw)
        except Exception as exc:                      # noqa: BLE001
            reason = f"parse_error:{type(exc).__name__}:{str(exc)[:60]}"
            rows.append({**base, "reason": reason})
            failures.append((rec, reason))
            continue

        result = find_section(text)
        row = {**base, "doc_chars": len(text),
               "n_candidates": len(result.get("candidates", []))}

        if result["ok"]:
            w = result["winner"]
            section = text[w["pos"]: w["stop"] or len(text)]
            sec_blocks = [asdict(b) for b in blocks
                          if b.start >= w["pos"] and b.end <= (w["stop"] or len(text))]
            out = OUT_DIR / f"{rec['cik']}_FY{rec['fiscal_year']}.json"
            out.write_text(json.dumps({
                "cik": rec["cik"], "ticker": rec["ticker"], "name": rec["name"],
                "fiscal_year": rec["fiscal_year"], "accession": rec["accession"],
                "source_url": rec["url"], "sha256": rec["sha256"],
                "start_char": w["pos"], "end_char": w["stop"],
                "start_pct": w["pct"], "stop_by": w["stop_by"],
                "score": w["score"], "why": w["why"],
                "n_candidates": len(result["candidates"]),
                "section_chars": len(section),
                "n_blocks": len(sec_blocks),
                "n_bold_blocks": sum(1 for b in sec_blocks if b["bold"]),
                "n_italic_blocks": sum(1 for b in sec_blocks if b["italic"]),
                "n_styled_blocks": sum(1 for b in sec_blocks
                                       if b["bold"] or b["italic"] or b["underline"]),
                "text": section,
                "blocks": sec_blocks,
            }, indent=1), encoding="utf-8")
            row.update({"ok": True, "section_chars": len(section),
                        "start_pct": w["pct"], "stop_by": w["stop_by"],
                        "score": w["score"], "why": ";".join(w["why"]),
                        "n_bold_blocks": sum(1 for b in sec_blocks if b["bold"]),
                        "n_italic_blocks": sum(1 for b in sec_blocks if b["italic"]),
                        "n_styled_blocks": sum(1 for b in sec_blocks
                                               if b["bold"] or b["italic"] or b["underline"]),
                        "reason": ""})
        else:
            row.update({"ok": False, "section_chars": 0, "start_pct": "",
                        "stop_by": "", "score": "", "why": "",
                        "n_bold_blocks": 0, "reason": result["reason"]})
            failures.append((rec, result["reason"]))

        rows.append(row)
        if i % 25 == 0:
            ok = sum(1 for r in rows if r["ok"])
            print(f"  {i}/{len(records)}   ok={ok} failed={len(rows) - ok}")
            sys.stdout.flush()

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # ---------------------------------------------------------------- report
    ok_rows = [r for r in rows if r["ok"]]
    print("\n" + "=" * 74)
    print(f"  PARSE RATE  {len(ok_rows)}/{len(rows)} = "
          f"{100 * len(ok_rows) / max(len(rows), 1):.1f}%")
    print("=" * 74)

    if ok_rows:
        lens = sorted(r["section_chars"] for r in ok_rows)
        print(f"  section chars   min {lens[0]:,}   median "
              f"{statistics.median(lens):,.0f}   max {lens[-1]:,}")
        pcts = sorted(r["start_pct"] for r in ok_rows)
        print(f"  starts at       min {pcts[0]}%   median "
              f"{statistics.median(pcts)}%   max {pcts[-1]}%")
        for label, key in (("bold runs", "n_bold_blocks"),
                           ("italic runs", "n_italic_blocks"),
                           ("any styled", "n_styled_blocks")):
            vals = sorted(r[key] for r in ok_rows)
            note = "   <- the splitter needs these" if key == "n_styled_blocks" else ""
            print(f"  {label:<15} min {vals[0]:>4}   median "
                  f"{statistics.median(vals):>5.0f}   max {vals[-1]:>5}{note}")

        # A section with no terminator ran to the end of the document. That is
        # not a crash and not necessarily wrong, but it means the length was
        # bounded by MAX_SECTION_CHARS rather than by an actual section
        # boundary -- so those rows deserve a look before being trusted.
        label = {
            r"item\s*1\s*b\b": "Item 1B",
            r"item\s*1\s*c\b": "Item 1C",
            r"item\s*2\b": "Item 2",
        }
        by_stop: dict = {}
        for r in ok_rows:
            key = label.get(r["stop_by"], r["stop_by"] or "NONE (ran to end of document)")
            by_stop[key] = by_stop.get(key, 0) + 1
        print("\n  terminated by:")
        for k, v in sorted(by_stop.items(), key=lambda kv: -kv[1]):
            print(f"      {str(k):<32} {v}")

        print("\n  by fiscal year:")
        for fy in sorted({r["fiscal_year"] for r in rows}):
            sub = [r for r in rows if r["fiscal_year"] == fy]
            good = sum(1 for r in sub if r["ok"])
            pct = 100 * good / max(len(sub), 1)
            print(f"      FY{fy}   {good:>3}/{len(sub):<3}  {pct:5.1f}%")

        # Shortest sections are the likeliest silent failures: a parse that
        # succeeded and returned plausible but wrong text.
        print("\n  SHORTEST 8 SECTIONS -- read these before trusting the rate:")
        for r in sorted(ok_rows, key=lambda r: r["section_chars"])[:8]:
            print(f"      {str(r['ticker']):<7} FY{r['fiscal_year']}  "
                  f"{r['section_chars']:>8,} chars  @{r['start_pct']}%  "
                  f"score={r['score']}  {r['why']}")

        # Fewest bold runs. The splitter finds risk-factor boundaries from
        # styled headings, so a section with almost none will not split.
        print("\n  FEWEST STYLED RUNS -- these will break the splitter:")
        for r in sorted(ok_rows, key=lambda r: r["n_styled_blocks"])[:8]:
            print(f"      {str(r['ticker']):<7} FY{r['fiscal_year']}  "
                  f"bold={r['n_bold_blocks']:>4} italic={r['n_italic_blocks']:>4} "
                  f"any={r['n_styled_blocks']:>4}   {r['section_chars']:>8,} chars")

    if failures:
        print(f"\n  FAILURES ({len(failures)}) BY REASON:")
        groups: dict = {}
        for rec, reason in failures:
            groups.setdefault(reason.split(":")[0] + ":" + reason.split(":")[1]
                              if ":" in reason else reason, []).append(rec)
        for reason, recs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            years = sorted({r["fiscal_year"] for r in recs})
            print(f"\n      {reason}   ({len(recs)} filings, FY {years})")
            sample = ", ".join(f"{r['ticker']} FY{r['fiscal_year']}" for r in recs[:6])
            print(f"        e.g. {sample}")

    print(f"\n  sections -> {OUT_DIR}")
    print(f"  report   -> {REPORT}")
    print("\n  The parse rate is a measurement, not a target. Do not tune it")
    print("  upward without reading the documents it fails on.")


if __name__ == "__main__":
    main()
