"""
Batch pipeline orchestrator.

Runs the full lead-generation flow for every (area × category) pair in
config.yaml, with rich progress bars at each phase.

Called by:  main.py  when --batch is passed.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from leadscout.audit.engine import run_audit
from leadscout.config import AppConfig
from leadscout.fetcher import WebFetcher
from leadscout.output import build_lead_row, print_summary, write_output
from leadscout.places import search_all
from leadscout.scoring import score_lead, synthesise_pitch

logger = logging.getLogger(__name__)


def run_pipeline(config: AppConfig, api_key: str, console: Console) -> None:
    """
    Execute the full batch pipeline:
      1. Search all configured (area × category) pairs via Places API
      2. Audit each qualifying business website
      3. Score + synthesise pitches
      4. Write spreadsheet output + print summary
    """
    total_pairs = len(config.search_areas) * len(config.categories)

    # ── Phase 1: Places search ────────────────────────────────────────────────
    console.print(
        f"\n[bold cyan]Phase 1 — Searching[/bold cyan]  "
        f"[dim]{total_pairs} area × category pair(s)[/dim]"
    )

    with console.status("[cyan]Calling Google Places API (cache checked first)…[/cyan]"):
        businesses = search_all(config, api_key)

    if not businesses:
        console.print("[yellow]No businesses matched your filters. "
                      "Try lowering min_rating or min_review_count in config.yaml.[/yellow]")
        return

    track_a = sum(1 for b in businesses if not b.website_uri)
    track_b = sum(1 for b in businesses if b.website_uri)
    console.print(
        f"[green]Found {len(businesses)} qualifying leads[/green]  "
        f"[dim](Track A: {track_a}  Track B: {track_b})[/dim]"
    )

    # ── Phase 2: Audit + score ────────────────────────────────────────────────
    console.print(
        f"\n[bold cyan]Phase 2 — Auditing[/bold cyan]  "
        f"[dim]{track_b} website(s) to fetch[/dim]"
    )

    fetcher = WebFetcher(config)
    rows = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Starting…", total=len(businesses))

        for business in businesses:
            progress.update(
                task,
                description=f"[yellow]{business.name[:38]}[/yellow]",
            )
            try:
                report = run_audit(business, fetcher, config)
                score  = score_lead(business, report, config)
                pitch  = synthesise_pitch(business, report)
                rows.append(build_lead_row(business, report, score, pitch))
            except Exception as exc:
                logger.warning("pipeline error for %s: %s", business.name, exc)
            progress.advance(task)

    fetcher.close()

    # ── Phase 3: Output ───────────────────────────────────────────────────────
    console.print()
    print_summary(rows, scanned_count=len(businesses), console=console)
    console.print()
    write_output(rows, config, console)
