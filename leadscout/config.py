"""
Config loader.

Reads config.yaml into typed dataclasses and loads the Google API key from .env.
Raises clear, descriptive errors early so the user finds out about missing or
invalid config before any API calls are made.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


# ── Sub-config dataclasses ────────────────────────────────────────────────────


@dataclass
class FilterConfig:
    """Lead-filter settings."""

    min_rating: float
    min_review_count: int
    exclude_chains: bool


@dataclass
class AuditConfig:
    """Audit engine settings."""

    enabled_checks: list[str]
    page_load_threshold_seconds: float
    stale_copyright_years: int
    min_listing_photos: int
    min_review_count_presence: int


@dataclass
class ScoringConfig:
    """Lead-scoring weight settings."""

    weight_rating: float
    weight_review_count: float
    weight_gap_count: float


@dataclass
class OutputConfig:
    """Spreadsheet output settings."""

    format: str    # "xlsx" or "csv"
    path: Path


@dataclass
class CacheConfig:
    """SQLite cache settings."""

    db_path: Path
    ttl_days: int


@dataclass
class AppConfig:
    """Root config object passed throughout the application."""

    search_areas: list[str]
    categories: list[str]
    filters: FilterConfig
    audit: AuditConfig
    scoring: ScoringConfig
    output: OutputConfig
    cache: CacheConfig


# ── Public loaders ────────────────────────────────────────────────────────────


def load_config(path: Path = Path("config.yaml")) -> AppConfig:
    """
    Read *path* (YAML) and return a validated AppConfig.

    Raises FileNotFoundError if the file is missing.
    Raises ValueError with a descriptive message on invalid or missing fields.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Make sure you are running from the project root, or pass --config <path>."
        )

    with open(path, encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    if not raw:
        raise ValueError(f"Config file is empty: {path}")

    try:
        return AppConfig(
            search_areas=_require_str_list(raw, "search_areas"),
            categories=_require_str_list(raw, "categories"),
            filters=_parse_filters(raw.get("filters") or {}),
            audit=_parse_audit(raw.get("audit") or {}),
            scoring=_parse_scoring(raw.get("scoring") or {}),
            output=_parse_output(raw.get("output") or {}),
            cache=_parse_cache(raw.get("cache") or {}),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Config error in {path}: {exc}") from exc


def load_api_key() -> str:
    """
    Load GOOGLE_PLACES_API_KEY from the .env file (or existing environment).

    Raises RuntimeError with setup instructions if the key is absent or blank.
    """
    load_dotenv(override=False)  # don't clobber values already in the environment
    key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GOOGLE_PLACES_API_KEY is not set.\n"
            "  1. Copy .env.example to .env\n"
            "  2. Add your Google Cloud API key\n"
            "  See README.md → 'Google Cloud Setup' for instructions."
        )
    return key


# ── Section parsers ───────────────────────────────────────────────────────────


def _parse_filters(raw: dict[str, Any]) -> FilterConfig:
    """Build a FilterConfig from the raw YAML dict, applying defaults."""
    return FilterConfig(
        min_rating=float(raw.get("min_rating", 3.5)),
        min_review_count=int(raw.get("min_review_count", 10)),
        exclude_chains=bool(raw.get("exclude_chains", True)),
    )


def _parse_audit(raw: dict[str, Any]) -> AuditConfig:
    """Build an AuditConfig from the raw YAML dict, applying defaults."""
    checks = raw.get("enabled_checks")
    if checks is None:
        # Default: all known checks enabled
        checks = _default_checks()
    if not isinstance(checks, list):
        raise ValueError("audit.enabled_checks must be a list of check names.")

    return AuditConfig(
        enabled_checks=[str(c) for c in checks],
        page_load_threshold_seconds=float(
            raw.get("page_load_threshold_seconds", 4.0)
        ),
        stale_copyright_years=int(raw.get("stale_copyright_years", 2)),
        min_listing_photos=int(raw.get("min_listing_photos", 5)),
        min_review_count_presence=int(raw.get("min_review_count_presence", 25)),
    )


def _parse_scoring(raw: dict[str, Any]) -> ScoringConfig:
    """Build a ScoringConfig from the raw YAML dict, applying defaults."""
    return ScoringConfig(
        weight_rating=float(raw.get("weight_rating", 1.0)),
        weight_review_count=float(raw.get("weight_review_count", 1.0)),
        weight_gap_count=float(raw.get("weight_gap_count", 2.0)),
    )


def _parse_output(raw: dict[str, Any]) -> OutputConfig:
    """Build an OutputConfig from the raw YAML dict, applying defaults."""
    fmt = str(raw.get("format", "xlsx")).lower()
    if fmt not in ("xlsx", "csv"):
        raise ValueError(
            f"output.format must be 'xlsx' or 'csv', got '{fmt}'."
        )
    return OutputConfig(
        format=fmt,
        path=Path(raw.get("path", "./output/leads.xlsx")),
    )


def _parse_cache(raw: dict[str, Any]) -> CacheConfig:
    """Build a CacheConfig from the raw YAML dict, applying defaults."""
    return CacheConfig(
        db_path=Path(raw.get("db_path", "./cache/leadscout.db")),
        ttl_days=int(raw.get("ttl_days", 30)),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_str_list(raw: dict[str, Any], key: str) -> list[str]:
    """Return raw[key] as a non-empty list of strings, or raise ValueError."""
    value = raw.get(key)
    if not value:
        raise ValueError(
            f"'{key}' is required in config.yaml and must not be empty."
        )
    if not isinstance(value, list):
        raise ValueError(f"'{key}' must be a list, got {type(value).__name__}.")
    return [str(v) for v in value]


def _default_checks() -> list[str]:
    """Return the canonical ordered list of all built-in check names."""
    return [
        # Conversion — high severity
        "no_contact_form",
        "no_click_to_call",
        "no_visible_contact_info",
        "no_booking_or_ordering",
        "no_map_or_directions",
        # Trust — medium severity
        "no_https",
        "no_testimonials",
        "no_real_photos",
        "stale_copyright",
        "broken_links_or_images",
        # Technical / SEO — medium severity
        "not_mobile_responsive",
        "slow_load",
        "no_analytics",
        "weak_seo",
        "no_favicon",
        # Presence — medium severity (both tracks)
        "few_listing_photos",
        "low_review_count",
    ]
