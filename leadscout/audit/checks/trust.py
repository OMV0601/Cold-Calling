"""
Trust checks (severity: medium).

These detect missing or broken credibility signals that make visitors
second-guess whether a business is professional, active, and trustworthy.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests

from leadscout.audit.engine import register
from leadscout.audit.models import CheckResult
from leadscout.config import AppConfig
from leadscout.fetcher import FetchResult
from leadscout.places import Business

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_TESTIMONIAL_CLASS_RE = re.compile(
    r"testimonial|review|feedback|rating|star|client.?say|customer.?say|what.?people", re.I
)
_TESTIMONIAL_HEADING_RE = re.compile(
    r"\b(testimonials?|reviews?|what (our |my )?(clients?|customers?|patients?|people) say|"
    r"success stories|happy customers|don'?t take our word)\b",
    re.I,
)
_COPYRIGHT_RE = re.compile(
    r"(?:©|&copy;|copyright|\(c\))\s*(?:\d{4}\s*[-–—]\s*)?(\d{4})",
    re.I,
)


# ── Checks ────────────────────────────────────────────────────────────────────

@register("no_https")
def check_no_https(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect that the site is served over plain HTTP after following redirects."""
    homepage = pages.get("/")
    if not homepage or not homepage.final_url:
        return CheckResult(
            check_name="no_https", passed=True, severity="medium",
            finding="Could not determine protocol (page not fetched).", pitch_angle="",
        )

    is_https = homepage.final_url.startswith("https://")
    if is_https:
        return CheckResult(
            check_name="no_https", passed=True, severity="medium",
            finding="Site uses HTTPS.", pitch_angle="",
        )

    return CheckResult(
        check_name="no_https",
        passed=False,
        severity="medium",
        finding=f"Site final URL is HTTP: {homepage.final_url}",
        pitch_angle=(
            "The site shows as 'Not Secure' in every browser — that warning alone "
            "makes potential customers click away before they read a word."
        ),
    )


