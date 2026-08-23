"""
Step 2 of ingestion: pull the actual 10-K documents into the raw layer.

The raw layer rule, which you should not break for the rest of the project:
raw is immutable and is exactly what the server sent, byte for byte, with a
fetch timestamp and a checksum. Nothing downstream ever writes here. If a
parse looks wrong in week 4, you need to be able to prove whether the bug is
in your parser or in the source document, and you can only do that if raw is
untouched.

Documents are stored gzipped -- bank 10-Ks run 2-15 MB of HTML and compress
to about a fifth of that.

Output:
  data/raw/filings/{cik}/{fiscal_year}/{accession}/{filename}.gz
  data/raw/manifest.jsonl   one JSON object per document, append-only
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import sys
from pathlib import Path

from config import ARCHIVE_DIR_URL, RAW_DIR, UNIVERSE_DIR
from sec_client import SECClient

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", stream=sys.stdout
)
log = logging.getLogger("download")

MANIFEST = RAW_DIR / "manifest.jsonl"

# Exhibits and graphics live in the same directory as the 10-K itself.
SKIP_SUFFIXES = (".jpg", ".png", ".gif", ".xml", ".xsd", ".css", ".js", ".pdf")


def document_url(cik: int, accession: str, filename: str) -> str:
    """
    EDGAR archive paths use the CIK with leading zeros stripped and the
    accession number with its dashes removed. Both quirks, both mandatory.
    """
    base = ARCHIVE_DIR_URL.format(cik=int(cik), accession_nodash=accession.replace("-", ""))
    return f"{base}/{filename}"


def resolve_primary_document(client: SECClient, cik: int, accession: str) -> str | None:
    """
    Fallback for filings where submissions.json has an empty primaryDocument.

    Heuristic: of the .htm/.txt files in the filing directory, take the largest
    that is not obviously an exhibit. This is a guess, and guesses need to be
    visible -- every document resolved this way is flagged in the manifest so
    you can eyeball them rather than trusting the heuristic silently.
    """
    base = ARCHIVE_DIR_URL.format(cik=int(cik), accession_nodash=accession.replace("-", ""))
    listing = client.get_json(f"{base}/index.json")
    if not listing:
        return None
    best, best_size = None, -1
    for item in listing.get("directory", {}).get("item", []):
        name = item.get("name", "")
        lower = name.lower()
        if lower.endswith(SKIP_SUFFIXES) or not lower.endswith((".htm", ".html", ".txt")):
            continue
        if lower.startswith(("ex-", "ex_", "exhibit")) or "-ex" in lower:
            continue
        size = int(item.get("size") or 0)
        if size > best_size:
            best, best_size = name, size
    return best


def already_downloaded() -> set[str]:
    if not MANIFEST.exists():
        return set()
    done = set()
    with MANIFEST.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("http_status") == 200:
                done.add(rec["accession"])
    return done


def main() -> None:
    index_path = UNIVERSE_DIR / "filing_index.csv"
    if not index_path.exists():
        raise SystemExit("Run build_universe.py first.")

    with index_path.open(encoding="utf-8") as fh:
        filings = list(csv.DictReader(fh))

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = SECClient()
    done = already_downloaded()
    log.info("%d filings in index, %d already fetched", len(filings), len(done))

    ok = failed = 0
    with MANIFEST.open("a", encoding="utf-8") as manifest:
        for n, row in enumerate(filings, start=1):
            accession = row["accession"]
            if accession in done:
                continue

            cik, fy = int(row["cik"]), int(row["fiscal_year"])
            filename = (row.get("primary_document") or "").strip()
            resolved = False
            if not filename:
                filename = resolve_primary_document(client, cik, accession) or ""
                resolved = True
            if not filename:
                log.error("no document found for %s FY%s (%s)", row["name"], fy, accession)
                failed += 1
                continue

            url = document_url(cik, accession, filename)
            result = client.get(url)

            if result.status != 200 or not result.body:
                log.error("HTTP %s for %s FY%s", result.status, row["name"], fy)
                manifest.write(json.dumps({
                    "cik": cik, "ticker": row["ticker"], "name": row["name"],
                    "fiscal_year": fy, "accession": accession, "url": url,
                    "http_status": result.status, "error": "empty_or_error",
                }) + "\n")
                failed += 1
                continue

            out_dir = RAW_DIR / "filings" / str(cik) / str(fy) / accession
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / (filename + ".gz")
            out_path.write_bytes(gzip.compress(result.body))

            manifest.write(json.dumps({
                "cik": cik,
                "ticker": row["ticker"],
                "name": row["name"],
                "fiscal_year": fy,
                "form": row["form"],
                "accession": accession,
                "filing_date": row["filing_date"],
                "report_date": row["report_date"],
                "url": url,
                "primary_document": filename,
                "primary_document_resolved_by_heuristic": resolved,
                "http_status": 200,
                "fetched_at_utc": result.fetched_at_utc,
                "sha256": result.sha256,
                "bytes_uncompressed": len(result.body),
                "stored_path": str(out_path.relative_to(RAW_DIR)),
                "n_amendments": int(row.get("n_amendments") or 0),
            }) + "\n")
            manifest.flush()
            ok += 1

            if n % 25 == 0:
                log.info("  %d/%d  ok=%d failed=%d", n, len(filings), ok, failed)

    log.info("-" * 60)
    log.info("downloaded=%d failed=%d", ok, failed)
    log.info("cache hits=%d misses=%d retries=%d", *client.stats.values())
    log.info("manifest: %s", MANIFEST)


def load_filing_text(stored_path: str) -> str:
    """Helper for the parser, next step. Never open raw files any other way."""
    return gzip.decompress((RAW_DIR / stored_path).read_bytes()).decode(
        "utf-8", errors="replace"
    )


if __name__ == "__main__":
    main()
