# LeadScout

Find small businesses with fixable web-presence gaps and generate ready-to-send cold-outreach leads.

LeadScout searches Google Places for local businesses, audits their websites for concrete missing features, scores each lead, and writes a ranked spreadsheet with a personalised pitch opener for every row.

---

## Quick start (5 steps)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Add your Google API key
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux
# Open .env and set: GOOGLE_PLACES_API_KEY=your_key_here

# 4. Edit config.yaml (search areas, categories, filters)

# 5. Run — interactive mode (default)
python main.py
```

---

## Two modes

### Interactive mode (default)
```bash
python main.py
```
Type a business category and city, see a ranked table of leads, and export to a spreadsheet — all without editing any files.  Results from each search are saved to a timestamped file so runs don't overwrite each other.

### Batch mode
```bash
python main.py --batch
```
Searches every `search_area × category` combination in `config.yaml` and writes one combined spreadsheet.  Best for a systematic sweep of multiple cities.

### Other options
```bash
python main.py --dry-run              # validate config, no API calls
python main.py --batch --config my_config.yaml   # custom config file
```

---

## Google Cloud setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a project
2. **APIs & Services → Library** → search **"Places API (New)"** → Enable
3. **APIs & Services → Credentials → + Create Credentials → API Key**
4. Edit the key → restrict to **Places API (New)**
5. **Billing → Budgets & Alerts** → set a $10/month cap as a safety net
6. Paste the key into your `.env` file:
   ```
   GOOGLE_PLACES_API_KEY=AIza...
   ```

### Cost estimate

| Action | Cost per 1,000 |
|---|---|
| `searchText` (all fields) | ~$27 |
| Re-run within cache TTL | **$0** |

A typical session (3 cities × 5 categories = 15 searches, ~300 businesses):
- **First run:** ~$5–8
- **Re-run within 30 days:** $0 (SQLite cache)
- **Monthly free credit:** $200 → covers ~25–40 fresh full runs/month

---

## Output spreadsheet

Columns (sorted by lead score, highest first):

| Column | Description |
|---|---|
| Lead Score | Weighted score — sort by this |
| Track | A = no website (pitch building one), B = has website (pitch improvements) |
| Business Name | |
| Category | The search category used |
| City / Area | Which search area this came from |
| Rating | Google star rating |
| Reviews | Google review count |
| Photos (listing) | Number of photos on the Google listing |
| Phone | |
| Contact Email | Scraped from the site (if found) |
| Website | |
| Contact Page | URL of the /contact page (if found) |
| Address | |
| Gaps | Pipe-separated list of failed checks with severity |
| Pitch Opener | Ready-to-paste cold email opener |

---

## Config reference (`config.yaml`)

```yaml
search_areas:          # Searched independently; any city/region string
  - "Seattle, WA"

categories:            # Any business type Google Places understands
  - "barbershop"
  - "dentist"

filters:
  min_rating: 3.5      # Skip businesses rated below this
  min_review_count: 10 # Skip businesses with fewer reviews
  exclude_chains: true # Skip national franchises (see data/chain_fragments.txt)

audit:
  enabled_checks:      # Comment out a line to disable that check
    - no_contact_form  # HIGH — no <form> on homepage or /contact
    - no_click_to_call # HIGH — no tel: link
    - no_https         # MED  — site served over HTTP
    - not_mobile_responsive  # MED — no viewport meta tag
    # ... (17 checks total)
  page_load_threshold_seconds: 4
  stale_copyright_years: 2   # Footer year older than (now - N) = stale
  min_listing_photos: 5      # Fewer photos = presence gap
  min_review_count_presence: 25

scoring:
  weight_rating: 1.0       # Higher-rated business = better lead
  weight_review_count: 1.0 # More reviews = more established
  weight_gap_count: 2.0    # More fixable gaps = more value to offer

output:
  format: "xlsx"           # "xlsx" or "csv"
  path: "./output/leads.xlsx"

