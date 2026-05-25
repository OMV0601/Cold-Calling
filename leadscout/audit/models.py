"""
Audit data models.

Defines the CheckResult dataclass (one per check) and AuditReport (all results
for a single business).  Both are pure data — no logic here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Severity = Literal["high", "medium", "low"]


@dataclass
class CheckResult:
    """
    The outcome of a single audit check against one business.

    Attributes:
        check_name:  The registry name of the check, e.g. "no_contact_form".
        passed:      True if the site passes (no problem found).
        severity:    "high" | "medium" | "low" — only meaningful when not passed.
        finding:     Plain-English description of what was found (or not found).
        pitch_angle: One sentence ready to paste into a cold email.
    """

    check_name: str
    passed: bool
    severity: Severity
    finding: str
    pitch_angle: str


@dataclass
class AuditReport:
    """
    All audit results for one business.

    Attributes:
        place_id:       Identifies the business this report belongs to.
        results:        One CheckResult per enabled check that was run.
        contact_email:  Email scraped from the site, if found.
        contact_url:    URL of the /contact page, if found.
        fetch_errors:   Any URLs that failed to load (for transparency).
    """

    place_id: str
    results: list[CheckResult] = field(default_factory=list)
    contact_email: str | None = None
    contact_url: str | None = None
    fetch_errors: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        """Return only the checks that did not pass."""
        return [r for r in self.results if not r.passed]

    @property
    def high_severity_failures(self) -> list[CheckResult]:
        """Return failures with severity == 'high'."""
        return [r for r in self.failures if r.severity == "high"]

    @property
    def gap_count(self) -> int:
        """Total number of failed checks."""
        return len(self.failures)
