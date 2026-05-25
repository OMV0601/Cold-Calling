"""
Website fetcher.

Downloads web pages for the audit engine with:
  - SQLite caching keyed by URL (respects ttl_days — re-audits are free)
  - robots.txt compliance: fetches and caches robots.txt per host; defaults to
    permissive if unreachable
  - Per-host rate limiting so we do not hammer small business servers
  - Configurable timeout; all failures are captured in FetchResult — never fatal
  - Load-time measurement for the slow_load audit check
  - Contact info scraping (mailto: links, /contact page URL)

Public surface:
  WebFetcher.fetch(url)                        -> FetchResult
  WebFetcher.fetch_site_pages(homepage, paths) -> dict[str, FetchResult]
  extract_contact_info(pages)                  -> tuple[email | None, url | None]
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from leadscout.config import AppConfig, CacheConfig

logger = logging.getLogger(__name__)

# Paths we always try to fetch in addition to the homepage.
AUDIT_PATHS: list[str] = [
    "/contact",
    "/contact-us",
    "/book",
    "/booking",
    "/appointments",
    "/appointment",
    "/schedule",
    "/order",
    "/about",
    "/about-us",
]

# Realistic Chrome UA — many small-business sites block obvious bots.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Cap response size — skip pages over 8 MB (avoids downloading huge SPAs).
_MAX_CONTENT_BYTES = 8 * 1024 * 1024


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    """The outcome of a single URL fetch attempt."""

    url: str                        # the URL we requested
    final_url: str                  # after redirects
    status_code: Optional[int]      # None on connection-level failure
    html: Optional[str]             # raw HTML text; None on failure
    soup: Optional[BeautifulSoup] = field(default=None, repr=False)
    load_time_seconds: float = 0.0
    error: Optional[str] = None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        """True if the fetch succeeded and returned usable HTML."""
        return self.status_code is not None and self.status_code < 400 and self.html is not None


# ── HTML cache ────────────────────────────────────────────────────────────────

class HtmlCache:
    """
    SQLite-backed cache for raw HTML responses, stored in the same DB file
    as the Places cache.

    Keyed by the requested URL (before redirects).  Stores the raw HTML and
    the measured load time so the slow_load check uses the real first-run time
    on re-audits.
    """

    def __init__(self, config: CacheConfig) -> None:
        """Open (or create) the cache table in the shared DB file."""
        config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(config.db_path), check_same_thread=False)
        self._ttl = config.ttl_days * 86_400
        self._ensure_schema()

    def get(self, url: str) -> Optional[tuple[str, float]]:
        """
        Return (html, load_time_seconds) for *url* if cached and fresh.
        Returns None if the entry is missing or stale.
        """
        cutoff = time.time() - self._ttl
        row = self._conn.execute(
            "SELECT html, load_time_seconds FROM html_cache "
            "WHERE url = ? AND fetched_at > ?",
            (url, cutoff),
        ).fetchone()
        return (row[0], row[1]) if row else None

    def set(self, url: str, html: str, load_time_seconds: float) -> None:
        """Insert or replace the cached HTML for *url*."""
        self._conn.execute(
            "INSERT OR REPLACE INTO html_cache (url, html, load_time_seconds, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (url, html, load_time_seconds, time.time()),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def _ensure_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS html_cache (
                url                 TEXT PRIMARY KEY,
                html                TEXT NOT NULL,
                load_time_seconds   REAL NOT NULL,
                fetched_at          REAL NOT NULL
            )
        """)
        self._conn.commit()


# ── Fetcher ───────────────────────────────────────────────────────────────────

