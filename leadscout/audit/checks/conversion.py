"""
Conversion checks (severity: high).

These detect missing paths for turning a site visitor into a paying customer.
All five are high-severity because a missing conversion path directly costs
the business money — it is the strongest pitch angle.
"""

from __future__ import annotations

import re

from leadscout.audit.engine import register
from leadscout.audit.models import CheckResult
from leadscout.config import AppConfig
from leadscout.fetcher import FetchResult
from leadscout.places import Business

# ── Constants ─────────────────────────────────────────────────────────────────

_CONTACT_FIELD_NAMES = re.compile(
    r"(email|e-mail|message|msg|inquiry|enquiry|name|subject|phone|tel)",
    re.I,
)

_BOOKING_PROVIDERS = (
    "calendly.com", "booksy.com", "acuityscheduling.com",
    "squareup.com", "square.site", "squareappointments.com",
    "opentable.com", "toasttab.com", "resy.com", "fresha.com",
    "vagaro.com", "mindbodyonline.com", "setmore.com",
    "simplybook.me", "hubspot.com/meetings", "zcal.co",
    "appointy.com", "oncehub.com", "tidycal.com",
)

_BOOKING_TEXT_RE = re.compile(
    r"\b(book(\s+now|\s+online|\s+an?\s+appointment)?|schedule(\s+now|\s+online)?|"
    r"make\s+an?\s+appointment|request\s+a\s+quote|get\s+a\s+quote|"
    r"order\s+(online|now)|online\s+ordering|reserve(\s+a\s+table)?|"
    r"start\s+order|place\s+order)\b",
    re.I,
)

