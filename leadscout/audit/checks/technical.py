"""
Technical / SEO checks (severity: medium).

These detect technical deficiencies that hurt discoverability and user
experience — often invisible to the business owner but very fixable.
"""

from __future__ import annotations

import re

from leadscout.audit.engine import register
from leadscout.audit.models import CheckResult
from leadscout.config import AppConfig
from leadscout.fetcher import FetchResult
from leadscout.places import Business

# Analytics signatures to search for in raw HTML
_ANALYTICS_PATTERNS = (
    re.compile(r"gtag\s*\(", re.I),                        # GA4 / gtag.js
    re.compile(r"google-analytics\.com/analytics\.js"),    # Universal Analytics
    re.compile(r"googletagmanager\.com/gtm\.js"),          # Google Tag Manager
    re.compile(r"GTM-[A-Z0-9]{4,}"),                       # GTM container ID
    re.compile(r"G-[A-Z0-9]{6,}"),                         # GA4 measurement ID
    re.compile(r"UA-\d{4,}-\d+"),                          # Universal Analytics ID
    re.compile(r"static\.hotjar\.com"),                    # Hotjar
    re.compile(r"plausible\.io/js"),                       # Plausible
    re.compile(r"cdn\.usefathom\.com"),                    # Fathom
    re.compile(r"matomo\.js|piwik\.js|piwik\.php"),        # Matomo/Piwik
    re.compile(r"fbq\s*\(|fbevents\.js"),                  # Meta Pixel
    re.compile(r"_paq\s*=\s*\["),                          # Matomo _paq
)


# ── Checks ────────────────────────────────────────────────────────────────────

@register("not_mobile_responsive")
def check_not_mobile_responsive(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect the absence of a mobile viewport meta tag in the <head>."""
    homepage = pages.get("/")
    if not homepage or not homepage.soup:
        return CheckResult(
            check_name="not_mobile_responsive", passed=True, severity="medium",
            finding="Homepage not available for analysis.", pitch_angle="",
        )

    viewport = homepage.soup.find(
        "meta", attrs={"name": re.compile(r"^viewport$", re.I)}
    )

    if viewport:
        content = viewport.get("content", "")
        return CheckResult(
            check_name="not_mobile_responsive",
            passed=True,
            severity="medium",
            finding=f"Viewport meta tag found: {content!r}",
            pitch_angle="",
        )

    return CheckResult(
        check_name="not_mobile_responsive",
        passed=False,
        severity="medium",
        finding="No <meta name='viewport'> tag found — site likely renders as a desktop layout on phones.",
        pitch_angle=(
            f"The site isn't built for phones — and most people searching for a "
            f"{business.category} near them are on mobile."
        ),
    )


@register("slow_load")
def check_slow_load(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Flag if the homepage load time exceeds the configured threshold."""
    homepage = pages.get("/")
    if not homepage:
        return CheckResult(
            check_name="slow_load", passed=True, severity="medium",
            finding="Homepage not available for analysis.", pitch_angle="",
        )

    threshold = config.audit.page_load_threshold_seconds
    load_time = homepage.load_time_seconds

    if load_time <= threshold:
        return CheckResult(
            check_name="slow_load",
            passed=True,
            severity="medium",
            finding=f"Homepage loaded in {load_time:.2f}s (threshold: {threshold}s).",
            pitch_angle="",
        )

    return CheckResult(
        check_name="slow_load",
        passed=False,
        severity="medium",
        finding=f"Homepage took {load_time:.2f}s to respond (threshold: {threshold}s).",
        pitch_angle=(
            f"The site takes {load_time:.1f} seconds to load — "
            "most mobile users leave before it finishes."
        ),
    )


@register("no_analytics")
def check_no_analytics(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect the absence of any web analytics snippet on the homepage."""
    homepage = pages.get("/")
    if not homepage or not homepage.html:
        return CheckResult(
            check_name="no_analytics", passed=True, severity="medium",
            finding="Homepage not available for analysis.", pitch_angle="",
        )

    for pattern in _ANALYTICS_PATTERNS:
        if pattern.search(homepage.html):
            return CheckResult(
                check_name="no_analytics",
                passed=True,
                severity="medium",
                finding=f"Analytics snippet detected (matched: {pattern.pattern[:40]!r}).",
                pitch_angle="",
            )

    return CheckResult(
        check_name="no_analytics",
        passed=False,
        severity="medium",
        finding=(
            "No Google Analytics, GTM, or other analytics snippet detected on the homepage."
        ),
        pitch_angle=(
            "There's no analytics on the site, so there's no way to know how many "
            "people are visiting, where they're coming from, or what they're doing."
        ),
    )


@register("weak_seo")
def check_weak_seo(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect missing or empty <title>, meta description, and <h1>."""
    homepage = pages.get("/")
    if not homepage or not homepage.soup:
        return CheckResult(
            check_name="weak_seo", passed=True, severity="medium",
            finding="Homepage not available for analysis.", pitch_angle="",
        )

    soup = homepage.soup
    missing: list[str] = []

    title_tag = soup.find("title")
    if not title_tag or not (title_tag.string or "").strip():
        missing.append("<title>")

    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if not meta_desc or not (meta_desc.get("content") or "").strip():
        missing.append("meta description")

    h1_tag = soup.find("h1")
    if not h1_tag or not h1_tag.get_text(strip=True):
        missing.append("<h1>")

    if not missing:
        return CheckResult(
            check_name="weak_seo", passed=True, severity="medium",
            finding="Title, meta description, and <h1> are all present.", pitch_angle="",
        )

    items = ", ".join(missing)
    return CheckResult(
        check_name="weak_seo",
        passed=False,
        severity="medium",
        finding=f"Missing SEO elements: {items}.",
        pitch_angle=(
            f"The site is missing {items} — those are basic signals Google uses "
            "to rank local businesses in search results."
        ),
    )


@register("no_favicon")
def check_no_favicon(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect the absence of a favicon declaration in the <head>."""
    homepage = pages.get("/")
    if not homepage or not homepage.soup:
        return CheckResult(
            check_name="no_favicon", passed=True, severity="medium",
            finding="Homepage not available for analysis.", pitch_angle="",
        )

    favicon = homepage.soup.find(
        "link",
        rel=re.compile(r"(shortcut\s+)?icon|apple-touch-icon", re.I),
    )

    if favicon:
        return CheckResult(
            check_name="no_favicon", passed=True, severity="medium",
            finding=f"Favicon link found: {favicon.get('href', '')!r}",
            pitch_angle="",
        )

    return CheckResult(
        check_name="no_favicon",
        passed=False,
        severity="medium",
        finding="No favicon link tag found in <head>.",
        pitch_angle=(
            "The site has no favicon — the browser tab shows a blank icon, "
            "which is a small but visible sign that the site isn't fully finished."
        ),
    )
