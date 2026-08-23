"""
LLM classification of risk factors, via the Gemini API.

RUN ORDER, AND WHY IT MATTERS

    python extract_llm.py --gold      # 300 items, ~15 requests, free
    python evaluate.py --pred llm_gold.csv --name llm --compare baseline_gold.csv
    ... iterate on prompts/category_vN.txt until the numbers stop improving ...
    python extract_llm.py --all       # 10,585 items, ~530 requests

Validate on the gold set before touching the full corpus. Running 10,585 items
and then discovering a prompt problem wastes a day of free-tier quota and gives
you a number you cannot trust.

DESIGN DECISIONS

BATCHING (20 items per request, not 1).
    10,585 individual requests against a free tier capped at a few hundred per
    day would take weeks. At 20 per request it is ~530 calls -- a day at most.
    Batching is also better engineering: fewer round trips, and it forces the
    strict structured output the project requires.

    The cost is that one malformed response loses 20 items rather than 1. That
    is handled by validating every batch and falling back to single-item
    requests for any batch that fails twice.

TEMPERATURE 0.
    The same input must produce the same output. A classifier that returns
    different labels on re-run cannot be evaluated, and its accuracy figure
    would not be reproducible by anyone else.

PROMPT IN A FILE, NOT A STRING.
    prompts/category_vN.txt is versioned in the repo, and EVERY output row
    records which version produced it. Improving the prompt in week 3 means
    weeks 1 and 2 were classified differently; without the version stamp those
    rows are silently incomparable.

RESPONSE SCHEMA.
    The API is given an explicit JSON schema with an enum on category, so the
    model cannot return prose, a fifth category, or a differently-shaped object.
    Output is still validated, because a constrained decoder is not a guarantee.

PARSE FAILURE RATE IS A METRIC.
    Malformed or short responses are counted and reported, not silently
    retried away. If 5% of batches fail, that belongs in EVALUATION.md.

RESUMABLE.
    Results are appended after every batch. Interrupt it, rerun it, and it skips
    what is done. Free-tier quota exhaustion is expected, not exceptional.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from pathlib import Path

import requests

from config import DATA

RISK_DIR = DATA / "interim" / "risk_factors"
GOLD = DATA / "gold" / "gold_set.csv"
PRED_DIR = DATA / "predictions"
PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

API_KEY_ENV = "GEMINI_API_KEY"
ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")

CATEGORIES = ["financial", "operational", "regulatory", "strategic"]

# Heading is what the risk IS; body is context. Truncated because a 6,000-char
# body adds tokens without adding signal, and the prompt tells the model to
# judge from the heading anyway.
HEADING_CHARS = 400
BODY_CHARS = 900

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "category": {"type": "STRING", "enum": CATEGORIES},
        },
        "required": ["id", "category"],
    },
}


def load_prompt(version: str) -> str:
    path = PROMPT_DIR / f"category_{version}.txt"
    if not path.exists():
        raise SystemExit(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def load_items(scope: str) -> list[dict]:
    if scope == "gold":
        if not GOLD.exists():
            raise SystemExit("No gold set. Run build_gold_set.py --draw first.")
        with GOLD.open(encoding="utf-8-sig") as fh:
            return [{"risk_id": r["risk_id"], "heading": r["heading"],
                     "body": r["body"]} for r in csv.DictReader(fh)]
    out = []
    for path in sorted(RISK_DIR.glob("*.json")):
        try:
            f = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for i, r in enumerate(f["risks"]):
            out.append({"risk_id": f"{f['cik']}_FY{f['fiscal_year']}_{i:03d}",
                        "heading": r["heading"], "body": r["body"]})
    return out


def build_batch_text(batch: list[dict]) -> str:
    lines = []
    for n, it in enumerate(batch):
        lines.append(f"--- ITEM {n} ---")
        lines.append(f"id: {n}")
        lines.append(f"HEADING: {it['heading'][:HEADING_CHARS]}")
        lines.append(f"BODY: {it['body'][:BODY_CHARS]}")
        lines.append("")
    return "\n".join(lines)


def call_api(prompt: str, batch_text: str, model: str, api_key: str,
             timeout: int = 120) -> str:
    body = {
        "contents": [{"parts": [{"text": prompt + "\n\n" + batch_text}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    resp = requests.post(
        ENDPOINT.format(model=model),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json=body, timeout=timeout)

    if resp.status_code == 429:
        raise RateLimited(resp.headers.get("Retry-After"))
    if resp.status_code in (500, 502, 503, 504):
        raise Transient(f"HTTP {resp.status_code}")
    if resp.status_code == 400:
        raise SystemExit(f"HTTP 400 from the API. Usually a bad model name or "
                         f"malformed request.\n{resp.text[:500]}")
    if resp.status_code in (401, 403):
        raise SystemExit(f"HTTP {resp.status_code}. Check {API_KEY_ENV} is set "
                         f"correctly and the key is active.\n{resp.text[:300]}")
    resp.raise_for_status()

    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise Malformed(f"unexpected response shape: {json.dumps(data)[:300]}")


class RateLimited(Exception):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after


class Transient(Exception):
    pass


class Malformed(Exception):
    pass


def parse_response(text: str, batch: list[dict]) -> list[dict]:
    """Validate strictly. A response that does not match the batch is a failure."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Malformed(f"not JSON: {exc}")
    if not isinstance(parsed, list):
        raise Malformed(f"expected a list, got {type(parsed).__name__}")
    if len(parsed) != len(batch):
        raise Malformed(f"expected {len(batch)} items, got {len(parsed)}")

    out = []
    for obj in parsed:
        if not isinstance(obj, dict) or "id" not in obj or "category" not in obj:
            raise Malformed(f"bad item: {str(obj)[:120]}")
        idx, cat = obj["id"], str(obj["category"]).strip().lower()
        if not isinstance(idx, int) or not (0 <= idx < len(batch)):
            raise Malformed(f"id out of range: {idx}")
        if cat not in CATEGORIES:
            raise Malformed(f"unknown category: {cat}")
        out.append({"risk_id": batch[idx]["risk_id"], "category": cat})

    if len({r["risk_id"] for r in out}) != len(batch):
        raise Malformed("duplicate or missing ids in response")
    return out


