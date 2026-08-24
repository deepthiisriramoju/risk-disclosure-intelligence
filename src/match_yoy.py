"""
Year-over-year risk factor matching.

The component the project is worthless without. Matching each risk factor to its
counterpart in the prior year is what turns a pile of classified text into an
answer to "what is this industry newly worried about?"

THE CENTRAL PROBLEM

Wording changes every year, so exact matching fails. Some rewrites are cosmetic
(a lawyer tightening a sentence); some are substantive (a bank adding a
deposit-concentration paragraph after March 2023). A similarity threshold has to
separate them, and that threshold determines EVERY "newly disclosed" number on
the dashboard. Too loose and everything looks carried forward; too tight and
routine editing looks like new risk.

So the threshold is not guessed. Run --calibrate first: it computes every
similarity in the corpus, prints the distribution, and shows real pairs at each
similarity band so the cut points are chosen by reading actual text.

HOW SIMILARITY IS COMPUTED

TF-IDF weighted cosine over word unigrams and bigrams, implemented directly
rather than imported. IDF is computed once across all 10,585 risk factors, so
weights are stable rather than varying per company.

  * Digits are normalised to a placeholder BEFORE comparison. Without this,
    "As of December 31, 2022" versus "As of December 31, 2023" registers as a
    change in every single risk factor, and the entire corpus looks rewritten
    every year. This one step matters more than the threshold.
  * Bigrams are included because unigram overlap alone treats any two risk
    factors from the same bank as similar -- they share a house vocabulary.

MATCHING IS GREEDY, NOT OPTIMAL

Pairs are matched in descending similarity, each risk used at most once. Optimal
assignment (Hungarian) would maximise total similarity across the whole set, but
it can move a strong pair to accommodate a weak one, which is harder to explain
and produces matches a reader cannot verify by eye. Greedy is defensible: the
best available pair wins, and nothing outranks it.

Usage:
    python match_yoy.py --calibrate          # look at the data first
    python match_yoy.py --audit 100          # draw pairs to hand-check
    python match_yoy.py --run                # apply thresholds, write results
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict

from config import DATA

RISK_DIR = DATA / "interim" / "risk_factors"
OUT_DIR = DATA / "interim"
MATCHES = OUT_DIR / "yoy_matches.csv"
AUDIT = OUT_DIR / "yoy_audit.csv"

# ---------------------------------------------------------------- thresholds
# Provisional. Replace after reading --calibrate output, and record the
# reasoning in DECISIONS.md. These are the most consequential numbers in the
# project.
MATCH_MIN = 0.25        # below this: no counterpart exists -> NEW / DROPPED
UNCHANGED_MIN = 0.90    # above this: CARRIED FORWARD
# between the two: MATERIALLY REVISED

# A pair must be MUTUAL best -- each side's top choice must be the other -- and
# must beat its own runner-up by this margin. Without both guards a low
# MATCH_MIN is genuinely too loose, because sibling risks within one bank-year
# (two commercial-real-estate items, say) can cross-match. With them, siblings
# compete and the correct counterpart wins on its own merits; MATCH_MIN then
# only decides the fate of items with no strong counterpart anywhere.
MATCH_MARGIN = 0.05

# Pairs landing in this band are counted, but flagged for review rather than
# trusted. It straddles MATCH_MIN deliberately: the cases most likely to be
# wrong are those just either side of the cut.
REVIEW_BAND = (0.20, 0.40)

# Heading and body are scored SEPARATELY and combined with these weights.
#
# A first version concatenated heading and body into one vector. Calibration
# against the real corpus showed why that fails: risk factors whose headings
# were IDENTICAL scored as low as 0.22 because their bodies had been rewritten.
#
#   0.407  "We are subject to extensive government regulation and supervision."
#          -> identical heading, both years
#   0.225  "Deposit insurance premiums could increase further in the future."
#          -> identical heading, both years
#
# Under a single combined score those would be labelled NEW plus DROPPED,
# inflating the "newly disclosed" count with risks that plainly carried
# forward -- exactly the failure the threshold is meant to prevent.
#
# The heading is the IDENTITY of a risk; the body is its explanation. Two risks
# with the same heading are the same risk however much the body was rewritten.
# The same principle governs the gold-set rubric and the Item 1A extractor:
# judge from the heading, use the body only to break ties.
#
# At these weights an identical heading floors the score at 0.70, so it can
# never be read as a new risk, while a genuinely unrelated pair
# ("Failure to complete the merger" -> "management's selection of accounting
# methods") still scores near zero and stays unmatched.
HEADING_WEIGHT = 0.70
BODY_WEIGHT = 0.30

# A company-year where this share of risks are NEW *and* a similar share are
# DROPPED, with total text roughly unchanged, has been reorganised rather than
# rewritten. See detect_structural_rewrite.
REWRITE_CHURN = 0.55
REWRITE_LENGTH_TOLERANCE = 0.35
REWRITE_COUNT_CHANGE = 0.25     # risk count moved at least this much

TOKEN = re.compile(r"[a-z]+")
DIGITS = re.compile(r"\d+")


def tokens(text: str) -> list[str]:
    """
    Normalise then tokenise.

    Digits collapse to a single placeholder before tokenising. Risk factors are
    dense with dates and dollar amounts that change every year without the risk
    changing -- "$3.4 billion" becoming "$3.9 billion" is not a new risk.
    Leaving digits in makes every risk factor look revised annually.
    """
    text = DIGITS.sub(" NUM ", text.lower())
    words = TOKEN.findall(text)
    grams = list(words)
    grams += [f"{a}_{b}" for a, b in zip(words, words[1:])]
    return grams


def build_idf(docs: list[list[str]]) -> dict[str, float]:
    """Inverse document frequency. Built separately for headings and bodies,
    because a term that is distinctive among one-sentence headings may be
    commonplace across multi-paragraph bodies."""
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    n = len(docs)
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def vectorise(grams: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = Counter(grams)
    vec = {t: (1 + math.log(c)) * idf.get(t, 1.0) for t, c in tf.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {t: v / norm for t, v in vec.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


def similarity(a: tuple, b: tuple) -> float:
    """Weighted combination of heading similarity and body similarity."""
    return HEADING_WEIGHT * cosine(a[0], b[0]) + BODY_WEIGHT * cosine(a[1], b[1])


def load_by_company() -> dict[int, dict[int, list[dict]]]:
    out: dict[int, dict[int, list[dict]]] = defaultdict(dict)
    for path in sorted(RISK_DIR.glob("*.json")):
        try:
            f = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        risks = []
        for i, r in enumerate(f["risks"]):
            risks.append({
                "risk_id": f"{f['cik']}_FY{f['fiscal_year']}_{i:03d}",
                "cik": f["cik"], "ticker": f["ticker"],
                "fiscal_year": f["fiscal_year"], "index": i,
                "heading": r["heading"], "body": r["body"],
                "text": r["heading"] + " " + r["body"],
                "chars": r["chars"],
            })
        out[f["cik"]][f["fiscal_year"]] = risks
    return out


def match_pair(prev: list[dict], curr: list[dict], vecs: dict) -> list[dict]:
    """
    Iterative mutual-best matching with a runner-up margin.

    WHY NOT GREEDY

    Greedy descending-similarity takes the highest score first and never
    reconsiders. That is fine when every risk has one obvious counterpart, and
    wrong when a bank discloses SIBLING risks -- two commercial-real-estate
    items, or an information-security item beside a data-privacy item. A greedy
    pass can bind last year's CRE-credit risk to this year's CRE-scrutiny risk
    simply because that pair happened to score highest first, leaving the true
    counterparts to mis-match in turn.

    WHAT THIS DOES INSTEAD

    A pair is accepted only if:
      * it is MUTUAL best -- this year's risk ranks that prior risk first, AND
        that prior risk ranks this one first, among everything still unmatched
      * BOTH sides beat their own runner-up by MATCH_MARGIN

    Accepted pairs are removed and the process repeats, so competition is
    re-evaluated as the field narrows. Siblings therefore resolve against each
    other rather than against a threshold.

    Anything still unmatched when rounds converge, but which had a candidate
    above MATCH_MIN, is AMBIGUOUS -- a real counterpart may exist but the
    evidence does not single one out. Counting those as NEW would manufacture
    disclosures that never happened.

    This is what makes a low MATCH_MIN defensible. A bare 0.25 with naive
    one-to-one matching would be too loose, and an interviewer would be right
    to push on it.
    """
    sim: dict = {}
    for c in curr:
        cv = vecs[c["risk_id"]]
        for p in prev:
            v = similarity(cv, vecs[p["risk_id"]])
            if v > 0.03:
                sim[(c["risk_id"], p["risk_id"])] = v

    open_c = {c["risk_id"] for c in curr}
    open_p = {p["risk_id"] for p in prev}
    pairs: list[dict] = []

    while True:
        best_c: dict = {}
        best_p: dict = {}
        for cid in open_c:
            cands = sorted(((sim.get((cid, pid), 0.0), pid) for pid in open_p),
                           reverse=True)
            if cands:
                best_c[cid] = cands
        for pid in open_p:
            cands = sorted(((sim.get((cid, pid), 0.0), cid) for cid in open_c),
                           reverse=True)
            if cands:
                best_p[pid] = cands

        accepted = []
        for cid, ccands in best_c.items():
            if not ccands or ccands[0][0] < MATCH_MIN:
                continue
            score, pid = ccands[0]
            pcands = best_p.get(pid)
            if not pcands or pcands[0][1] != cid:
                continue                      # not mutual best
            c_runner = ccands[1][0] if len(ccands) > 1 else 0.0
            p_runner = pcands[1][0] if len(pcands) > 1 else 0.0
            if score - c_runner < MATCH_MARGIN or score - p_runner < MATCH_MARGIN:
                continue                      # too close to call this round
            accepted.append((score, cid, pid))

        if not accepted:
            break
        for score, cid, pid in accepted:
            open_c.discard(cid)
            open_p.discard(pid)
            pairs.append({"curr_id": cid, "prev_id": pid,
                          "similarity": round(score, 4), "ambiguous": False})

    # Rounds have converged. Anything left with a plausible candidate is
    # ambiguous rather than new or dropped.
    # Unmatched items keep a pointer to their best REJECTED candidate.
    #
    # Without it, missed matches are unmeasurable. Mutual-best matching never
    # creates a sub-threshold pair -- a risk that fails simply becomes NEW or
    # DROPPED with nothing attached -- so an audit sampling only matched pairs
    # can measure false matches and is structurally blind to missed ones.
    #
    # That is the wrong blind spot to have. A missed match inflates BOTH the
    # NEW and the DROPPED count, and the headline claim of this project is a
    # NEW rate. The directional error is the one that had to be measurable.
    for cid in open_c:
        cands = [(sim.get((cid, pid), 0.0), pid) for pid in open_p]
        best, bid = max(cands, default=(0.0, ""))
        pairs.append({"curr_id": cid, "prev_id": "", "similarity": round(best, 4),
                      "ambiguous": best >= MATCH_MIN,
                      "rejected_id": bid, "rejected_sim": round(best, 4)})
    for pid in open_p:
        cands = [(sim.get((cid, pid), 0.0), cid) for cid in open_c]
        best, bid = max(cands, default=(0.0, ""))
        pairs.append({"curr_id": "", "prev_id": pid, "similarity": round(best, 4),
                      "ambiguous": best >= MATCH_MIN,
                      "rejected_id": bid, "rejected_sim": round(best, 4)})
    return pairs


def compute_all() -> tuple[list[dict], dict[str, dict]]:
    by_co = load_by_company()
    index: dict[str, dict] = {}
    head_docs, body_docs, ids, docs = [], [], [], []
    for years in by_co.values():
        for risks in years.values():
            for r in risks:
                index[r["risk_id"]] = r
                head_docs.append(tokens(r["heading"]))
                body_docs.append(tokens(r["body"]))
                docs.append(head_docs[-1])
                ids.append(r["risk_id"])

    print(f"  {len(docs):,} risk factors, building IDF over the whole corpus...")
    idf_h = build_idf(head_docs)
    idf_b = build_idf(body_docs)
    vecs = {rid: (vectorise(h, idf_h), vectorise(b, idf_b))
            for rid, h, b in zip(ids, head_docs, body_docs)}
    print(f"  vocabulary: {len(idf_h):,} heading terms, {len(idf_b):,} body terms")

    all_pairs = []
    n_co = 0
    for cik, years in sorted(by_co.items()):
        n_co += 1
        for fy in sorted(years):
            if fy - 1 not in years:
                continue                        # no prior year: first in window
            all_pairs.extend(match_pair(years[fy - 1], years[fy], vecs))
        if n_co % 10 == 0:
            print(f"  {n_co} companies matched")
    return all_pairs, index


def label(sim: float, curr_id: str, prev_id: str, ambiguous: bool = False) -> str:
    if ambiguous:
        return "AMBIGUOUS"
    """
    Label a pair. Sub-threshold pairs are split by the caller, not labelled
    here -- a greedy pair below MATCH_MIN means the best available counterpart
    was still not a counterpart, so the current-year risk is NEW and the
    prior-year risk is DROPPED. An earlier version emitted a single
    'UNMATCHED_LOW' row, which lost the DROPPED half entirely and undercounted
    every drop in the corpus.
    """
    if not prev_id:
        return "NEW"
    if not curr_id:
        return "DROPPED"
    if sim >= UNCHANGED_MIN:
        return "CARRIED_FORWARD"
    return "MATERIALLY_REVISED"


def cmd_calibrate(pairs: list[dict], index: dict) -> None:
    matched = [p for p in pairs if p["curr_id"] and p["prev_id"]]
    sims = sorted(p["similarity"] for p in matched)
    print("\n" + "=" * 74)
    print(f"  SIMILARITY DISTRIBUTION  ({len(matched):,} matched pairs)")
    print("=" * 74)
    for q in (5, 10, 25, 50, 75, 90, 95, 99):
        print(f"    {q:>2}th percentile   {sims[int(len(sims)*q/100)]:.3f}")
    print(f"    mean             {statistics.mean(sims):.3f}")

    print("\n  HISTOGRAM")
    bands = [(0.0, .2), (.2, .35), (.35, .5), (.5, .65), (.65, .8),
             (.8, .9), (.9, .95), (.95, 1.01)]
    for lo, hi in bands:
        n = sum(1 for s in sims if lo <= s < hi)
        print(f"    {lo:.2f}-{hi:.2f}  {n:>6,}  {100*n/len(sims):>5.1f}%  "
              f"{'#' * int(50*n/len(sims))}")

    print("\n" + "=" * 74)
    print("  REAL PAIRS AT EACH BAND -- read these to choose the thresholds")
    print("=" * 74)
    print("  Ask of each: is this the SAME RISK, reworded? Or a different risk?")
    rng = random.Random(20260731)
    for lo, hi in reversed(bands):
        band = [p for p in matched if lo <= p["similarity"] < hi]
        if not band:
            continue
        print(f"\n  --- similarity {lo:.2f} to {hi:.2f}  ({len(band):,} pairs) ---")
        for p in rng.sample(band, min(2, len(band))):
            c, pr = index[p["curr_id"]], index[p["prev_id"]]
            print(f"\n    {c['ticker']} FY{pr['fiscal_year']} -> FY{c['fiscal_year']}"
                  f"   similarity {p['similarity']:.3f}")
            print(f"      BEFORE: {pr['heading'][:150]}")
            print(f"      AFTER : {c['heading'][:150]}")

    print("\n" + "-" * 74)
    print("  CHOOSING THE THRESHOLDS")
    print("  MATCH_MIN     below it, treat as no counterpart (NEW / DROPPED).")
    print("                Set it where the pairs stop being the same risk.")
    print("  UNCHANGED_MIN above it, CARRIED FORWARD unchanged.")
    print("                Set it where rewording stops being substantive.")
    print(f"  Currently MATCH_MIN={MATCH_MIN}, UNCHANGED_MIN={UNCHANGED_MIN}.")
    print("  Edit them at the top of this file and record why in DECISIONS.md.")
    print("-" * 74)


def year_totals(index: dict) -> dict:
    """(cik, fiscal_year) -> (risk count, total characters) for every year."""
    tot: dict = defaultdict(lambda: [0, 0])
    for info in index.values():
        k = (info["cik"], info["fiscal_year"])
        tot[k][0] += 1
        tot[k][1] += info["chars"]
    return {k: tuple(v) for k, v in tot.items()}


def detect_structural_rewrite(rows: list[dict], totals: dict) -> dict:
    """
    Flag company-years REORGANISED rather than genuinely changed.

    A bank that merges 40 risk factors into 25 longer ones has added and
    dropped nothing, but every matcher reports mass deletion plus mass
    addition. One such company can dominate an industry trend line.

    Three signals together:
      churn         most risks look new or dropped
      count change  the number of risk factors moved sharply
      length ratio  yet the section is about the same total length

    The length ratio is computed from WHOLE-YEAR totals, not by summing the
    matched rows. An earlier version summed per-row characters, which
    double-counts nothing but omits the merged text entirely -- a company that
    merged six risks into two showed a length ratio of 0.67 and escaped
    detection, which is precisely the case the check exists for.
    """
    flags = {}
    by_cy: dict = defaultdict(list)
    for r in rows:
        by_cy[(r["cik"], r["ticker"], r["fiscal_year"])].append(r)

    for (cik, ticker, fy), rs in by_cy.items():
        labels = Counter(r["label"] for r in rs)
        total = sum(labels.values())
        churn = (labels["NEW"] + labels["DROPPED"]) / max(total, 1)

        curr = totals.get((cik, fy))
        prev = totals.get((cik, fy - 1))
        if not (curr and prev and prev[1]):
            continue
        length_ratio = curr[1] / prev[1]
        count_ratio = curr[0] / prev[0] if prev[0] else 1.0

        if (churn >= REWRITE_CHURN
                and abs(length_ratio - 1.0) <= REWRITE_LENGTH_TOLERANCE
                and abs(count_ratio - 1.0) >= REWRITE_COUNT_CHANGE):
            flags[(cik, fy)] = {
                "ticker": ticker, "fiscal_year": fy, "churn": round(churn, 3),
                "length_ratio": round(length_ratio, 3),
                "count_ratio": round(count_ratio, 3),
                "n_prev": prev[0], "n_curr": curr[0],
            }
    return flags


def cmd_run(pairs: list[dict], index: dict) -> None:
    lo, hi = REVIEW_BAND

    def row_for(curr_id, prev_id, sim, ambiguous=False):
        rid = curr_id or prev_id
        info = index[rid]
        return {
            "cik": info["cik"], "ticker": info["ticker"],
            "fiscal_year": info["fiscal_year"] if curr_id else info["fiscal_year"] + 1,
            "curr_id": curr_id, "prev_id": prev_id,
            "similarity": sim,
            "label": label(sim, curr_id, prev_id, ambiguous),
            # Counted, but not trusted. The cases most likely to be wrong sit
            # just either side of MATCH_MIN, so the band straddles it.
            "review": "y" if (lo <= sim < hi or ambiguous) else "",
            "heading": info["heading"][:300],
        }

    rows = []
    for p in pairs:
        amb = p.get("ambiguous", False)
        both = p["curr_id"] and p["prev_id"]
        if both and p["similarity"] < MATCH_MIN:
            rows.append(row_for(p["curr_id"], "", p["similarity"]))
            rows.append(row_for("", p["prev_id"], p["similarity"]))
        else:
            rows.append(row_for(p["curr_id"], p["prev_id"], p["similarity"], amb))

    flags = detect_structural_rewrite(rows, year_totals(index))
    for r in rows:
        r["structural_rewrite"] = "y" if (r["cik"], r["fiscal_year"]) in flags else ""

    with MATCHES.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    counts = Counter(r["label"] for r in rows)
    n_review = sum(1 for r in rows if r["review"])
    print("\n" + "=" * 74)
    print(f"  YEAR-OVER-YEAR MATCHING  --  {len(rows):,} rows")
    print("=" * 74)
    print(f"  thresholds: MATCH_MIN={MATCH_MIN}  UNCHANGED_MIN={UNCHANGED_MIN}"
          f"  MATCH_MARGIN={MATCH_MARGIN}")
    for lab, n in counts.most_common():
        print(f"    {lab:<20} {n:>7,}  {100*n/len(rows):>5.1f}%  "
              f"{'#' * int(40*n/len(rows))}")

    print("\n  BY FISCAL YEAR")
    years = sorted({r["fiscal_year"] for r in rows})
    print(f"    {'FY':<6}" + "".join(f"{l[:9]:>12}" for l in
                                     ("NEW", "CARRIED", "REVISED", "DROPPED")))
    for fy in years:
        sub = [r for r in rows if r["fiscal_year"] == fy]
        c = Counter(r["label"] for r in sub)
        print(f"    {fy:<6}" + "".join(
            f"{c[l]:>12,}" for l in ("NEW", "CARRIED_FORWARD",
                                     "MATERIALLY_REVISED", "DROPPED")))

    print(f"\n  FLAGGED FOR REVIEW: {n_review:,} rows ({100*n_review/len(rows):.1f}%)")
    print(f"  similarity in {REVIEW_BAND}, or ambiguous. Counted, not trusted.")

    if flags:
        print(f"\n  STRUCTURAL REWRITES FLAGGED: {len(flags)} company-years")
        print("  High churn with unchanged total text = reorganised, not rewritten.")
        for (cik, fy), f in sorted(flags.items(), key=lambda kv: -kv[1]["churn"])[:10]:
            print(f"    {f['ticker']:<7} FY{fy}  churn {f['churn']:.2f}  "
                  f"risks {f['n_prev']}->{f['n_curr']}  "
                  f"text length ratio {f['length_ratio']:.2f}")
        print("  These must be excluded from or flagged in any trend line.")
    else:
        print("\n  No structural rewrites detected at the current thresholds.")

    print(f"\n  wrote {MATCHES}")
    print("\n  NEXT: hand-check the matching itself.")
    print("    python match_yoy.py --audit 100")
    print("  Measure BOTH error directions -- false matches and missed matches.")
    print("  An unvalidated NEW rate is a number, not a finding.")


def cmd_audit(pairs: list[dict], index: dict, n: int) -> None:
    """
    Draw pairs for hand-checking, from BOTH populations.

    ACCEPTED pairs measure the false match rate: similarity cleared MATCH_MIN
    but the two texts are not the same risk.

    REJECTED candidates measure the missed match rate: similarity fell short,
    yet the texts are the same risk reworded. This is the error that inflates
    the headline NEW count, and it is where the known weakness of lexical
    similarity lives -- TF-IDF cosine cannot see a pure synonym rewrite
    ("increased regulatory scrutiny" -> "heightened supervisory attention"
    share no words), so those pairs score near zero and are counted as a
    disclosure that never happened.

    Sampling only accepted pairs would report a reassuring false-match rate and
    say nothing about the direction that matters.
    """
    rng = random.Random(20260801)
    matched = [p for p in pairs if p["curr_id"] and p["prev_id"]]
    rejected = [p for p in pairs
                if (bool(p["curr_id"]) != bool(p["prev_id"]))
                and p.get("rejected_id") and p.get("rejected_sim", 0) > 0.05]

    rows = []
    # A rejected pair surfaces twice -- once from the current-year side and
    # once from the prior-year side, both naming the same two texts. Counting
    # it twice would double its weight in the missed-match rate.
    seen: set = set()

    def add(curr_id, prev_id, sim, kind):
        if (curr_id, prev_id) in seen:
            return
        seen.add((curr_id, prev_id))
        c, pr = index[curr_id], index[prev_id]
        # similarity and kind sit AFTER the text, deliberately. Both would
        # anchor the judgement if read first -- knowing a pair scored 0.9, or
        # that the pipeline already accepted it, makes "same risk" the easy
        # answer. The columns are needed to compute the error rates, so they
        # cannot be omitted; they are placed where they will be read last.
        rows.append({
            "same_risk": "", "notes": "",
            "ticker": c["ticker"],
            "prev_fy": pr["fiscal_year"], "curr_fy": c["fiscal_year"],
            "prev_heading": pr["heading"][:400],
            "curr_heading": c["heading"][:400],
            "prev_body_start": pr["body"][:250],
            "curr_body_start": c["body"][:250],
            "similarity": sim, "kind": kind,
            "curr_id": curr_id, "prev_id": prev_id,
        })

    half = n // 2
    bands = [(MATCH_MIN, .35), (.35, .5), (.5, .65), (.65, .8), (.8, .9), (.9, 1.01)]
    per = max(1, half // len(bands))
    for lo, hi in bands:
        band = [p for p in matched if lo <= p["similarity"] < hi]
        for p in rng.sample(band, min(per, len(band))):
            add(p["curr_id"], p["prev_id"], p["similarity"], "accepted")

    # Rejected candidates, weighted toward the top of the range where a missed
    # match is most likely, but including the low end where synonym rewrites sit.
    rej_bands = [(0.05, .12), (.12, .18), (.18, MATCH_MIN)]
    per_r = max(1, (n - len(rows)) // len(rej_bands))
    for lo, hi in rej_bands:
        band = [p for p in rejected if lo <= p["rejected_sim"] < hi]
        for p in rng.sample(band, min(per_r, len(band))):
            cid = p["curr_id"] or p["rejected_id"]
            pid = p["prev_id"] or p["rejected_id"]
            if cid in index and pid in index:
                add(cid, pid, p["rejected_sim"], "rejected")

    rng.shuffle(rows)
    with AUDIT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    kinds = Counter(r["kind"] for r in rows)
    print(f"\n  Drew {len(rows)} pairs -> {AUDIT}")
    print(f"    accepted (matched):            {kinds['accepted']}")
    print(f"    rejected (counted NEW/DROPPED): {kinds['rejected']}")
    print("\n  Rows are shuffled, and 'similarity' and 'kind' are the last")
    print("  columns before the ids -- both would anchor you if read first.")
    print("\n  Put y or n in 'same_risk': is the AFTER text the same risk as the")
    print("  BEFORE text, reworded? Ignore the similarity column while judging.")
    print("\n  The two error rates:")
    print("    false match  = kind 'accepted' but same_risk = n")
    print("                   -> counts a rewrite as carried forward when it is new")
    print("    MISSED match = kind 'rejected' but same_risk = y")
    print("                   -> counts one risk as both NEW and DROPPED.")
    print("                      This is the error that inflates the headline.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--audit", type=int, metavar="N")
    args = ap.parse_args()
    if not (args.calibrate or args.run or args.audit):
        ap.error("pick --calibrate, --run or --audit N")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pairs, index = compute_all()

    if args.calibrate:
        cmd_calibrate(pairs, index)
    elif args.audit:
        cmd_audit(pairs, index, args.audit)
    else:
        cmd_run(pairs, index)


if __name__ == "__main__":
    main()
