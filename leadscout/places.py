"""
Google Places API (New) client.

Wraps the searchText endpoint with:
  - SQLite caching: search results (query → place_id list) AND place details
    (place_id → full payload).  Re-runs within ttl_days cost $0.
  - Rate limiting: at most 5 requests per second.
  - Exponential back-off: retries on HTTP 429 and 5xx responses.

Two entry points:
  search_all(config, api_key)                -> list[Business]   (batch mode)
  search_interactive(area, category, ...)    -> list[Business]   (interactive mode)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

from leadscout.config import AppConfig, CacheConfig

logger = logging.getLogger(__name__)

# ── Field mask ────────────────────────────────────────────────────────────────
# Passed as X-Goog-FieldMask header on every searchText call.
# Requesting contact + atmosphere fields costs slightly more per call —
# but lets us skip a separate Place Details call entirely.
_SEARCH_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.primaryType",
    "places.formattedAddress",
    "places.location",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.photos",
    "places.businessStatus",
])


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Business:
    """A single business record, ready for filtering and auditing."""

    place_id: str
    name: str
    category: str           # the search term used, e.g. "barbershop"
    primary_type: str       # Google's type slug, e.g. "barber_shop"
    address: str
    search_area: str        # e.g. "Seattle, WA"
    latitude: float
    longitude: float
    phone: Optional[str]
    website_uri: Optional[str]
    rating: Optional[float]
    review_count: Optional[int]
    photo_count: int
    business_status: str    # "OPERATIONAL", "CLOSED_PERMANENTLY", etc.
    is_chain: bool = False


# ── SQLite cache ──────────────────────────────────────────────────────────────

class PlacesCache:
    """
    SQLite-backed cache stored in the same DB file as the HTML cache.

    Two tables:
      search_cache  — maps a query string to the list of place_ids it returned.
      places_cache  — maps a place_id to its full raw API payload (JSON).

    Both tables respect ttl_days: stale rows are ignored and overwritten.
    """

    def __init__(self, config: CacheConfig) -> None:
        """Open (or create) the DB and initialise tables."""
        config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(config.db_path), check_same_thread=False)
        self._ttl = config.ttl_days * 86_400  # days → seconds
        self._ensure_schema()

    # ── Search-level cache ────────────────────────────────────────────────────

    def get_search(self, query: str) -> Optional[list[str]]:
        """Return the cached list of place_ids for *query*, or None if stale/missing."""
        cutoff = time.time() - self._ttl
        row = self._conn.execute(
            "SELECT place_ids FROM search_cache WHERE query = ? AND fetched_at > ?",
            (query, cutoff),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def set_search(self, query: str, place_ids: list[str]) -> None:
        """Store the place_ids returned by *query*."""
        self._conn.execute(
            "INSERT OR REPLACE INTO search_cache (query, place_ids, fetched_at) "
            "VALUES (?, ?, ?)",
            (query, json.dumps(place_ids), time.time()),
        )
        self._conn.commit()

    # ── Place-level cache ─────────────────────────────────────────────────────

    def get_place(self, place_id: str) -> Optional[dict[str, Any]]:
        """Return the cached payload for *place_id*, or None if stale/missing."""
        cutoff = time.time() - self._ttl
        row = self._conn.execute(
            "SELECT payload FROM places_cache WHERE place_id = ? AND fetched_at > ?",
            (place_id, cutoff),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def set_place(self, place_id: str, payload: dict[str, Any]) -> None:
        """Insert or replace the payload for *place_id*."""
        self._conn.execute(
            "INSERT OR REPLACE INTO places_cache (place_id, payload, fetched_at) "
            "VALUES (?, ?, ?)",
            (place_id, json.dumps(payload), time.time()),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def _ensure_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS search_cache (
                query       TEXT PRIMARY KEY,
                place_ids   TEXT NOT NULL,
                fetched_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS places_cache (
                place_id    TEXT PRIMARY KEY,
                payload     TEXT NOT NULL,
                fetched_at  REAL NOT NULL
            );
        """)
        self._conn.commit()


