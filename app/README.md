# Streamlit app

Deployed from this repo on Streamlit Community Cloud.

## Data

`data/risks.csv` and `data/quality.json` are built by `src/build_app_data.py`
and committed, because Streamlit Community Cloud can only read files that are in
the repository.

The extract carries risk-factor **headings and labels, not bodies** — headings
are what the app displays, bodies are what would make the file large. The full
pipeline output stays out of version control, where it belongs.

Rebuild after any pipeline change:

```bash
cd src
python build_app_data.py
```

## Running locally

```bash
pip install -r app/requirements.txt
streamlit run app/streamlit_app.py
```

## Deploying

1. Push to GitHub
2. share.streamlit.io → New app
3. Repository: `deepthiisriramoju/risk-disclosure-intelligence`
4. Main file path: `app/streamlit_app.py`
5. Deploy

No secrets are required — the app reads committed CSVs and makes no API calls.
