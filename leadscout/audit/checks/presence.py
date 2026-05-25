"""
Presence checks (severity: medium).

These use data already in the Business record from Google Places — no web
fetch is needed.  They run for BOTH Track A and Track B leads.
"""

from __future__ import annotations

from leadscout.audit.engine import register
from leadscout.audit.models import CheckResult
from leadscout.config import AppConfig
from leadscout.fetcher import FetchResult
from leadscout.places import Business


@register("few_listing_photos")
def check_few_listing_photos(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """
    Flag a listing with fewer photos than the configured minimum.

    Google Places API returns up to 10 photo references; if a listing
    has photos at all, business.photo_count reflects how many Google
    returned (capped at 10).  Zero photos is a strong negative signal.
    """
    threshold = config.audit.min_listing_photos
    count = business.photo_count

    if count >= threshold:
        return CheckResult(
            check_name="few_listing_photos",
            passed=True,
            severity="medium",
            finding=f"Listing has {count} photos (threshold: {threshold}).",
            pitch_angle="",
        )

    return CheckResult(
        check_name="few_listing_photos",
        passed=False,
        severity="medium",
        finding=f"Listing has only {count} photo(s) on Google (threshold: {threshold}).",
        pitch_angle=(
            f"The Google listing only has {count} photo{'s' if count != 1 else ''} — "
            "listings with more photos get significantly more clicks and calls."
        ),
    )


@register("low_review_count")
def check_low_review_count(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """
    Flag a listing with fewer reviews than the presence-check threshold.

    This is a separate, higher bar from the lead-filter min_review_count:
    a business can have enough reviews to qualify as a lead but still
    benefit from an active review-generation strategy.
    """
    threshold = config.audit.min_review_count_presence
    count = business.review_count or 0

    if count >= threshold:
        return CheckResult(
            check_name="low_review_count",
            passed=True,
            severity="medium",
            finding=f"Listing has {count} reviews (threshold: {threshold}).",
            pitch_angle="",
        )

    return CheckResult(
        check_name="low_review_count",
        passed=False,
        severity="medium",
        finding=f"Only {count} Google reviews (threshold: {threshold}).",
        pitch_angle=(
            f"With only {count} Google review{'s' if count != 1 else ''}, the listing "
            "doesn't stand out against competitors — a simple review-ask strategy "
            "could change that fast."
        ),
    )
