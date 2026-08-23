"""
The only thing in this project that talks to sec.gov.

Three jobs, and it exists as its own module because all three are easy to get
wrong once and then get wrong in ten places:

  1. Rate limiting. SEC allows 10 req/s and will block your IP if you exceed it.
  2. Caching. You will run the ingestion twenty times this week. Downloading
     250 filings twenty times is both rude and slow. Cache on disk, keyed by URL.
  3. Retries. EDGAR returns 429 and 503 under load. Without backoff your run
     dies at company 37 and you restart from zero.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from config import CACHE_DIR, HTTP_TIMEOUT, MAX_RETRIES, REQUESTS_PER_SECOND

log = logging.getLogger(__name__)

USER_AGENT_ENV = "SEC_USER_AGENT"


class RateLimiter:
    """Token-bucket of size 1. Thread-safe so this survives adding threads later."""

    def __init__(self, per_second: float) -> None:
        self._min_interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


@dataclass
class FetchResult:
    url: str
    body: bytes
    status: int
    content_type: str
    from_cache: bool
    fetched_at_utc: str          # ISO8601; for a cache hit this is the ORIGINAL fetch time
    sha256: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class SECClientError(RuntimeError):
    pass


class SECClient:
    def __init__(
        self,
        user_agent: str | None = None,
        per_second: float = REQUESTS_PER_SECOND,
        cache_dir: Path = CACHE_DIR,
        timeout: int = HTTP_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        ua = user_agent or os.environ.get(USER_AGENT_ENV, "").strip()
        if not ua or "@" not in ua:
            raise SECClientError(
                f"Set {USER_AGENT_ENV} to something like:\n"
                f'  export {USER_AGENT_ENV}="Risk Disclosure Intelligence (research) '
                f'yourname@iastate.edu"\n'
                "SEC requires a descriptive User-Agent with a contact address and "
                "returns 403 without one."
            )
        self.limiter = RateLimiter(per_second)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
        )
        self.stats = {"hits": 0, "misses": 0, "retries": 0}

    # ------------------------------------------------------------ cache
    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(url.encode()).hexdigest()
        shard = self.cache_dir / digest[:2]
        shard.mkdir(parents=True, exist_ok=True)
        return shard / f"{digest}.body.gz", shard / f"{digest}.meta.json"

    def _read_cache(self, url: str) -> FetchResult | None:
        body_path, meta_path = self._cache_paths(url)
        if not (body_path.exists() and meta_path.exists()):
            return None
        try:
            meta = json.loads(meta_path.read_text())
            body = gzip.decompress(body_path.read_bytes())
        except (OSError, ValueError, json.JSONDecodeError):
            return None  # corrupt cache entry, treat as a miss
        return FetchResult(
            url=url,
            body=body,
            status=meta["status"],
            content_type=meta.get("content_type", ""),
            from_cache=True,
            fetched_at_utc=meta["fetched_at_utc"],
            sha256=meta["sha256"],
        )

    def _write_cache(self, result: FetchResult) -> None:
        body_path, meta_path = self._cache_paths(result.url)
        body_path.write_bytes(gzip.compress(result.body))
        meta_path.write_text(
            json.dumps(
                {
                    "url": result.url,
                    "status": result.status,
                    "content_type": result.content_type,
                    "fetched_at_utc": result.fetched_at_utc,
                    "sha256": result.sha256,
                    "bytes": len(result.body),
                },
                indent=2,
            )
        )

    # ------------------------------------------------------------ fetch
    def get(self, url: str, use_cache: bool = True) -> FetchResult:
        if use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                self.stats["hits"] += 1
                return cached

        last_error: str = ""
        for attempt in range(self.max_retries):
            self.limiter.acquire()
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._sleep_backoff(attempt)
                continue

            if resp.status_code == 200:
                self.stats["misses"] += 1
                result = FetchResult(
                    url=url,
                    body=resp.content,
                    status=200,
                    content_type=resp.headers.get("Content-Type", ""),
                    from_cache=False,
                    fetched_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    sha256=hashlib.sha256(resp.content).hexdigest(),
                )
                if use_cache:
                    self._write_cache(result)
                return result

            if resp.status_code == 403:
                raise SECClientError(
                    f"403 from {url}. SEC blocks requests without a valid "
                    f"User-Agent, and blocks IPs that exceed the rate limit. "
                    f"Check {USER_AGENT_ENV}; if it looks right, wait 10 minutes."
                )

            if resp.status_code == 404:
                # Genuinely missing. Do not burn retries on it.
                return FetchResult(
                    url=url,
                    body=b"",
                    status=404,
                    content_type=resp.headers.get("Content-Type", ""),
                    from_cache=False,
                    fetched_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    sha256=hashlib.sha256(b"").hexdigest(),
                )

            if resp.status_code in (429, 500, 502, 503, 504):
                self.stats["retries"] += 1
                last_error = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(int(retry_after), 60))
                else:
                    self._sleep_backoff(attempt)
                continue

            raise SECClientError(f"HTTP {resp.status_code} from {url}")

        raise SECClientError(f"Gave up on {url} after {self.max_retries} attempts ({last_error})")

    def get_json(self, url: str, use_cache: bool = True) -> Any:
        result = self.get(url, use_cache=use_cache)
        if result.status == 404:
            return None
        return json.loads(result.text)

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        # Full jitter. Without jitter, parallel retries resynchronise and you
        # hammer the server in lockstep.
        time.sleep(random.uniform(0, min(2**attempt, 30)))