@register("no_testimonials")
def check_no_testimonials(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect the absence of a testimonials or reviews section on the site."""
    homepage = pages.get("/")
    if not homepage or not homepage.soup:
        return CheckResult(
            check_name="no_testimonials", passed=True, severity="medium",
            finding="Homepage not available for analysis.", pitch_angle="",
        )

    soup = homepage.soup

    # Class or ID matching
    if soup.find(class_=_TESTIMONIAL_CLASS_RE) or soup.find(id=_TESTIMONIAL_CLASS_RE):
        return _testimonials_found()

    # Heading text matching
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
        if _TESTIMONIAL_HEADING_RE.search(tag.get_text(strip=True)):
            return _testimonials_found()

    # Blockquote (common testimonial markup)
    if soup.find("blockquote"):
        return _testimonials_found()

    # Schema.org Review markup
    if soup.find(attrs={"itemtype": re.compile(r"schema.org/Review", re.I)}):
        return _testimonials_found()

    return CheckResult(
        check_name="no_testimonials",
        passed=False,
        severity="medium",
        finding="No testimonials, reviews section, or blockquotes detected on the homepage.",
        pitch_angle=(
            "There are no customer reviews or testimonials on the site — social proof "
            "is one of the biggest factors when someone is choosing a local business."
        ),
    )


def _testimonials_found() -> CheckResult:
    return CheckResult(
        check_name="no_testimonials", passed=True, severity="medium",
        finding="Testimonials or reviews section detected.", pitch_angle="",
    )


@register("no_real_photos")
def check_no_real_photos(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """
    Heuristically count real content images on the homepage.
    Flags for manual review when fewer than 2 real images are found.
    """
    homepage = pages.get("/")
    if not homepage or not homepage.soup:
        return CheckResult(
            check_name="no_real_photos", passed=True, severity="medium",
            finding="Homepage not available for analysis.", pitch_angle="",
        )

    skip_patterns = re.compile(
        r"(pixel|track|beacon|analytics|icon|logo|favicon|sprite|arrow|"
        r"placeholder|blank|spacer|\.svg)",
        re.I,
    )

    real_count = 0
    for img in homepage.soup.find_all("img"):
        src = img.get("src", "") or img.get("data-src", "")
        if not src or src.startswith("data:"):
            continue
        if skip_patterns.search(src):
            continue
        # Skip tiny images (tracking pixels / decorative icons)
        try:
            w = int(img.get("width", 0) or 0)
            h = int(img.get("height", 0) or 0)
            if (w and w < 80) or (h and h < 80):
                continue
        except (ValueError, TypeError):
            pass
        real_count += 1

    passed = real_count >= 2
    return CheckResult(
        check_name="no_real_photos",
        passed=passed,
        severity="medium",
        finding=(
            f"Homepage has approximately {real_count} real content image(s) — "
            "flagged for manual review." if not passed
            else f"Homepage has approximately {real_count} content images."
        ),
        pitch_angle=(
            "The site has almost no photos of your work or space — "
            "customers want to see what they're getting before they commit."
            if not passed else ""
        ),
    )


@register("stale_copyright")
def check_stale_copyright(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect a stale copyright year in the page footer."""
    homepage = pages.get("/")
    if not homepage or not homepage.soup:
        return CheckResult(
            check_name="stale_copyright", passed=True, severity="medium",
            finding="Homepage not available for analysis.", pitch_angle="",
        )

    current_year = datetime.now().year
    threshold = current_year - config.audit.stale_copyright_years

    # Prefer footer; fall back to full page text
    footer = (
        homepage.soup.find("footer")
        or homepage.soup.find(id=re.compile(r"footer", re.I))
        or homepage.soup.find(class_=re.compile(r"footer", re.I))
    )
    text = footer.get_text(" ", strip=True) if footer else homepage.soup.get_text(" ", strip=True)

    years_found = [int(y) for y in _COPYRIGHT_RE.findall(text) if 1990 <= int(y) <= current_year + 1]
    if not years_found:
        return CheckResult(
            check_name="stale_copyright", passed=True, severity="medium",
            finding="No copyright year found in footer.", pitch_angle="",
        )

    latest = max(years_found)
    if latest >= threshold:
        return CheckResult(
            check_name="stale_copyright", passed=True, severity="medium",
            finding=f"Copyright year is {latest} — within the acceptable range.", pitch_angle="",
        )

    return CheckResult(
        check_name="stale_copyright",
        passed=False,
        severity="medium",
        finding=f"Footer shows copyright year {latest} (threshold: {threshold}).",
        pitch_angle=(
            f"The site's footer still shows {latest} — visitors notice that kind of "
            "detail and it signals the site hasn't been touched in years."
        ),
    )


@register("broken_links_or_images")
def check_broken_links_or_images(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """
    Check for broken links by examining pages already fetched, then sampling
    a few additional internal links and image srcs from the homepage via HEAD.
    """
    broken: list[str] = []

    homepage = pages.get("/")
    if not homepage or not homepage.soup or not homepage.final_url:
        return _broken_result(broken)

    base = f"{urlparse(homepage.final_url).scheme}://{urlparse(homepage.final_url).netloc}"
    already_checked = {r.final_url for r in pages.values() if r.final_url}
    session = requests.Session()
    session.headers["User-Agent"] = _USER_AGENT

    # 2. Sample up to 8 internal links not already checked
    internal_urls: list[str] = []
    for a in homepage.soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        full = urljoin(base + "/", href)
        if full.startswith(base) and full not in already_checked:
            internal_urls.append(full)
            already_checked.add(full)
        if len(internal_urls) >= 8:
            break

    for url in internal_urls:
        try:
            r = session.head(url, timeout=5, allow_redirects=True)
            if r.status_code >= 400:
                broken.append(f"{url} ({r.status_code})")
        except requests.RequestException:
            pass  # network errors on sample links are not definitive broken-link signals

    # 3. Sample up to 5 img src values
    img_count = 0
    for img in homepage.soup.find_all("img", src=True):
        src = img["src"]
        if src.startswith("data:") or not src.startswith(("http", "/")):
            continue
        full = urljoin(base + "/", src)
        try:
            r = session.head(full, timeout=5, allow_redirects=True)
            if r.status_code >= 400:
                broken.append(f"image {full} ({r.status_code})")
        except requests.RequestException:
            pass
        img_count += 1
        if img_count >= 5:
            break

    return _broken_result(broken)


def _broken_result(broken: list[str]) -> CheckResult:
    if not broken:
        return CheckResult(
            check_name="broken_links_or_images", passed=True, severity="medium",
            finding="No broken links or images detected in the sample.", pitch_angle="",
        )
    sample = broken[:3]
    return CheckResult(
        check_name="broken_links_or_images",
        passed=False,
        severity="medium",
        finding=f"{len(broken)} broken link(s)/image(s) found: {'; '.join(sample)}" + (
            f" (and {len(broken) - 3} more)" if len(broken) > 3 else ""
        ),
        pitch_angle=(
            "Some links or images on the site are broken — "
            "that makes the business look like it's no longer active."
        ),
    )
