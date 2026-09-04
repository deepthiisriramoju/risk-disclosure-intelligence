"""
Find out why a model rejected every request.

extract_llm.py reports a parse failure rate but not the underlying HTTP status
or message -- it retries, gives up, and counts the failure. That is right for a
long run and useless for diagnosis. This makes one bare request per variation
and prints exactly what came back.

Three things get tested separately, because they fail differently:

  1. the model name        -> 404 or a 400 naming the model
  2. plain JSON output     -> works on any model that exists
  3. an enforced schema    -> smaller models may not support it, which would
                              make output valid JSON but not the shape the
                              strict validator requires

Usage:
    python diagnose_model.py
    python diagnose_model.py --model gemini-2.5-flash-lite-preview-06-17
"""

from __future__ import annotations

import argparse
import json
import os

import requests

ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")
LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"

SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "category": {"type": "STRING",
                         "enum": ["financial", "operational",
                                  "regulatory", "strategic"]},
        },
        "required": ["id", "category"],
    },
}

PROMPT = ('Classify this risk factor as financial, operational, regulatory or '
          'strategic. Return a JSON array with one object: id 0 and the '
          'category.\n\n--- ITEM 0 ---\nid: 0\nHEADING: Deteriorating credit '
          'quality may increase loan losses.\nBODY: Borrower defaults may rise '
          'and collateral values may fall.')


def call(model: str, key: str, use_schema: bool) -> tuple[int, str]:
    cfg = {"temperature": 0, "responseMimeType": "application/json"}
    if use_schema:
        cfg["responseSchema"] = SCHEMA
    r = requests.post(
        ENDPOINT.format(model=model),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        json={"contents": [{"parts": [{"text": PROMPT}]}],
              "generationConfig": cfg},
        timeout=60)
    return r.status_code, r.text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-2.5-flash-lite")
    args = ap.parse_args()

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set in this terminal.")

    print("=" * 70)
    print(f"  DIAGNOSING  {args.model}")
    print("=" * 70)

    for label, use_schema in (("plain JSON, no schema", False),
                              ("with enforced responseSchema", True)):
        print(f"\n  --- {label} ---")
        try:
            status, body = call(args.model, key, use_schema)
        except requests.RequestException as exc:
            print(f"    network error: {type(exc).__name__}: {exc}")
            continue
        print(f"    HTTP {status}")
        if status == 200:
            try:
                data = json.loads(body)
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                print(f"    returned: {text[:200]}")
                try:
                    parsed = json.loads(text)
                    print(f"    parses as JSON: yes, {type(parsed).__name__} "
                          f"of {len(parsed) if isinstance(parsed, list) else '?'}")
                except json.JSONDecodeError as e:
                    print(f"    parses as JSON: NO -- {e}")
            except (KeyError, IndexError):
                # A 200 with no text usually means the response was blocked or
                # truncated; the reason is in the raw body.
                print(f"    200 but no text in the response:")
                print(f"    {body[:500]}")
        else:
            print(f"    {body[:500]}")

    print("\n" + "-" * 70)
    print("  MODELS YOUR KEY CAN ACTUALLY USE")
    print("-" * 70)
    try:
        r = requests.get(LIST_URL, headers={"x-goog-api-key": key}, timeout=60)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                name = m.get("name", "").replace("models/", "")
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods and "gemini" in name:
                    print(f"    {name}")
        else:
            print(f"    HTTP {r.status_code}: {r.text[:300]}")
    except requests.RequestException as exc:
        print(f"    could not list models: {exc}")

    print("\n  If the model name is absent from that list, that is the problem.")
    print("  If plain JSON works and the schema version fails, the model does")
    print("  not support enforced schemas and extract_llm.py must send the")
    print("  request without one for this model.")


if __name__ == "__main__":
    main()