cache:
  db_path: "./cache/leadscout.db"
  ttl_days: 30             # Re-fetch a place/page only after this many days
```

### Tuning chain detection
Edit `data/chain_fragments.txt` — one franchise name fragment per line.
If a business name contains any fragment (case-insensitive), it is flagged as a
chain and excluded when `exclude_chains: true`.

---

## Audit checks

### Conversion — severity: HIGH
| Check | What it detects |
|---|---|
| `no_contact_form` | No `<form>` with contact-like fields on homepage or /contact |
| `no_click_to_call` | No `tel:` link (critical for mobile) |
| `no_visible_contact_info` | No phone, no email, and no reachable contact page |
| `no_booking_or_ordering` | No booking widget (Calendly, Booksy, Square, etc.) |
| `no_map_or_directions` | No embedded map or Google Maps link |

### Trust — severity: MEDIUM
| Check | What it detects |
|---|---|
| `no_https` | Site served over HTTP — browsers show "Not Secure" |
| `no_testimonials` | No reviews section, blockquotes, or schema.org Review markup |
| `no_real_photos` | Fewer than 2 real content images on homepage |
| `stale_copyright` | Footer copyright year older than threshold |
| `broken_links_or_images` | Internal links or images returning 4xx |

### Technical / SEO — severity: MEDIUM
| Check | What it detects |
|---|---|
| `not_mobile_responsive` | Missing `<meta name="viewport">` tag |
| `slow_load` | Homepage response time over threshold |
| `no_analytics` | No GA4, GTM, Hotjar, or other analytics snippet |
| `weak_seo` | Missing `<title>`, meta description, or `<h1>` |
| `no_favicon` | No favicon link tag |

### Presence — from Google Places data, no fetch needed (both tracks)
| Check | What it detects |
|---|---|
| `few_listing_photos` | Listing has fewer photos than `min_listing_photos` |
| `low_review_count` | Review count below `min_review_count_presence` |

---

## Adding a new check

1. Write a function in the appropriate `leadscout/audit/checks/*.py` file:
   ```python
   @register("my_new_check")
   def check_my_new_check(
       pages: dict[str, FetchResult],
       business: Business,
       config: AppConfig,
   ) -> CheckResult:
       ...
   ```
2. Add `"my_new_check"` to `enabled_checks` in `config.yaml`.

That's it — no changes to the engine needed.

---

## Project structure

```
main.py                        CLI entry point
config.yaml                    All tunable settings
data/chain_fragments.txt       Franchise name fragments (editable)
leadscout/
  config.py                    Config + .env loader
  places.py                    Google Places API client + SQLite cache
  fetcher.py                   Website fetcher + HTML cache
  pipeline.py                  Batch mode orchestrator
  interactive.py               Interactive mode
  scoring.py                   Lead score + pitch synthesis
  output.py                    Spreadsheet + console summary
  audit/
    engine.py                  Check registry + run_audit()
    models.py                  CheckResult + AuditReport dataclasses
    checks/
      conversion.py            5 conversion checks (HIGH)
      trust.py                 5 trust checks (MED)
      technical.py             5 technical/SEO checks (MED)
      presence.py              2 presence checks (MED)
cache/                         SQLite DB (gitignored)
output/                        Generated spreadsheets (gitignored)
```

---

## Troubleshooting

**"GOOGLE_PLACES_API_KEY is not set"**
→ Make sure you created a `.env` file (not `.env.txt`) with the key on one line, no spaces around `=`.

**"No qualifying leads found"**
→ Lower `min_rating` or `min_review_count` in `config.yaml`, or try a broader search area.

**Site audit returning all passes**
→ The site may have blocked the fetcher.  Check `fetch_errors` — some sites block scrapers.  The Places-data presence checks still run regardless.

**Re-runs are slow**
→ Check `cache/leadscout.db` exists.  If you deleted it, the first run after will re-fetch everything.
