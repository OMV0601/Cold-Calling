"""
Interactive lead-finder session.

Presents a prompt-driven CLI: type a business category and a location,
see a ranked results table, optionally export to a spreadsheet, repeat.

Called by:  main.py  when run with no arguments (default mode).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from rich.columns import Columns
from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from leadscout.audit.engine import run_audit
from leadscout.config import AppConfig
from leadscout.fetcher import WebFetcher
from leadscout.output import LeadRow, build_lead_row, write_output
from leadscout.places import search_interactive
from leadscout.scoring import score_lead, synthesise_pitch

logger = logging.getLogger(__name__)

POPULAR_CATEGORIES: list[str] = [
    "Barbershops",
    "Dental Practices",
    "Auto Repair",
    "Gyms & Fitness",
    "Restaurants",
    "Yoga Studios",
    "Pet Groomers",
    "Landscaping",
    "Plumbers",
    "Accountants",
    "Chiropractors",
    "Real Estate Agents",
]


# ── Entry point ───────────────────────────────────────────────────────────────

def run_interactive(config: AppConfig, api_key: str, console: Console) -> None:
    """
    Run an interactive lead-finder session.

    Loops until the user declines to search again.  Each iteration:
      1. Show popular-category chips
      2. Prompt for business type + location
      3. Search + audit with live progress
      4. Display ranked results table
      5. Offer to export to xlsx/csv
    """
    _print_search_header(console)

    while True:
        # ── Prompts ───────────────────────────────────────────────────────────
        _print_popular_chips(console)

        category = Prompt.ask(
            "\n  [bold white]Business type[/bold white]",
            console=console,
        ).strip()
        if not category:
            continue

        location = Prompt.ask(
            "  [bold white]Location[/bold white] [dim](city, state or country)[/dim]",
            console=console,
        ).strip()
        if not location:
            continue

        # ── Search ────────────────────────────────────────────────────────────
        console.print(
            f"\n  [dim]Searching for[/dim] [cyan]{category!r}[/cyan] "
            f"[dim]in[/dim] [cyan]{location!r}[/cyan][dim]…[/dim]"
        )

        with console.status("[cyan]Calling Google Places API…[/cyan]", spinner="dots"):
            businesses = search_interactive(location, category, config, api_key)

        if not businesses:
            console.print(
                "\n  [yellow]No qualifying leads found.[/yellow]  "
                "[dim]Try a broader category, different city, or lower filters "
                "in config.yaml.[/dim]\n"
            )
            if not Confirm.ask("  Search again?", default=True, console=console):
                break
            console.print()
            _print_search_header(console)
            continue

        track_a = sum(1 for b in businesses if not b.website_uri)
        track_b = sum(1 for b in businesses if b.website_uri)
        console.print(
            f"  [green]Found {len(businesses)} qualifying businesses[/green]  "
            f"[dim](Track A: {track_a}  Track B: {track_b})[/dim]"
        )

        # ── Audit ─────────────────────────────────────────────────────────────
        fetcher = WebFetcher(config)
        rows: list[LeadRow] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("  [progress.description]{task.description}"),
            BarColumn(bar_width=28),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            audit_task = progress.add_task("Auditing…", total=len(businesses))
            for business in businesses:
                progress.update(
                    audit_task,
                    description=f"[yellow]{business.name[:35]}[/yellow]",
                )
                try:
                    report = run_audit(business, fetcher, config)
                    score  = score_lead(business, report, config)
                    pitch  = synthesise_pitch(business, report)
                    rows.append(build_lead_row(business, report, score, pitch))
                except Exception as exc:
                    logger.warning("audit error for %s: %s", business.name, exc)
                progress.advance(audit_task)

        fetcher.close()

        # ── Results table ─────────────────────────────────────────────────────
        console.print()
        _display_results_table(rows, console)

        # ── Export offer ──────────────────────────────────────────────────────
        console.print()
        if Confirm.ask("  Export to spreadsheet?", default=True, console=console):
            export_path = _build_export_path(category, location, config)
            # Temporarily override the output path for this export
            config.output.path = export_path
            write_output(rows, config, console)

        console.print()
        if not Confirm.ask("  Search again?", default=True, console=console):
            break

        console.print()
        _print_search_header(console)

    console.print("\n  [dim]Done. Good luck with the outreach.[/dim]\n")


# ── Display helpers ───────────────────────────────────────────────────────────

def _print_search_header(console: Console) -> None:
    """Print the interactive mode banner."""
    console.print(
        Panel(
            "[bold blue]Lead Finder[/bold blue]  "
            "[dim]— type a business category and location to find leads[/dim]",
            border_style="bright_black",
            padding=(0, 2),
        )
    )


def _print_popular_chips(console: Console) -> None:
    """Print popular categories as a row of styled chips."""
    chips = [
        Text(f" {cat} ", style="bold white on grey23")
        for cat in POPULAR_CATEGORIES
    ]
    console.print(Padding(Columns(chips, equal=False, expand=False), pad=(1, 2)))


def _display_results_table(rows: list[LeadRow], console: Console) -> None:
    """Render a ranked results table sorted by lead score."""
    sorted_rows = sorted(
        rows,
        key=lambda r: (0 if r.lead_track.startswith("A") else 1, -r.lead_score),
    )

    table = Table(
        title=f"[bold]Results — {len(rows)} lead(s) found[/bold]",
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="bright_black",
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("#",       width=3,  justify="right", style="dim")
    table.add_column("Business",width=30, style="bold")
    table.add_column("Tr",      width=2,  justify="center")
    table.add_column("Score",   width=6,  justify="right", style="green")
    table.add_column("Stars",   width=5,  justify="center", style="yellow")
    table.add_column("Reviews", width=7,  justify="right")
    table.add_column("Gaps",    width=4,  justify="right", style="red")
    table.add_column("Website", width=32, style="dim")

    for rank, row in enumerate(sorted_rows, start=1):
        track_str = (
            Text("A", style="bold yellow")
            if row.lead_track.startswith("A")
            else Text("B", style="bold cyan")
        )
        gap_count = len([g for g in row.gaps.split("|") if g.strip()]) if row.gaps else 0
        site = row.website_url[:31] if row.website_url else "[dim]none[/dim]"

        table.add_row(
            str(rank),
            row.name[:29],
            track_str,
            f"{row.lead_score:.1f}",
            row.rating,
            row.review_count,
            str(gap_count) if gap_count else "—",
            site,
        )

    console.print(Padding(table, pad=(0, 2)))


def _build_export_path(category: str, location: str, config: AppConfig) -> Path:
    """Build a timestamped export path based on the search query."""
    slug = re.sub(r"[^a-z0-9]+", "_", f"{category}_{location}".lower()).strip("_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"leads_{slug}_{timestamp}{Path(config.output.path).suffix}"
    return config.output.path.parent / filename