def already_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig") as fh:
        return {r["risk_id"] for r in csv.DictReader(fh) if r.get("category")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--prompt-version", default="v1")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--rpm", type=float, default=8.0,
                    help="requests per minute; stay under the free-tier cap")
    ap.add_argument("--limit", type=int, default=0, help="stop after N batches")
    ap.add_argument("--mock", action="store_true",
                    help="no API calls; exercises the plumbing only")
    args = ap.parse_args()
    if not (args.gold or args.all):
        ap.error("pick --gold or --all")

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key and not args.mock:
        raise SystemExit(
            f"{API_KEY_ENV} is not set.\n"
            f'  $env:{API_KEY_ENV} = "your-key"\n'
            "Never hardcode it and never commit it.")

    prompt = load_prompt(args.prompt_version)
    scope = "gold" if args.gold else "all"
    items = load_items(scope)
    if not items:
        raise SystemExit("Nothing to classify.")

    PRED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PRED_DIR / f"llm_{scope}.csv"
    done = already_done(out_path)
    todo = [it for it in items if it["risk_id"] not in done]

    batches = [todo[i:i + args.batch_size]
               for i in range(0, len(todo), args.batch_size)]
    if args.limit:
        batches = batches[: args.limit]

    print("=" * 70)
    print(f"  LLM CLASSIFICATION  --  {scope}")
    print("=" * 70)
    print(f"  model           {args.model}")
    print(f"  prompt version  {args.prompt_version}")
    print(f"  items           {len(items):,} total, {len(done):,} already done,"
          f" {len(todo):,} to do")
    print(f"  batches         {len(batches):,} of {args.batch_size}"
          f"   (~{len(batches)/max(args.rpm,1):.0f} min at {args.rpm:.0f} rpm)")
    if args.mock:
        print("  MOCK MODE -- no API calls, labels are random")
    print()

    interval = 60.0 / max(args.rpm, 0.1)
    new_file = not out_path.exists()
    fh = out_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=[
        "risk_id", "category", "prompt_version", "model", "run_utc"])
    if new_file:
        writer.writeheader()

    run_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ok = failed = rate_limits = 0
    failures: list[str] = []
    last_call = 0.0

    for n, batch in enumerate(batches, 1):
        results = None
        for attempt in range(4):
            wait = last_call + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last_call = time.monotonic()
            try:
                if args.mock:
                    text = json.dumps([{"id": i,
                                        "category": random.choice(CATEGORIES)}
                                       for i in range(len(batch))])
                else:
                    text = call_api(prompt, build_batch_text(batch),
                                    args.model, api_key)
                results = parse_response(text, batch)
                break
            except RateLimited as exc:
                rate_limits += 1
                delay = int(exc.retry_after) if (exc.retry_after or "").isdigit() else 60
                print(f"  rate limited; waiting {delay}s")
                time.sleep(min(delay, 120))
            except (Transient, requests.RequestException) as exc:
                time.sleep(random.uniform(1, 2 ** (attempt + 2)))
            except Malformed as exc:
                if attempt >= 1:
                    failures.append(f"batch {n}: {exc}")
                    break
                time.sleep(2)

        if results is None:
            # One bad batch should cost one batch, not the run. Retrying each
            # item alone isolates the item the model could not handle.
            for it in batch:
                try:
                    if args.mock:
                        raise Malformed("mock single-item failure")
                    text = call_api(prompt, build_batch_text([it]),
                                    args.model, api_key)
                    single = parse_response(text, [it])
                    writer.writerow({**single[0], "prompt_version": args.prompt_version,
                                     "model": args.model, "run_utc": run_utc})
                    ok += 1
                except Exception:                          # noqa: BLE001
                    failed += 1
                time.sleep(interval)
            fh.flush()
            continue

        for r in results:
            writer.writerow({**r, "prompt_version": args.prompt_version,
                             "model": args.model, "run_utc": run_utc})
        ok += len(results)
        fh.flush()

        if n % 5 == 0 or n == len(batches):
            print(f"  batch {n}/{len(batches)}   classified={ok:,} failed={failed}")
            sys.stdout.flush()

    fh.close()

    print("\n" + "=" * 70)
    print(f"  classified {ok:,}   failed {failed}   rate-limit waits {rate_limits}")
    if ok + failed:
        print(f"  parse failure rate {100*failed/(ok+failed):.2f}%"
              "   <- report this in EVALUATION.md")
    if failures:
        print(f"\n  first malformed batches ({len(failures)}):")
        for f in failures[:5]:
            print(f"    {f}")
    print(f"\n  wrote {out_path}")
    print(f"\n  Score it:  python evaluate.py --pred {out_path.name} "
          f"--name llm --compare baseline_{scope}.csv --compare-name keywords")


if __name__ == "__main__":
    main()
