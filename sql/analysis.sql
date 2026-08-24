-- Analyses against the risk-factor warehouse.
--
--   python build_warehouse.py --query "$(cat sql/analysis.sql)"    -- one at a time
--   or open data/warehouse/risk.duckdb in any DuckDB client
--
-- The queries below are the questions the project set out to answer. Each one
-- would take a person days by hand.


-- ============================================================================
-- 1. THE HEADLINE FINDING
--    Which banks newly disclosed a deposit-concentration risk, and when?
--
--    Counts DISTINCT COMPANIES, not risk factors. 19 new risks across 19 banks
--    is a sector-wide response; 19 across 4 banks is an outlier story, and the
--    two support completely different claims.
-- ============================================================================
SELECT
    fiscal_year,
    count(DISTINCT cik)                                    AS banks,
    round(100.0 * count(DISTINCT cik)
          / (SELECT count(*) FROM dim_company), 1)         AS pct_of_panel,
    count(*)                                               AS risk_factors
FROM mart_risk_factor
WHERE yoy_label = 'NEW'
  AND regexp_matches(lower(heading),
        'uninsured|deposit concentration|bank failure|fdic insurance')
GROUP BY fiscal_year
ORDER BY fiscal_year;


-- ============================================================================
-- 2. THE CONTROL
--    Did every theme spike in FY2023, or only deposits?
--
--    If everything spikes together, the result is an artifact of filing
--    conventions rather than a change in what banks were worried about. This
--    is the query that makes the finding defensible.
-- ============================================================================
WITH themes(theme, pattern) AS (
    VALUES ('deposits', 'uninsured|deposit concentration|bank failure|fdic insurance'),
           ('cyber',    'cyber|information security|data breach|ransomware'),
           ('ai',       'artificial intelligence|machine learning|generative'),
           ('climate',  'climate|weather|natural disaster'),
           ('rates',    'interest rate|net interest|unrealized loss')
)
SELECT
    t.theme,
    count(DISTINCT CASE WHEN m.fiscal_year = 2022 THEN m.cik END) AS fy2022,
    count(DISTINCT CASE WHEN m.fiscal_year = 2023 THEN m.cik END) AS fy2023,
    count(DISTINCT CASE WHEN m.fiscal_year = 2024 THEN m.cik END) AS fy2024,
    count(DISTINCT CASE WHEN m.fiscal_year = 2025 THEN m.cik END) AS fy2025
FROM themes t
LEFT JOIN mart_risk_factor m
       ON m.yoy_label = 'NEW'
      AND regexp_matches(lower(m.heading), t.pattern)
GROUP BY t.theme
ORDER BY fy2023 DESC;


-- ============================================================================
-- 3. FIRST MOVERS
--    Who disclosed a given risk first, and how long did peers take to follow?
--
--    Window function over the first year each company disclosed the theme.
-- ============================================================================
WITH first_disclosure AS (
    SELECT cik, ticker, min(fiscal_year) AS first_year
    FROM mart_risk_factor
    WHERE regexp_matches(lower(heading), 'uninsured|deposit concentration')
    GROUP BY cik, ticker
)
SELECT
    first_year,
    count(*)                                          AS banks_starting,
    sum(count(*)) OVER (ORDER BY first_year)          AS cumulative,
    string_agg(ticker, ', ' ORDER BY ticker)          AS which_banks
FROM first_disclosure
GROUP BY first_year
ORDER BY first_year;


-- ============================================================================
-- 4. CHURN BY COMPANY
--    Which banks rewrite their risk factors most, and which barely touch them?
--
--    A high churn rate with a stable risk count usually means a reorganisation
--    rather than genuine change -- see DECISIONS.md D14.
-- ============================================================================
SELECT
    ticker,
    fiscal_year,
    count(*)                                                       AS risks,
    sum(CASE WHEN yoy_label = 'NEW' THEN 1 ELSE 0 END)             AS new_risks,
    sum(CASE WHEN yoy_label = 'MATERIALLY_REVISED' THEN 1 ELSE 0 END) AS revised,
    sum(CASE WHEN yoy_label = 'CARRIED_FORWARD' THEN 1 ELSE 0 END) AS unchanged,
    round(100.0 * sum(CASE WHEN yoy_label IN ('NEW','MATERIALLY_REVISED')
                           THEN 1 ELSE 0 END) / count(*), 1)       AS pct_changed