_MAP_DOMAINS = (
    "maps.google", "google.com/maps", "goo.gl/maps",
    "maps.app.goo.gl", "waze.com", "bing.com/maps",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _all_soups(pages: dict[str, FetchResult]):
    """Yield BeautifulSoup objects for all successfully fetched pages."""
    for result in pages.values():
        if result.ok and result.soup is not None:
            yield result.soup


def _homepage_soup(pages: dict[str, FetchResult]):
    """Return the homepage BeautifulSoup or None."""
    hp = pages.get("/")
    return hp.soup if (hp and hp.ok) else None


# ── Checks ────────────────────────────────────────────────────────────────────

@register("no_contact_form")
def check_no_contact_form(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect the absence of a contact form on the homepage or /contact page."""
    target_paths = ["/", "/contact", "/contact-us"]
    for path in target_paths:
        result = pages.get(path)
        if not result or not result.soup:
            continue
        for form in result.soup.find_all("form"):
            has_email = bool(form.find("input", {"type": "email"}))
            has_textarea = bool(form.find("textarea"))
            has_named_field = bool(
                form.find("input", {"name": _CONTACT_FIELD_NAMES})
                or form.find("input", {"placeholder": _CONTACT_FIELD_NAMES})
            )
            if has_email or has_textarea or has_named_field:
                return CheckResult(
                    check_name="no_contact_form",
                    passed=True,
                    severity="high",
                    finding="Contact form detected.",
                    pitch_angle="",
                )

    return CheckResult(
        check_name="no_contact_form",
        passed=False,
        severity="high",
        finding="No contact form found on the homepage or /contact page.",
        pitch_angle=(
            "Right now the only way a new customer can reach you online is to call — "
            "many people will just move on instead."
        ),
    )


@register("no_click_to_call")
def check_no_click_to_call(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect the absence of a tel: hyperlink anywhere on the fetched pages."""
    for soup in _all_soups(pages):
        if soup.find("a", href=re.compile(r"^tel:", re.I)):
            return CheckResult(
                check_name="no_click_to_call",
                passed=True,
                severity="high",
                finding="Click-to-call (tel:) link found.",
                pitch_angle="",
            )

    return CheckResult(
        check_name="no_click_to_call",
        passed=False,
        severity="high",
        finding="No tap-to-call (tel:) link found on any page.",
        pitch_angle=(
            f"There's no tap-to-call button — mobile visitors searching for a "
            f"{business.category} can't call you in one tap."
        ),
    )


@register("no_visible_contact_info")
def check_no_visible_contact_info(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """
    Detect the absence of all contact methods: phone text, email, contact page.
    Fails only if every signal is absent.
    """
    _phone_re = re.compile(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
    _email_re = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

    for result in pages.values():
        if not result.ok or not result.soup:
            continue
        # tel: link
        if result.soup.find("a", href=re.compile(r"^tel:", re.I)):
            return _contact_found("no_visible_contact_info")
        text = result.soup.get_text(" ", strip=True)
        if _phone_re.search(text) or _email_re.search(text):
            return _contact_found("no_visible_contact_info")

    # Reachable contact page counts as contact info
    for path in ("/contact", "/contact-us"):
        r = pages.get(path)
        if r and r.ok:
            return _contact_found("no_visible_contact_info")

    return CheckResult(
        check_name="no_visible_contact_info",
        passed=False,
        severity="high",
        finding="No phone number, email address, or contact page found on the site.",
        pitch_angle=(
            "A new customer visiting your site has no obvious way to reach you — "
            "no phone, no email, no contact page."
        ),
    )


def _contact_found(check_name: str) -> CheckResult:
    return CheckResult(
        check_name=check_name,
        passed=True,
        severity="high",
        finding="Contact information found.",
        pitch_angle="",
    )


@register("no_booking_or_ordering")
def check_no_booking_or_ordering(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect the absence of any online booking, scheduling, or ordering capability."""
    for soup in _all_soups(pages):
        # Embedded booking widget (iframe)
        for iframe in soup.find_all("iframe", src=True):
            if any(p in iframe["src"].lower() for p in _BOOKING_PROVIDERS):
                return _booking_found()

        # Links to booking providers or with booking text
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            text = a.get_text(strip=True)
            if any(p in href for p in _BOOKING_PROVIDERS):
                return _booking_found()
            if _BOOKING_TEXT_RE.search(text):
                return _booking_found()

        # Button / submit text
        for btn in soup.find_all(["button"]):
            if _BOOKING_TEXT_RE.search(btn.get_text(strip=True)):
                return _booking_found()

        # Input[type=submit] value
        for inp in soup.find_all("input", {"type": ["submit", "button"]}):
            val = inp.get("value", "")
            if _BOOKING_TEXT_RE.search(val):
                return _booking_found()

    return CheckResult(
        check_name="no_booking_or_ordering",
        passed=False,
        severity="high",
        finding=(
            "No booking, scheduling, or online ordering capability detected "
            "(no known widget embed or booking-related call-to-action)."
        ),
        pitch_angle=(
            "There's no way to book, schedule, or order from the site — "
            "every potential customer has to pick up the phone first."
        ),
    )


def _booking_found() -> CheckResult:
    return CheckResult(
        check_name="no_booking_or_ordering",
        passed=True,
        severity="high",
        finding="Booking or ordering capability detected.",
        pitch_angle="",
    )


@register("no_map_or_directions")
def check_no_map_or_directions(
    pages: dict[str, FetchResult],
    business: Business,
    config: AppConfig,
) -> CheckResult:
    """Detect the absence of an embedded map or a link to driving directions."""
    for soup in _all_soups(pages):
        for iframe in soup.find_all("iframe", src=True):
            if any(d in iframe["src"].lower() for d in _MAP_DOMAINS):
                return _map_found()
        for a in soup.find_all("a", href=True):
            if any(d in a["href"].lower() for d in _MAP_DOMAINS):
                return _map_found()

    return CheckResult(
        check_name="no_map_or_directions",
        passed=False,
        severity="high",
        finding="No embedded map or directions link found on any page.",
        pitch_angle=(
            "There's no map or directions link on the site — customers who want "
            "to visit in person have to look you up separately."
        ),
    )


def _map_found() -> CheckResult:
    return CheckResult(
        check_name="no_map_or_directions",
        passed=True,
        severity="high",
        finding="Map or directions link detected.",
        pitch_angle="",
    )
