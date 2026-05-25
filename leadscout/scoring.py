"""
Lead scoring and opening-line pitch synthesis.

Scoring formula (all weights configurable in config.yaml):
  rating_part  = rating  * weight_rating
  review_part  = log(review_count + 1) * weight_review_count
  gap_part     = (high_gap_count * 2 + medium_gap_count * 1) * weight_gap_count
  total        = rating_part + review_part + gap_part

Higher score = stronger lead.  More gaps means more concrete value to offer.
Higher rating + more reviews means the business is established and worth pitching.

Public surface:
  score_lead(business, report, config)  -> LeadScore
  synthesise_pitch(business, report)    -> str
  format_gap_list(report)               -> str   (for spreadsheet output)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from leadscout.audit.models import AuditReport, CheckResult
from leadscout.config import AppConfig
from leadscout.places import Business


# ── Score dataclass ───────────────────────────────────────────────────────────

@dataclass
class LeadScore:
    """
    Numeric lead score broken into its components for transparency.

    Attributes:
        total:        Final weighted score (sort leads by this, descending).
        rating_part:  Contribution from the business rating.
        review_part:  Contribution from review count (log-scaled).
        gap_part:     Contribution from the number and severity of gaps found.
    """

    total: float
    rating_part: float
    review_part: float
    gap_part: float


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_lead(
    business: Business,
    report: Optional[AuditReport],
    config: AppConfig,
) -> LeadScore:
    """
    Compute a LeadScore for *business*.

    *report* may be None for Track A leads with no audit results — the score
    still reflects rating + reviews, with gap_part coming from any presence
    checks that ran.
    """
    w = config.scoring
    rating = business.rating or 0.0
    review_count = business.review_count or 0

    rating_part = rating * w.weight_rating
    review_part = math.log(review_count + 1) * w.weight_review_count

    if report:
        high_count = sum(1 for r in report.failures if r.severity == "high")
        med_count  = sum(1 for r in report.failures if r.severity == "medium")
    else:
        high_count = med_count = 0

    gap_part = (high_count * 2 + med_count * 1) * w.weight_gap_count
    total = rating_part + review_part + gap_part

    return LeadScore(
        total=round(total, 2),
        rating_part=round(rating_part, 2),
        review_part=round(review_part, 2),
        gap_part=round(gap_part, 2),
    )


# ── Pitch synthesis ───────────────────────────────────────────────────────────

def synthesise_pitch(
    business: Business,
    report: Optional[AuditReport],
) -> str:
    """
    Generate a 2–3 sentence cold-email opener for *business*.

    Pulls the top 2–3 highest-severity pitch angles from *report*, strings
    them together into a paragraph personalised with the business name.

    Track A (no website): uses a different, tailored opener.
    Track B (website with gaps): leads with a concise hook then lists gaps.
    Track B (no gaps found): returns a minimal fallback.
    """
    name = business.name
    category = business.category
    rating = business.rating or 0.0
    reviews = business.review_count or 0

    # ── Track A: no website ───────────────────────────────────────────────────
    if not business.website_uri:
        presence_angles = _top_pitch_angles(report, n=2) if report else []

        base = (
            f"I was searching for {category}s in your area and came across "
            f"{name} on Google — {reviews} reviews and a {rating:.1f}-star rating "
            f"is genuinely impressive. "
            f"One thing I noticed is that you don't have a website, which means "
            f"anyone who finds your listing online can't learn more about you or "
            f"get in touch without calling first."
        )
        if presence_angles:
            base += " " + " ".join(presence_angles)
        return base.strip()

    # ── Track B: has website, no gaps found ───────────────────────────────────
    if not report or not report.failures:
        return (
            f"I was looking at {name}'s website and it's in solid shape — "
            f"I'd love to connect and see if there are any areas worth improving."
        )

    # ── Track B: has website, gaps found ─────────────────────────────────────
    top_angles = _top_pitch_angles(report, n=3)
    if not top_angles:
        return ""

    gap_count = report.gap_count
    gap_word = "a few things" if gap_count >= 3 else ("two things" if gap_count == 2 else "something")

    opener = (
        f"I was taking a look at {name}'s website and noticed {gap_word} "
        f"that might be costing you new customers. "
    )
    return (opener + " ".join(top_angles)).strip()


# ── Gap list formatter (used by output.py) ────────────────────────────────────

def format_gap_list(report: Optional[AuditReport]) -> str:
    """
    Return a pipe-separated, severity-sorted string of failed check names.

    Example: "[HIGH] no_contact_form | [HIGH] no_click_to_call | [MED] no_https"
    Returns an empty string when there are no gaps.
    """
    if not report or not report.failures:
        return ""

    sorted_failures = _sort_by_severity(report.failures)
    parts = []
    for r in sorted_failures:
        tag = "[HIGH]" if r.severity == "high" else "[MED]"
        parts.append(f"{tag} {r.check_name}")
    return " | ".join(parts)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _top_pitch_angles(report: Optional[AuditReport], n: int = 3) -> list[str]:
    """
    Return up to *n* non-empty pitch angles from the highest-severity failures.
    """
    if not report:
        return []
    sorted_failures = _sort_by_severity(report.failures)
    return [
        r.pitch_angle
        for r in sorted_failures
        if r.pitch_angle.strip()
    ][:n]


def _sort_by_severity(failures: list[CheckResult]) -> list[CheckResult]:
    """Sort failures: high first, then medium, preserving original order within each group."""
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(failures, key=lambda r: order.get(r.severity, 3))