# ── API client ────────────────────────────────────────────────────────────────

class PlacesClient:
    """
    Thin, cache-aware wrapper around the Places API (New) searchText endpoint.

    Rate limit : 5 requests/second (configurable via _min_interval).
    Retry      : up to _max_retries attempts with doubling back-off on 429/5xx.
    Pagination : up to 3 pages per query (≤ 60 results).
    """

    _BASE_URL = "https://places.googleapis.com/v1"
    _min_interval: float = 0.2    # seconds between requests (= 5 req/s)
    _max_retries: int = 4
    _backoff_base: float = 1.0    # seconds; doubles each retry

    def __init__(self, api_key: str, cache: PlacesCache) -> None:
        self._cache = cache
        self._last_request: float = 0.0
        self._session = requests.Session()
        self._session.headers.update({
            "X-Goog-Api-Key": api_key,
            "Content-Type": "application/json",
        })

    def search_text(self, query: str) -> list[dict[str, Any]]:
        """
        Search for places matching *query* and return raw place dicts.

        Cache strategy:
          1. If the query is in search_cache (and fresh), reconstruct results
             from places_cache — zero API calls.
          2. Otherwise, call the API (with pagination), store each place in
             places_cache, and store the id list in search_cache.
        """
        cached_ids = self._cache.get_search(query)
        if cached_ids is not None:
            logger.debug("search cache hit: %r (%d places)", query, len(cached_ids))
            places = [
                p for pid in cached_ids
                if (p := self._cache.get_place(pid)) is not None
            ]
            return places

        logger.info("calling API: %r", query)
        all_places: list[dict[str, Any]] = []
        page_token: Optional[str] = None

        for _page in range(3):  # max 3 pages = 60 results
            body: dict[str, Any] = {
                "textQuery": query,
                "maxResultCount": 20,
                "languageCode": "en",
            }
            if page_token:
                body["pageToken"] = page_token

            data = self._post("places:searchText", body, _SEARCH_FIELD_MASK)
            places = data.get("places", [])
            all_places.extend(places)

            for place in places:
                if pid := place.get("id"):
                    self._cache.set_place(pid, place)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        place_ids = [p["id"] for p in all_places if "id" in p]
        self._cache.set_search(query, place_ids)

        return all_places

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        field_mask: str,
    ) -> dict[str, Any]:
        """POST to *path* with retry / backoff; return the parsed JSON body."""
        url = f"{self._BASE_URL}/{path}"
        headers = {"X-Goog-FieldMask": field_mask}

        for attempt in range(self._max_retries):
            self._wait_for_rate_limit()
            try:
                resp = self._session.post(
                    url, json=payload, headers=headers, timeout=10
                )
                self._last_request = time.time()

                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = self._backoff_base * (2 ** attempt)
                    logger.warning(
                        "HTTP %s — retrying in %.1fs (attempt %d/%d)",
                        resp.status_code, wait, attempt + 1, self._max_retries,
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()

            except requests.RequestException as exc:
                if attempt == self._max_retries - 1:
                    raise
                wait = self._backoff_base * (2 ** attempt)
                logger.warning("request error: %s — retrying in %.1fs", exc, wait)
                time.sleep(wait)

        raise RuntimeError(f"All {self._max_retries} retries exhausted for {path}")

    def _wait_for_rate_limit(self) -> None:
        """Block until at least _min_interval seconds have passed since the last request."""
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)


# ── Chain detection ───────────────────────────────────────────────────────────

def load_chain_fragments(
    path: Path = Path("data/chain_fragments.txt"),
) -> list[str]:
    """
    Load chain name fragments from *path*, ignoring comments and blank lines.

    Returns lowercase strings for case-insensitive matching.
    Returns an empty list (no filtering) if the file is missing.
    """
    if not path.exists():
        logger.warning("chain_fragments.txt not found at %s — chain filtering disabled", path)
        return []
    fragments = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            fragments.append(line.lower())
    return fragments


