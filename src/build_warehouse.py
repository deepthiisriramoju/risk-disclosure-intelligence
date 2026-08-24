"""
Build the DuckDB warehouse.

WHY LAYERS

The pipeline so far produces files. A warehouse turns them into something a
person can ask questions of without writing Python, and it forces the data
through a shape where bad rows have somewhere to go.

Three layers, each with one job:

  RAW      what the pipeline produced, loaded as-is. No cleaning, no filtering,
           no joins. If a row is malformed it lands here anyway, because the
           point of raw is to be able to prove what arrived.

  CLEAN    typed, deduplicated, validated. Rows failing a validation rule are
           NOT dropped -- they move to a quarantine table with the reason.
           Silently discarding bad rows is how a pipeline reports 100%
           completeness while losing data.

  MART     the star schema an analyst queries: one fact table of risk factors,
           dimensions for company, fiscal year and category. Denormalised
           enough that the common questions are one join or none.

WHY QUARANTINE RATHER THAN DELETE

A dropped row is invisible. A quarantined row is a number you can report and a
record you can read. The counts here feed the data-quality section of
EVALUATION.md; several are already known -- 624 DROPPED risk factors have no
current-year record, and risk factors from excluded companies exist in the
extract but must not reach the mart.

Usage:
    python build_warehouse.py
    python build_warehouse.py --query "SELECT * FROM mart_risk_factor LIMIT 5"
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import duckdb

from config import DATA, EXCLUDED_CIKS, FLAGGED_CIKS

WAREHOUSE = DATA / "warehouse" / "risk.duckdb"
RISK_DIR = DATA / "interim" / "risk_factors"
UNIVERSE = DATA / "universe" / "universe.csv"
MATCHES = DATA / "interim" / "yoy_matches.csv"
BASELINE = DATA / "predictions" / "baseline_all.csv"
LLM = DATA / "predictions" / "llm_all.csv"
GOLD = DATA / "gold" / "gold_set.csv"
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def load_raw(con: duckdb.DuckDBPyConnection) -> None:
    """Load every source file verbatim. No filtering at this stage."""
    print("  RAW")

    rows = []
    for path in sorted(RISK_DIR.glob("*.json")):
        try:
            f = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for i, r in enumerate(f["risks"]):
            rows.append({
                "risk_id": f"{f['cik']}_FY{f['fiscal_year']}_{i:03d}",
                "cik": f["cik"], "ticker": f["ticker"], "company_name": f["name"],
                "fiscal_year": f["fiscal_year"], "risk_index": i,
                "accession": f["accession"], "source_url": f["source_url"],
                "sha256": f["sha256"],
                "filer_category": r["category"] or "",
                "heading": r["heading"], "body": r["body"], "chars": r["chars"],
            })
    con.register("rows_df", _to_arrow(rows))
    con.execute("CREATE OR REPLACE TABLE raw_risk_factor AS SELECT * FROM rows_df")
    print(f"    raw_risk_factor       {len(rows):>7,}")

    for name, path in (("raw_company", UNIVERSE), ("raw_yoy_match", MATCHES),
                       ("raw_pred_baseline", BASELINE), ("raw_pred_llm", LLM),
                       ("raw_gold_label", GOLD)):
        if not path.exists():
            print(f"    {name:<22} (missing, skipped)")
            continue
        con.execute(f"""
            CREATE OR REPLACE TABLE {name} AS
            SELECT * FROM read_csv_auto('{path.as_posix()}', header=true,
                                        all_varchar=true)
        """)
        n = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        print(f"    {name:<22} {n:>7,}")


def _to_arrow(rows: list[dict]):
    import pandas as pd
    return pd.DataFrame(rows)


def build_clean(con: duckdb.DuckDBPyConnection) -> None:
    """
    Type, validate, deduplicate. Failures go to quarantine, never to /dev/null.
    """
    print("\n  CLEAN")
    excluded = ", ".join(str(c) for c in EXCLUDED_CIKS) or "-1"
    flagged = ", ".join(str(c) for c in FLAGGED_CIKS) or "-1"

    con.execute("""
        CREATE OR REPLACE TABLE quarantine (
            source_table VARCHAR, record_id VARCHAR,
            reason VARCHAR, detail VARCHAR
        )
    """)

    # --- rules, each one written so the reason is self-explanatory -----------
    con.execute(f"""
        INSERT INTO quarantine
        SELECT 'raw_risk_factor', risk_id, 'excluded_company',
               ticker || ' is excluded under DECISIONS.md D5/D6/D11'
        FROM raw_risk_factor WHERE cik IN ({excluded})
    """)
    con.execute("""
        INSERT INTO quarantine
        SELECT 'raw_risk_factor', risk_id, 'empty_heading',
               'heading is blank or shorter than 10 characters'
        FROM raw_risk_factor
        WHERE heading IS NULL OR length(trim(heading)) < 10
    """)
    con.execute("""
        INSERT INTO quarantine
        SELECT 'raw_risk_factor', risk_id, 'short_body',
               'body under 200 characters: probably a heading fragment'
        FROM raw_risk_factor WHERE length(body) < 200
    """)
    con.execute("""
        INSERT INTO quarantine
        SELECT 'raw_risk_factor', risk_id, 'duplicate_heading_in_filing',
               'same heading appears more than once in the same filing'
        FROM (
            SELECT risk_id,
                   row_number() OVER (PARTITION BY cik, fiscal_year, heading
                                      ORDER BY risk_index) AS rn
            FROM raw_risk_factor
        ) WHERE rn > 1
    """)

    con.execute(f"""
        CREATE OR REPLACE TABLE clean_risk_factor AS
        SELECT
            r.risk_id, r.cik, r.ticker, r.company_name,
            CAST(r.fiscal_year AS INTEGER) AS fiscal_year,
            r.risk_index, r.accession, r.source_url,
            nullif(trim(r.filer_category), '') AS filer_category,
            trim(r.heading) AS heading,
            r.body, r.chars,
            (r.cik IN ({flagged})) AS company_has_caveat
        FROM raw_risk_factor r
        WHERE r.risk_id NOT IN (SELECT record_id FROM quarantine)
    """)

    # A match can point at a risk that quarantine removed -- the pair survives,
    # its target does not. That leaves a row claiming a lineage that no longer
    # exists, which is a referential integrity break rather than a cosmetic
    # one: any query joining back to the prior risk silently loses those rows.
    #
    # Rather than delete the match, the dangling pointer is nulled and the row
    # is quarantined with its reason. The match itself is still true -- the
    # risk existed and was matched -- so the label is kept and only the broken
    # foreign key is cleared.
    con.execute("""
        INSERT INTO quarantine
        SELECT 'raw_yoy_match', y.curr_id, 'prior_risk_quarantined',
               'matched against ' || y.prev_id ||
               ', which quarantine removed; prior_risk_id nulled'
        FROM raw_yoy_match y
        WHERE nullif(y.prev_id, '') IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM clean_risk_factor c
                          WHERE c.risk_id = y.prev_id)
    """)
    con.execute("""
        CREATE OR REPLACE TABLE clean_yoy_match AS
        SELECT
            nullif(y.risk_id, '') AS risk_id,
            CASE WHEN EXISTS (SELECT 1 FROM clean_risk_factor c
                              WHERE c.risk_id = y.prior_risk_id)
                 THEN y.prior_risk_id END AS prior_risk_id,
            y.cik, y.ticker, y.fiscal_year, y.similarity, y.yoy_label,
            y.flagged_for_review,
            (y.prior_risk_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM clean_risk_factor c
                             WHERE c.risk_id = y.prior_risk_id)) AS prior_risk_missing
        FROM (
            SELECT
                nullif(curr_id, '') AS risk_id,
                nullif(prev_id, '') AS prior_risk_id,
                CAST(cik AS INTEGER) AS cik, ticker,
                CAST(fiscal_year AS INTEGER) AS fiscal_year,
                CAST(similarity AS DOUBLE) AS similarity,
                label AS yoy_label,
                (review = 'y') AS flagged_for_review
            FROM raw_yoy_match
        ) y
    """)

    # Prefer the LLM label; fall back to keywords. Recording WHICH classifier
    # produced each row matters -- a chart blending two of different accuracy
    # without saying so is misleading.
    con.execute("""
        CREATE OR REPLACE TABLE clean_category AS
        SELECT
            coalesce(l.risk_id, b.risk_id) AS risk_id,
            coalesce(nullif(l.category, ''), b.category) AS category,
            CASE WHEN nullif(l.category, '') IS NOT NULL
                 THEN 'llm' ELSE 'keywords' END AS classifier
        FROM raw_pred_baseline b
        FULL OUTER JOIN raw_pred_llm l USING (risk_id)
    """)

    for t in ("clean_risk_factor", "clean_yoy_match", "clean_category"):
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"    {t:<22} {n:>7,}")

    q = con.execute("""
        SELECT reason, count(*) n FROM quarantine GROUP BY 1 ORDER BY n DESC
    """).fetchall()
    total = sum(r[1] for r in q)
    print(f"\n    quarantine            {total:>7,}")
    for reason, n in q:
        print(f"      {reason:<32} {n:>6,}")


def build_mart(con: duckdb.DuckDBPyConnection) -> None:
    """Star schema. One fact table, three dimensions."""
    print("\n  MART")

    con.execute("""
        CREATE OR REPLACE TABLE dim_company AS
        SELECT DISTINCT
            cik, ticker, company_name,
            company_has_caveat
        FROM clean_risk_factor
    """)
    con.execute("""
        CREATE OR REPLACE TABLE dim_fiscal_year AS
        SELECT DISTINCT fiscal_year,
               (fiscal_year >= 2023) AS post_svb,
               fiscal_year || '-12-31' AS period_end
        FROM clean_risk_factor ORDER BY fiscal_year
    """)
    con.execute("""
        CREATE OR REPLACE TABLE dim_category AS
        SELECT DISTINCT category,
               CASE category
                 WHEN 'financial'   THEN 'Money: credit, funding, rates, securities'
                 WHEN 'operational' THEN 'Things breaking: cyber, systems, vendors, people'
                 WHEN 'regulatory'  THEN 'Rules and courts: laws, supervisors, litigation'
                 WHEN 'strategic'   THEN 'The plan failing: competition, M&A, reputation'
               END AS description
        FROM clean_category WHERE category IS NOT NULL
    """)

    con.execute("""
        CREATE OR REPLACE TABLE mart_risk_factor AS
        SELECT
            r.risk_id, r.cik, r.ticker, r.company_name, r.fiscal_year,
            r.risk_index, r.heading, r.chars, r.filer_category,
            r.company_has_caveat, r.source_url,
            c.category, c.classifier AS category_classifier,
            m.yoy_label, m.similarity, m.prior_risk_id, m.flagged_for_review,
            coalesce(m.prior_risk_missing, false) AS prior_risk_missing,
            g.category AS gold_category,
            (g.category IS NOT NULL) AS in_gold_set
        FROM clean_risk_factor r
        LEFT JOIN clean_category  c USING (risk_id)
        LEFT JOIN clean_yoy_match m USING (risk_id)
        LEFT JOIN raw_gold_label  g ON g.risk_id = r.risk_id
    """)

    # Risks that disappeared have no current-year record, so they would vanish
    # from a fact table keyed on current-year risks. Disappearances are half
    # the story, so they get their own table.
    con.execute("""
        CREATE OR REPLACE TABLE mart_dropped_risk AS
        SELECT m.prior_risk_id AS risk_id, m.cik, m.ticker,
               m.fiscal_year AS dropped_in_fiscal_year,
               r.heading, r.fiscal_year AS last_disclosed_fiscal_year
        FROM clean_yoy_match m
        JOIN clean_risk_factor r ON r.risk_id = m.prior_risk_id
        WHERE m.yoy_label = 'DROPPED'
    """)
    lost = con.execute("""
        SELECT count(*) FROM raw_yoy_match
        WHERE label = 'DROPPED' AND nullif(prev_id,'') IS NOT NULL
          AND prev_id NOT IN (SELECT risk_id FROM clean_risk_factor)
    """).fetchone()[0]
    if lost:
        print(f"    (mart_dropped_risk excludes {lost} rows whose prior-year text")
        print("     was quarantined; they are recorded in the quarantine table)")

    for t in ("dim_company", "dim_fiscal_year", "dim_category",
              "mart_risk_factor", "mart_dropped_risk"):
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"    {t:<22} {n:>7,}")


def quality_report(con: duckdb.DuckDBPyConnection) -> None:
    """
    Checks that run against the warehouse rather than the Python that built it.

    Independent verification: if a bug in the loader dropped rows, a count
    computed by the same loader would not reveal it.
    """
    print("\n" + "=" * 70)
    print("  DATA QUALITY")
    print("=" * 70)

    checks = [
        ("companies in mart", "SELECT count(DISTINCT cik) FROM mart_risk_factor"),
        ("fiscal years", "SELECT count(DISTINCT fiscal_year) FROM mart_risk_factor"),
        ("risk factors", "SELECT count(*) FROM mart_risk_factor"),
        ("missing category", "SELECT count(*) FROM mart_risk_factor WHERE category IS NULL"),
        ("missing YoY label", "SELECT count(*) FROM mart_risk_factor WHERE yoy_label IS NULL"),
        ("flagged for review", "SELECT count(*) FROM mart_risk_factor WHERE flagged_for_review"),
        ("from caveat companies", "SELECT count(*) FROM mart_risk_factor WHERE company_has_caveat"),
        ("in the gold set", "SELECT count(*) FROM mart_risk_factor WHERE in_gold_set"),
        ("quarantined", "SELECT count(*) FROM quarantine"),
    ]
    for label, sql in checks:
        print(f"    {label:<26} {con.execute(sql).fetchone()[0]:>8,}")

    print("\n    completeness by fiscal year")
    for fy, n, cat, yoy in con.execute("""
        SELECT fiscal_year, count(*),
               count(category), count(yoy_label)
        FROM mart_risk_factor GROUP BY 1 ORDER BY 1
    """).fetchall():
        print(f"      FY{fy}   {n:>5,} risks   {100*cat/n:>5.1f}% categorised   "
              f"{100*yoy/n:>5.1f}% matched")

    print("\n    classifier coverage")
    for src, n in con.execute("""
        SELECT coalesce(category_classifier,'(none)'), count(*)
        FROM mart_risk_factor GROUP BY 1 ORDER BY 2 DESC
    """).fetchall():
        print(f"      {src:<12} {n:>7,}")

    # Checks that should return zero. A non-zero result is a defect.
    print("\n    integrity checks (all should be 0)")
    integrity = [
        ("orphan prior_risk_id",
         """SELECT count(*) FROM mart_risk_factor m WHERE m.prior_risk_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM clean_risk_factor c
                            WHERE c.risk_id = m.prior_risk_id)"""),
        ("match with a quarantined prior risk",
         """SELECT count(*) FROM mart_risk_factor WHERE prior_risk_missing"""),
        ("duplicate risk_id",
         """SELECT count(*) FROM (SELECT risk_id FROM mart_risk_factor
            GROUP BY 1 HAVING count(*) > 1)"""),
        ("excluded company leaked in",
         f"""SELECT count(*) FROM mart_risk_factor
             WHERE cik IN ({', '.join(str(c) for c in EXCLUDED_CIKS) or '-1'})"""),
        ("NEW with a prior risk attached",
         """SELECT count(*) FROM mart_risk_factor
            WHERE yoy_label = 'NEW' AND prior_risk_id IS NOT NULL"""),
    ]
    for label, sql in integrity:
        n = con.execute(sql).fetchone()[0]
        # "prior risk missing" is a KNOWN and recorded condition, not a defect:
        # the flag exists so those rows can be excluded from lineage analysis.
        expected = label == "match with a quarantined prior risk"
        flag = "" if n == 0 else ("   (known, flagged)" if expected else "   <-- DEFECT")
        print(f"      {label:<36} {n:>6,}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", help="run one SQL statement and print the result")
    args = ap.parse_args()

    WAREHOUSE.parent.mkdir(parents=True, exist_ok=True)

    if args.query:
        con = duckdb.connect(str(WAREHOUSE), read_only=True)
        print(con.execute(args.query).df().to_string(index=False))
        return

    con = duckdb.connect(str(WAREHOUSE))
    print("=" * 70)
    print(f"  BUILDING WAREHOUSE  ->  {WAREHOUSE}")
    print("=" * 70)
    load_raw(con)
    build_clean(con)
    build_mart(con)
    quality_report(con)

    size_mb = WAREHOUSE.stat().st_size / 1e6
    print(f"\n  {WAREHOUSE}  ({size_mb:.1f} MB)")
    print(f"\n  Query it:")
    print(f"    python build_warehouse.py --query \"SELECT * FROM mart_risk_factor LIMIT 5\"")
    print(f"  Or open {WAREHOUSE.name} in any DuckDB client.")
    print(f"  Example analyses: {SQL_DIR}")
    con.close()


if __name__ == "__main__":
    main()
