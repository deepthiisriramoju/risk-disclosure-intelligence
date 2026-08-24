"""
Risk Disclosure Intelligence — Streamlit app.

Reads app/data/risks.csv and app/data/quality.json, both built by
src/build_app_data.py and committed so Streamlit Community Cloud can deploy
from the repo.

DESIGN NOTE

The Quality tab is not an afterthought. Most dashboards present numbers as if
they were certain; this one shows its own error rates on the same screen as its
findings, because a 43.8% missed-match rate materially changes how the headline
should be read. Hiding it would make the app more persuasive and less true.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

DATA = Path(__file__).parent / "data"

st.set_page_config(page_title="Risk Disclosure Intelligence",
                   page_icon="📄", layout="wide")


@st.cache_data
def load():
    df = pd.read_csv(DATA / "risks.csv")
    df["fiscal_year"] = pd.to_numeric(df["fiscal_year"], errors="coerce").astype("Int64")
    q = json.loads((DATA / "quality.json").read_text())
    return df, q


df, Q = load()

SIGNALS = {
    "Deposits / liquidity": r"uninsured|deposit concentration|deposit outflow|"
                            r"bank failure|liquidity risk|depositor confidence",
    "Cybersecurity": r"cyber|information security|data breach|ransomware",
    "Artificial intelligence": r"artificial intelligence|\bAI\b|machine learning|generative",
    "Interest rates": r"interest rate|net interest|unrealized loss|securities portfolio",
    "Climate": r"climate|weather|natural disaster",
    "Commercial real estate": r"commercial real estate|\bCRE\b|office",
}
LABEL_COLOUR = {"NEW": "🟢", "DROPPED": "🔴",
                "MATERIALLY_REVISED": "🟡", "CARRIED_FORWARD": "⚪",
                "AMBIGUOUS": "⚫"}

st.title("Risk Disclosure Intelligence")
st.caption(
    f"{Q['risk_factors']:,} risk factors from {Q['filings']} SEC 10-K filings · "
    f"{Q['companies']} US regional banks · FY2021–FY2025 · "
    "every stage accuracy-measured"
)

tab_finding, tab_company, tab_explore, tab_quality = st.tabs(
    ["The finding", "By company", "Explore", "Quality"])

# ---------------------------------------------------------------- FINDING
with tab_finding:
    st.subheader("What did banks start worrying about, and when?")
    st.markdown(
        "Each line is the share of the panel that **newly disclosed** a risk "
        "matching that theme in a given year — a risk with no counterpart in "
        "the prior year's filing."
    )

    panel = df["cik"].nunique()

    # The first fiscal year in the panel has no prior year, so nothing can be
    # NEW in it. That year is dropped from the chart. The cut-off is computed
    # from the PANEL's years, not from each series' own index -- an earlier
    # version took min() of the series, which for a theme whose first new
    # disclosures appear in the final year deleted the entire line.
    years = sorted(y for y in df["fiscal_year"].dropna().unique())
    plot_years = years[1:]

    series = {}
    for name, pattern in SIGNALS.items():
        hits = df[df["heading"].str.contains(pattern, case=False, na=False)]
        new = hits[hits["yoy_label"] == "NEW"]
        counts = new.groupby("fiscal_year")["cik"].nunique()
        series[name] = [100 * counts.get(y, 0) / panel for y in plot_years]
    chart = pd.DataFrame(series, index=plot_years)
    chart.index.name = "fiscal year"
    st.line_chart(chart, height=380)

    st.markdown(
        "**Deposits spike sharply and uniquely in FY2023, then vanish.** "
        "Cyber *declines*. AI peaks two years later. That divergence is what "
        "rules out an artifact — a filing-format change or a matching quirk "
        "would move every line in the same year."
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Banks, strict criteria",
              f"{Q['finding_strict']} of {Q['panel']}",
              f"{100*Q['finding_strict_ci'][0]:.0f}–{100*Q['finding_strict_ci'][1]:.0f}% CI")
    c2.metric("Banks, all verified",
              f"{Q['finding_all']} of {Q['panel']}",
              f"{100*Q['finding_all_ci'][0]:.0f}–{100*Q['finding_all_ci'][1]:.0f}% CI")
    c3.metric("Unverified keyword count", "17 of 50", "not published",
              delta_color="off")

    st.info(
        "**All 19 keyword hits were checked by hand** against the full "
        "prior-year risk set of the same company. Four were rejected: a category "
        "header mis-split as a risk, an off-topic regulatory match, one genuine "
        "missed match, and one filer with known extraction problems. The "
        "published figure is the verified count, not the keyword count."
    )

    st.divider()
    st.markdown("##### The risks themselves")
    yr = st.selectbox("Fiscal year", sorted(df["fiscal_year"].dropna().unique(),
                                            reverse=True), key="find_yr")
    sig = st.selectbox("Theme", list(SIGNALS), key="find_sig")
    sel = df[(df["fiscal_year"] == yr) & (df["yoy_label"] == "NEW")
             & df["heading"].str.contains(SIGNALS[sig], case=False, na=False)]
    if sel.empty:
        st.write("No newly disclosed risks matching that theme in that year.")
    else:
        st.dataframe(sel[["ticker", "heading", "category", "review"]]
                     .rename(columns={"review": "flagged for review"}),
                     use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- COMPANY
with tab_company:
    st.subheader("One bank, year by year")
    tickers = sorted(t for t in df["ticker"].dropna().unique() if t)
    tk = st.selectbox("Bank", tickers)
    sub = df[df["ticker"] == tk]

    if (sub["caveat"] == "y").any():
        st.warning(
            "This company is retained with a caveat — it made a material "
            "acquisition inside the study window, so its year-over-year "
            "comparison spans a changed business. See DECISIONS.md D12."
        )

    counts = (sub[sub["yoy_label"] != ""]
              .groupby(["fiscal_year", "yoy_label"]).size().unstack(fill_value=0))
    order = [c for c in ["NEW", "MATERIALLY_REVISED", "CARRIED_FORWARD",
                         "DROPPED", "AMBIGUOUS"] if c in counts.columns]
    st.bar_chart(counts[order], height=300)

    yr2 = st.selectbox("Fiscal year", sorted(sub["fiscal_year"].dropna().unique(),
                                             reverse=True), key="co_yr")
    show = sub[sub["fiscal_year"] == yr2].copy()
    show["  "] = show["yoy_label"].map(LABEL_COLOUR).fillna("")
    st.dataframe(
        show[["  ", "yoy_label", "heading", "category", "similarity"]]
        .sort_values("yoy_label"),
        use_container_width=True, hide_index=True, height=460)
    st.caption("🟢 new · 🟡 materially revised · ⚪ carried forward · "
               "🔴 dropped · ⚫ ambiguous")

# ---------------------------------------------------------------- EXPLORE
with tab_explore:
    st.subheader("Search every risk factor")
    q = st.text_input("Search headings", placeholder="uninsured deposit")
    c1, c2 = st.columns(2)
    labs = c1.multiselect("Year-over-year label",
                          sorted(l for l in df["yoy_label"].dropna().unique() if l))
    cats = c2.multiselect("Category",
                          sorted(c for c in df["category"].dropna().unique() if c))

    res = df
    if q:
        res = res[res["heading"].str.contains(q, case=False, na=False)]
    if labs:
        res = res[res["yoy_label"].isin(labs)]
    if cats:
        res = res[res["category"].isin(cats)]

    st.write(f"**{len(res):,}** of {len(df):,} risk factors · "
             f"{res['cik'].nunique()} companies")
    if q and not res.empty:
        by_year = (res[res["yoy_label"] == "NEW"]
                   .groupby("fiscal_year")["cik"].nunique())
        if not by_year.empty:
            st.bar_chart(by_year, height=200)
            st.caption("Companies newly disclosing a matching risk, by year")
    st.dataframe(res[["ticker", "fiscal_year", "heading", "category",
                      "yoy_label"]].head(400),
                 use_container_width=True, hide_index=True, height=420)

# ---------------------------------------------------------------- QUALITY
with tab_quality:
    st.subheader("How well does this pipeline actually work?")
    st.markdown(
        "Showing error rates beside findings is unusual for a dashboard. It is "
        "here because the numbers on the other tabs cannot be read correctly "
        "without them."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Item 1A extraction", f"{Q['parse_rate']*100:.0f}%", "275 of 275")
    c2.metric("Risk-factor splitting", f"{Q['split_accuracy']*100:.1f}%",
              f"n={Q['split_n']}, hand-labelled")
    c3.metric("Classification (LLM)", f"{Q['llm_accuracy']*100:.1f}%",
              f"+{100*(Q['llm_accuracy']-Q['baseline_accuracy']):.1f} pts vs keywords")
    c4.metric("End to end", f"{Q['end_to_end']*100:.0f}%", "errors compound")

    st.divider()
    st.markdown("##### Classification: the LLM had to beat a keyword baseline")
    st.table(pd.DataFrame({
        "accuracy": [f"{Q['baseline_accuracy']:.3f}", f"{Q['llm_accuracy']:.3f}"],
        "macro F1": [f"{Q['baseline_macro_f1']:.3f}", f"{Q['llm_macro_f1']:.3f}"],
    }, index=["Keyword baseline", "LLM (Gemini 2.5 Flash)"]))
    st.caption(
        f"Measured against {Q['classifier_n']} risk factors hand-labelled by the "
        "author. No LLM was used to build the ground truth — a model graded "
        "against another model measures agreement, not accuracy."
    )

    st.divider()
    st.markdown("##### Year-over-year matching: both error directions")
    m1, m2 = st.columns(2)
    m1.metric("False match rate", f"{Q['match_false_rate']*100:.1f}%",
              f"n={Q['match_n']}", delta_color="off")
    m2.metric("Missed match rate", f"{Q['match_missed_rate']*100:.1f}%",
              f"n={Q['match_n']}", delta_color="off")
    st.error(
        f"**{Q['match_missed_rate']*100:.1f}% of items counted as newly disclosed "
        "are reworded existing risks** — counted once as NEW and again as "
        "DROPPED. The cause is that lexical similarity cannot detect a pure "
        "synonym rewrite: *\"increased regulatory scrutiny\"* and *\"heightened "
        "supervisory attention\"* share no words.\n\n"
        "This rate applies to the corpus as a whole and is concentrated in one "
        "company-year. **It is why the headline finding was verified item by "
        "item rather than taken from the aggregate** — only 1 of 19 deposit "
        "risks turned out to be a genuine missed match."
    )

    if Q.get("baseline_coverage", 0):
        st.warning(
            f"**Classification coverage is incomplete.** "
            f"{Q['llm_coverage']:,} risk factors carry LLM labels; "
            f"{Q['baseline_coverage']:,} fall back to the keyword baseline, "
            "which is less accurate. The free-tier API daily quota is the "
            "binding constraint. Rows are tagged with which classifier produced "
            "them rather than blended into one chart."
        )

    st.divider()
    st.markdown("##### What this study cannot say")
    st.markdown(
        "- **The panel is survivors.** The asset filter reads a December 2025 "
        "balance sheet, so a bank had to be alive in 2026 to appear. Silicon "
        "Valley Bank, Signature Bank and First Republic — the banks that "
        "actually failed — cannot be in this data.\n"
        "- **Six companies are excluded** under stated rules: a non-December "
        "fiscal year, two mid-window mergers that made the registrant a "
        "different company, and three whose document structure the splitter "
        "cannot handle.\n"
        "- **Splitting errors propagate.** At 89.3%, roughly one risk factor in "
        "nine is mis-split, and those errors are present in everything measured "
        "against them.\n"
        "- **The gold set is one annotator.** Inter-annotator agreement would "
        "need two or more labellers."
    )

st.divider()
st.caption(
    "Data: SEC EDGAR, public filings, accessed under the SEC's fair-access "
    "rules. · Method and measurements: EVALUATION.md · Reasoning behind every "
    "threshold: DECISIONS.md"
)