def is_chain(name: str, fragments: list[str]) -> bool:
    """Return True if any fragment from *fragments* appears in *name* (case-insensitive)."""
    name_lower = name.lower()
    return any(frag in name_lower for frag in fragments)


# ── Raw payload → Business ────────────────────────────────────────────────────

def _raw_to_business(
    raw: dict[str, Any],
    search_area: str,
    category: str,
    chain_fragments: list[str],
) -> Business:
    """Map a raw Places API place dict to a typed Business dataclass."""
    name = raw.get("displayName", {}).get("text", "Unknown")
    loc = raw.get("location", {})

    return Business(
        place_id=raw["id"],
        name=name,
        category=category,
        primary_type=raw.get("primaryType", ""),
        address=raw.get("formattedAddress", ""),
        search_area=search_area,
        latitude=float(loc.get("latitude", 0.0)),
        longitude=float(loc.get("longitude", 0.0)),
        phone=raw.get("nationalPhoneNumber") or None,
        website_uri=raw.get("websiteUri") or None,
        rating=raw.get("rating"),
        review_count=raw.get("userRatingCount"),
        photo_count=len(raw.get("photos", [])),
        business_status=raw.get("businessStatus", "UNKNOWN"),
        is_chain=is_chain(name, chain_fragments),
    )


# ── Filters ───────────────────────────────────────────────────────────────────

def _passes_filters(business: Business, config: AppConfig) -> bool:
    """Return True if *business* meets all configured lead filters."""
    if business.business_status == "CLOSED_PERMANENTLY":
        return False
    if business.rating is None or business.rating < config.filters.min_rating:
        return False
    if business.review_count is None or business.review_count < config.filters.min_review_count:
        return False
    if config.filters.exclude_chains and business.is_chain:
        return False
    return True


# ── Core search logic ─────────────────────────────────────────────────────────

def _search_one(
    area: str,
    category: str,
    config: AppConfig,
    client: PlacesClient,
    chain_fragments: list[str],
    seen: set[str],
) -> list[Business]:
    """
    Search for one (area, category) pair and return qualifying Business objects.

    *seen* is shared across calls to deduplicate place_ids across searches.
    """
    query = f"{category} in {area}"
    try:
        raw_places = client.search_text(query)
    except Exception as exc:
        logger.error("search failed for %r: %s", query, exc)
        return []

    results: list[Business] = []
    for raw in raw_places:
        place_id = raw.get("id", "")
        if not place_id or place_id in seen:
            continue
        seen.add(place_id)
        try:
            business = _raw_to_business(raw, area, category, chain_fragments)
        except Exception as exc:
            logger.warning("skipping malformed place %s: %s", place_id, exc)
            continue
        if _passes_filters(business, config):
            results.append(business)

    return results


# ── Public entry points ───────────────────────────────────────────────────────

def search_all(config: AppConfig, api_key: str) -> list[Business]:
    """
    Batch mode: search every (area, category) pair from config.

    Returns a deduplicated, filter-passing list of Business objects.
    Uses the cache aggressively — re-runs within ttl_days make zero API calls.
    """
    cache = PlacesCache(config.cache)
    client = PlacesClient(api_key, cache)
    chain_fragments = load_chain_fragments()
    seen: set[str] = set()
    results: list[Business] = []

    try:
        for area in config.search_areas:
            for category in config.categories:
                results.extend(
                    _search_one(area, category, config, client, chain_fragments, seen)
                )
    finally:
        cache.close()

    return results


def search_interactive(
    area: str,
    category: str,
    config: AppConfig,
    api_key: str,
) -> list[Business]:
    """
    Interactive mode: search a single (area, category) pair on demand.

    Same caching and filtering as search_all — re-running the same query
    within ttl_days is free.
    """
    cache = PlacesCache(config.cache)
    client = PlacesClient(api_key, cache)
    chain_fragments = load_chain_fragments()

    try:
        return _search_one(area, category, config, client, chain_fragments, set())
    finally:
        cache.close()