FROM mart_risk_factor
WHERE yoy_label IS NOT NULL
GROUP BY ticker, fiscal_year
HAVING count(*) > 10
ORDER BY pct_changed DESC
LIMIT 20;


-- ============================================================================
-- 5. CATEGORY MIX OVER TIME
--    Is the industry's attention shifting between kinds of risk?
-- ============================================================================
SELECT
    fiscal_year,
    round(100.0 * sum(CASE WHEN category='financial'   THEN 1 ELSE 0 END)/count(*),1) AS financial,
    round(100.0 * sum(CASE WHEN category='operational' THEN 1 ELSE 0 END)/count(*),1) AS operational,
    round(100.0 * sum(CASE WHEN category='regulatory'  THEN 1 ELSE 0 END)/count(*),1) AS regulatory,
    round(100.0 * sum(CASE WHEN category='strategic'   THEN 1 ELSE 0 END)/count(*),1) AS strategic,
    count(*) AS total
FROM mart_risk_factor
WHERE category IS NOT NULL
GROUP BY fiscal_year
ORDER BY fiscal_year;


-- ============================================================================
-- 6. ONE BANK AGAINST ITS PEERS
--    Does this bank disclose what its industry discloses?
--
--    The question a risk or compliance function actually asks.
-- ============================================================================
WITH peer_rate AS (
    SELECT category,
           count(DISTINCT cik) AS peers_disclosing,
           (SELECT count(*) FROM dim_company) AS panel
    FROM mart_risk_factor
    WHERE fiscal_year = 2025 AND category IS NOT NULL
    GROUP BY category
),
this_bank AS (
    SELECT category, count(*) AS risks
    FROM mart_risk_factor
    WHERE fiscal_year = 2025 AND ticker = 'CFG' AND category IS NOT NULL
    GROUP BY category
)
SELECT
    p.category,
    coalesce(t.risks, 0)                                   AS cfg_risk_factors,
    p.peers_disclosing,
    round(100.0 * p.peers_disclosing / p.panel, 1)         AS pct_of_peers
FROM peer_rate p
LEFT JOIN this_bank t USING (category)
ORDER BY p.peers_disclosing DESC;


-- ============================================================================
-- 7. TEMPLATE PROPAGATION
--    Which risk factors appear in near-identical wording across several banks?
--
--    A risk factor filed by five companies in the same words is one law firm's
--    template moving through the market, not five independent judgements. This
--    matters for anyone benchmarking peer disclosure.
-- ============================================================================
SELECT
    left(heading, 90)                          AS heading_start,
    count(DISTINCT cik)                        AS banks,
    string_agg(DISTINCT ticker, ', ')          AS which,
    min(fiscal_year)                           AS first_seen
FROM mart_risk_factor
GROUP BY left(heading, 90)
HAVING count(DISTINCT cik) >= 3
ORDER BY banks DESC
LIMIT 15;


-- ============================================================================
-- 8. DATA QUALITY
--    What did the pipeline throw away, and why?
--
--    Publishing this alongside the analysis is the point. A warehouse that
--    cannot say what it discarded is asking to be trusted rather than checked.
-- ============================================================================
SELECT reason,
       count(*)                                              AS records,
       round(100.0 * count(*) / (SELECT count(*) FROM raw_risk_factor), 2) AS pct_of_raw
FROM quarantine
GROUP BY reason
ORDER BY records DESC;


-- ============================================================================
-- 9. GOLD SET AGREEMENT
--    Where does the classifier disagree with the hand-labelled ground truth?
--
--    A confusion matrix in SQL. Repeated confusion between two categories is
--    usually a taxonomy problem rather than a model problem.
-- ============================================================================
SELECT
    gold_category                                       AS truth,
    category                                            AS predicted,
    count(*)                                            AS n
FROM mart_risk_factor
WHERE in_gold_set AND category IS NOT NULL
GROUP BY gold_category, category
ORDER BY truth, n DESC;


-- ============================================================================
-- 10. WHAT DISAPPEARED
--     Risks a bank used to disclose and stopped.
--
--     Dropped risks have no current-year record, so they live in their own
--     table -- a fact table keyed on current-year risks would lose them
--     entirely, and disappearances are half the story.
-- ============================================================================
SELECT
    dropped_in_fiscal_year,
    ticker,
    left(heading, 100) AS stopped_disclosing
FROM mart_dropped_risk
ORDER BY dropped_in_fiscal_year DESC, ticker
LIMIT 25;
