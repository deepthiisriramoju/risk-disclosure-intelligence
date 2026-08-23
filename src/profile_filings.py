"""
Read every downloaded filing and report how it is structured.

WHY PROFILE BEFORE PARSING

Writing the Item 1A extractor without knowing what the documents look like is
guesswork. The phrase "Item 1A" appears in a 10-K at least twice -- once in the
table of contents and once at the actual section -- and often more, in
cross-references and in a page header repeated on every page. A naive pattern
match takes the first hit, which is the TOC, and returns forty characters.

The questions that determine the parser's design are all empirical:

  * How many times does "Item 1A" appear per document, and how does that vary?
  * Where in the document is the real section -- is "last match" a safe rule,
    or does a cross-reference near the end break it?
  * Does Item 1A end at "Item 1B" or has that item been removed in later years?
  * Do filers use heading tags at all, or is everything <p> and <table>?
  * How much of the file is markup? A 5MB file may hold 300KB of text.

This script answers all of them across every filing, and prints a report small
enough to read. It writes nothing except the report and a JSON detail file.

Runtime is a few minutes for 275 filings. Nothing is fetched from the network.

Usage:
    python profile_filings.py
    python profile_filings.py --limit 25      # quick check first
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import statistics
import sys
from collections import Counter

from config import DATA, RAW_DIR

MANIFEST = RAW_DIR / "manifest.jsonl"
OUT_JSON = DATA / "filing_profile.json"

# Item headings. \s* between tokens because HTML puts tags and entities
# anywhere -- "Item&nbsp;1A." and "Item 1 A." both occur.
ITEM_PATTERNS = {
    "1A": re.compile(r"item\s*1\s*a\b", re.I),
    "1B": re.compile(r"item\s*1\s*b\b", re.I),
    "1C": re.compile(r"item\s*1\s*c\b", re.I),
    "2": re.compile(r"item\s*2\b", re.I),
}
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[\s\u00a0]+")


def to_text(html: str) -> str:
    """Crude but fast tag strip. Good enough for offsets and length."""
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    t = TAG_RE.sub(" ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#160;", " ")
    return WS_RE.sub(" ", t)


def profile_one(html: str) -> dict:
    text = to_text(html)
    n = max(len(text), 1)

    out: dict = {
        "bytes_html": len(html),
        "chars_text": len(text),
        "markup_ratio": round(1 - len(text) / max(len(html), 1), 3),
        "n_tables": len(re.findall(r"(?i)<table[\s>]", html)),
        "n_headings": len(re.findall(r"(?i)<h[1-6][\s>]", html)),
        "n_bold": len(re.findall(r"(?i)<(b|strong)[\s>]", html)),
        "inline_xbrl": bool(re.search(r"(?i)<ix:|xmlns:ix=", html)),
    }

    for label, pat in ITEM_PATTERNS.items():
        hits = [m.start() for m in pat.finditer(text)]
        out[f"n_{label}"] = len(hits)
        out[f"pos_{label}"] = [round(100 * h / n, 1) for h in hits[:12]]

    # What follows each "Item 1A"? A TOC entry is followed by dots or a page
    # number; the real section is followed by prose. This is the single most
    # useful field for designing the disambiguation rule.
    out["ctx_1A"] = [
        WS_RE.sub(" ", text[m.start(): m.start() + 90]).strip()
        for m in list(ITEM_PATTERNS["1A"].finditer(text))[:8]
    ]

    # Distance from last "Item 1A" to the next 1B/1C/2 after it -- a proxy for
    # how long the section is, and whether the terminator even exists.
    hits_1a = [m.start() for m in ITEM_PATTERNS["1A"].finditer(text)]
    if hits_1a:
        last = hits_1a[-1]
        for label in ("1B", "1C", "2"):
            after = [m.start() for m in ITEM_PATTERNS[label].finditer(text)
                     if m.start() > last]
            out[f"gap_to_{label}"] = (after[0] - last) if after else None
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

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

    print(f"Profiling {len(records)} filings. A few minutes; no network.\n")

    results = []
    for i, rec in enumerate(records, 1):
        path = RAW_DIR / rec["stored_path"]
        if not path.exists():
            continue
        try:
            html = gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
        except OSError:
            print(f"  unreadable: {rec['stored_path']}")
            continue
        p = profile_one(html)
        p.update({k: rec[k] for k in ("cik", "ticker", "name", "fiscal_year",
                                      "accession", "primary_document")})
        results.append(p)
        if i % 25 == 0:
            print(f"  {i}/{len(records)}")
            sys.stdout.flush()

    if not results:
        raise SystemExit("Nothing profiled.")

    OUT_JSON.write_text(json.dumps(results, indent=1))

    # ------------------------------------------------------------- report
    def med(key):
        vals = [r[key] for r in results if isinstance(r.get(key), (int, float))]
        return statistics.median(vals) if vals else 0

    R = len(results)
    print("\n" + "=" * 74)
    print(f"  CORPUS PROFILE  --  {R} filings, "
          f"{len({r['cik'] for r in results})} companies, "
          f"FY {min(r['fiscal_year'] for r in results)}"
          f"-{max(r['fiscal_year'] for r in results)}")
    print("=" * 74)

    sizes = sorted(r["bytes_html"] / 1e6 for r in results)
    chars = sorted(r["chars_text"] / 1000 for r in results)
    print(f"  html MB      min {sizes[0]:.1f}  median {med('bytes_html')/1e6:.1f}  "
          f"max {sizes[-1]:.1f}")
    print(f"  text Kchars  min {chars[0]:.0f}  median {med('chars_text')/1000:.0f}  "
          f"max {chars[-1]:.0f}")
    print(f"  markup ratio median {med('markup_ratio'):.2f}")
    print(f"  inline XBRL  {sum(r['inline_xbrl'] for r in results)}/{R}")
    print(f"  <h1-h6> tags present in {sum(r['n_headings'] > 0 for r in results)}/{R}"
          f"   (median count {med('n_headings'):.0f})")
    print(f"  <b>/<strong> median {med('n_bold'):.0f}    "
          f"<table> median {med('n_tables'):.0f}")

    print("\n" + "-" * 74)
    print("  HOW MANY TIMES EACH ITEM HEADING APPEARS PER FILING")
    print("-" * 74)
    for label in ("1A", "1B", "1C", "2"):
        c = Counter(r[f"n_{label}"] for r in results)
        dist = "  ".join(f"{k}x:{v}" for k, v in sorted(c.items())[:10])
        print(f"  Item {label:<3} {dist}")
    zero = [r for r in results if r["n_1A"] == 0]
    if zero:
        print(f"\n  !! {len(zero)} filings contain NO 'Item 1A' match at all:")
        for r in zero[:10]:
            print(f"       {r['ticker']} FY{r['fiscal_year']}  {r['primary_document']}")

    print("\n" + "-" * 74)
    print("  WHERE Item 1A APPEARS  (% through the text)")
    print("-" * 74)
    firsts = sorted(r["pos_1A"][0] for r in results if r["pos_1A"])
    lasts = sorted(r["pos_1A"][-1] for r in results if r["pos_1A"])
    if firsts:
        print(f"  first match   min {firsts[0]:.1f}%  median {statistics.median(firsts):.1f}%"
              f"  max {firsts[-1]:.1f}%")
        print(f"  last match    min {lasts[0]:.1f}%  median {statistics.median(lasts):.1f}%"
              f"  max {lasts[-1]:.1f}%")

    print("\n" + "-" * 74)
    print("  DISTANCE FROM LAST 'Item 1A' TO THE NEXT TERMINATOR (chars)")
    print("-" * 74)
    for label in ("1B", "1C", "2"):
        vals = [r[f"gap_to_{label}"] for r in results if r.get(f"gap_to_{label}")]
        missing = R - len(vals)
        if vals:
            print(f"  -> Item {label:<3} median {statistics.median(vals):>8.0f}   "
                  f"absent in {missing}/{R}")
        else:
            print(f"  -> Item {label:<3} never found after last 1A")

    print("\n" + "-" * 74)
    print("  TEXT FOLLOWING EACH 'Item 1A' MATCH  (first 6 filings)")
    print("  This is what tells TOC entries apart from the real section.")
    print("-" * 74)
    for r in results[:6]:
        print(f"\n  {r['ticker']} FY{r['fiscal_year']}  ({r['n_1A']} matches)")
        for j, (pos, ctx) in enumerate(zip(r["pos_1A"], r["ctx_1A"]), 1):
            print(f"    {j}. @{pos:>5.1f}%  {ctx[:88]}")

    print("\n" + "-" * 74)
    print(f"  full detail written to {OUT_JSON}")
    print("  Paste the report above. It describes all filings, not a subset.")
    print("-" * 74)


if __name__ == "__main__":
    main()