class WebFetcher:
    """
    Fetches web pages for the audit engine.

    Features
    --------
    - Checks the HTML cache before every request (re-audits are free).
    - Fetches and caches robots.txt per host; defaults to permissive.
    - Enforces a per-host minimum delay between requests.
    - Times the network round-trip for the slow_load check.
    - Never raises — every failure is returned as a FetchResult with .error set.
    - Parses HTML with BeautifulSoup/lxml for immediate use by audit checks.
    """

    _per_host_delay: float = 1.0    # seconds between requests to the same host
    _timeout: float = 10.0          # seconds before giving up on a request

    def __init__(self, config: AppConfig) -> None:
        """Initialise the fetcher and open the HTML cache."""
        self._cache = HtmlCache(config.cache)
        self._robots: dict[str, Optional[RobotFileParser]] = {}
        self._last_host_time: dict[str, float] = {}

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _USER_AGENT})

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch(self, url: str) -> FetchResult:
        """
        Fetch *url* and return a FetchResult.

        Order of operations:
          1. Normalise URL (add https:// if missing).
          2. Check HTML cache — return immediately if cached and fresh.
          3. Check robots.txt — return error result if disallowed.
          4. Apply per-host rate limit.
          5. Make the GET request with timeout; measure load time.
          6. Cache successful responses.
          7. Parse HTML into BeautifulSoup.

        Never raises.  Network/timeout/parse errors are captured in .error.
        """
        url = _normalise_url(url)

        # 1. Cache check
        cached = self._cache.get(url)
        if cached is not None:
            html, load_time = cached
            logger.debug("html cache hit: %s", url)
            return FetchResult(
                url=url,
                final_url=url,
                status_code=200,
                html=html,
                soup=_parse_html(html, url),
                load_time_seconds=load_time,
                from_cache=True,
            )

        # 2. robots.txt
        if not self._is_allowed_by_robots(url):
            logger.info("robots.txt disallows: %s", url)
            return FetchResult(
                url=url, final_url=url, status_code=None, html=None,
                error="Blocked by robots.txt",
            )

        # 3. Rate limit
        host = urlparse(url).netloc
        self._apply_rate_limit(host)

        # 4. Fetch
        start = time.time()
        try:
            resp = self._session.get(
                url,
                timeout=self._timeout,
                allow_redirects=True,
                stream=True,           # stream so we can enforce size cap
            )
            # Read up to _MAX_CONTENT_BYTES
            content_bytes = resp.raw.read(_MAX_CONTENT_BYTES, decode_content=True)
            load_time = time.time() - start
            self._last_host_time[host] = time.time()

            # Decode using charset from headers, fall back to utf-8
            encoding = resp.encoding or "utf-8"
            try:
                html = content_bytes.decode(encoding, errors="replace")
            except (LookupError, UnicodeDecodeError):
                html = content_bytes.decode("utf-8", errors="replace")

            result = FetchResult(
                url=url,
                final_url=resp.url,
                status_code=resp.status_code,
                html=html if resp.ok else None,
                soup=_parse_html(html, resp.url) if resp.ok else None,
                load_time_seconds=load_time,
            )

            if resp.ok and html:
                self._cache.set(url, html, load_time)

            return result

        except requests.Timeout:
            elapsed = time.time() - start
            logger.warning("timeout (%.1fs): %s", elapsed, url)
            return FetchResult(
                url=url, final_url=url, status_code=None, html=None,
                load_time_seconds=elapsed,
                error=f"Timed out after {self._timeout:.0f}s",
            )
        except requests.RequestException as exc:
            logger.warning("fetch error %s: %s", url, exc)
            return FetchResult(
                url=url, final_url=url, status_code=None, html=None,
                error=str(exc),
            )

    def fetch_site_pages(
        self,
        homepage: FetchResult,
        paths: list[str] = AUDIT_PATHS,
    ) -> dict[str, FetchResult]:
        """
        Fetch common sub-paths relative to the homepage URL.

        Always includes "/" (the homepage).  For each path in *paths*, attempts
        a fetch and records the result regardless of status code (so audit checks
        can distinguish "404" from "connection refused").

        Returns a dict mapping path string → FetchResult.
        """
        results: dict[str, FetchResult] = {"/": homepage}

        if not homepage.ok:
            # No point fetching sub-pages if homepage failed
            return results

        base = _base_url(homepage.final_url)

        for path in paths:
            url = base + path
            result = self.fetch(url)
            results[path] = result
            if result.ok:
                logger.debug("fetched sub-page %s → %d", path, result.status_code or 0)

        return results

    def close(self) -> None:
        """Close the HTML cache database connection."""
        self._cache.close()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _is_allowed_by_robots(self, url: str) -> bool:
        """
        Return True if our User-Agent is allowed to fetch *url*.

        Fetches and caches robots.txt per host using our own session (respects
        our timeout).  Returns True (permissive) if robots.txt is unreachable.
        """
        parsed = urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}"

        if host_key not in self._robots:
            robots_url = f"{host_key}/robots.txt"
            try:
                resp = self._session.get(robots_url, timeout=5, allow_redirects=True)
                if resp.ok:
                    rp = RobotFileParser()
                    rp.set_url(robots_url)
                    rp.parse(resp.text.splitlines())
                    self._robots[host_key] = rp
                else:
                    self._robots[host_key] = None   # no robots.txt → permissive
            except Exception:
                self._robots[host_key] = None       # unreachable → permissive

        rp = self._robots[host_key]
        if rp is None:
            return True
        return rp.can_fetch(_USER_AGENT, url)

    def _apply_rate_limit(self, host: str) -> None:
        """Block until _per_host_delay seconds have passed since the last request to *host*."""
        last = self._last_host_time.get(host, 0.0)
        elapsed = time.time() - last
        if elapsed < self._per_host_delay:
            time.sleep(self._per_host_delay - elapsed)


# ── Standalone helpers ────────────────────────────────────────────────────────

def extract_contact_info(
    pages: dict[str, FetchResult],
) -> tuple[Optional[str], Optional[str]]:
    """
    Scan fetched pages for a contact email and the /contact page URL.

    Searches for:
      - mailto: links  (email address)
      - bare email patterns in visible text
      - the URL of any page under a /contact path

    Returns (email_address | None, contact_page_url | None).
    """
    email: Optional[str] = None
    contact_url: Optional[str] = None
    email_pattern = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

    for path, result in pages.items():
        if not result.ok or result.soup is None:
            continue

        # Track the contact page URL
        if "contact" in path and contact_url is None:
            contact_url = result.final_url

        if email is not None:
            continue  # already found one

        # mailto: links
        for a in result.soup.find_all("a", href=True):
            href: str = a["href"]
            if href.lower().startswith("mailto:"):
                addr = href[7:].split("?")[0].strip()
                if addr and "@" in addr and "." in addr:
                    email = addr
                    break

        # Bare email in page text (scan first 5000 chars to stay fast)
        if email is None:
            text = result.soup.get_text(" ", strip=True)[:5_000]
            match = email_pattern.search(text)
            if match:
                candidate = match.group()
                # Skip generic/placeholder addresses
                if not any(s in candidate.lower() for s in ("example.", "domain.", "email@")):
                    email = candidate

    return email, contact_url


def _normalise_url(url: str) -> str:
    """Add https:// if the URL has no scheme."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _base_url(url: str) -> str:
    """Return scheme + host from a URL, with no trailing slash."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _parse_html(html: str, url: str) -> Optional[BeautifulSoup]:
    """Parse *html* with lxml, returning a BeautifulSoup object or None on failure."""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception as exc:
        logger.warning("HTML parse error for %s: %s", url, exc)
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception:
            return None
