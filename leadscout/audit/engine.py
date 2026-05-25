"""
Audit engine orchestrator.

Maintains a registry of check functions, fetches the relevant pages for each
business, runs all enabled checks, and assembles the results into an AuditReport.

Adding a new check:
  1. Write a function with the CheckFn signature in the appropriate checks/ file.
  2. Decorate it with @register("your_check_name").
  3. Add "your_check_name" to enabled_checks in config.yaml.
  That's all — no changes to this file are needed.
"""

from __future__ import annotations

import logging
from typing import Callable

from leadscout.audit.models import AuditReport, CheckResult
from leadscout.config import AppConfig
from leadscout.fetcher import FetchResult, WebFetcher, extract_contact_info
from leadscout.places import Business

logger = logging.getLogger(__name__)

# ── Check type + registry ─────────────────────────────────────────────────────

CheckFn = Callable[[dict[str, FetchResult], Business, AppConfig], CheckResult]

# Populated at import time by @register decorators in checks/ modules.
_REGISTRY: dict[str, CheckFn] = {}

# These checks only use Places data — they run even when there is no website.
_PRESENCE_ONLY_CHECKS: frozenset[str] = frozenset({"few_listing_photos", "low_review_count"})


def register(check_name: str) -> Callable[[CheckFn], CheckFn]:
    """
    Decorator that registers a check function under *check_name*.

    Usage::

        @register("no_contact_form")
        def check_no_contact_form(pages, business, config) -> CheckResult:
            ...
    """
    def decorator(fn: CheckFn) -> CheckFn:
        if check_name in _REGISTRY:
            raise ValueError(f"Check '{check_name}' is already registered.")
        _REGISTRY[check_name] = fn
        return fn
    return decorator


def list_registered_checks() -> list[str]:
    """Return the names of all registered checks in registration order."""
    return list(_REGISTRY.keys())


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_audit(
    business: Business,
    fetcher: WebFetcher,
    config: AppConfig,
) -> AuditReport:
    """
    Run all enabled checks for *business* and return an AuditReport.

    Track A (no website): only presence checks run.
    Track B (has website): pages are fetched, all enabled checks run.

    Individual check failures are caught and logged — a broken check never
    aborts the audit for the rest of the business.
    """
    if business.website_uri:
        pages = _fetch_pages(business.website_uri, fetcher)
        email, contact_url = extract_contact_info(pages)
        fetch_errors = [r.error for r in pages.values() if r.error is not None]
    else:
        pages = {}
        email = contact_url = None
        fetch_errors = []

    report = AuditReport(
        place_id=business.place_id,
        contact_email=email,
        contact_url=contact_url,
        fetch_errors=fetch_errors,
    )

    for check_name in config.audit.enabled_checks:
        fn = _REGISTRY.get(check_name)
        if fn is None:
            logger.warning("check '%s' is not registered — skipping", check_name)
            continue

        # Skip site-dependent checks for Track A businesses
        if not business.website_uri and check_name not in _PRESENCE_ONLY_CHECKS:
            continue

        try:
            result = fn(pages, business, config)
            report.results.append(result)
        except Exception as exc:
            logger.warning("check '%s' raised for %s: %s", check_name, business.place_id, exc)

    return report


def _fetch_pages(website_uri: str, fetcher: WebFetcher) -> dict[str, FetchResult]:
    """Fetch the homepage and all standard audit sub-paths."""
    homepage = fetcher.fetch(website_uri)
    return fetcher.fetch_site_pages(homepage)


# ── Import check modules ──────────────────────────────────────────────────────

def _import_checks() -> None:
    """Force-import all check modules so their @register decorators fire."""
    import leadscout.audit.checks.conversion  # noqa: F401
    import leadscout.audit.checks.trust       # noqa: F401
    import leadscout.audit.checks.technical   # noqa: F401
    import leadscout.audit.checks.presence    # noqa: F401


_import_checks()
