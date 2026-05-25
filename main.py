"""
LeadScout — entry point.

Two modes:

  Interactive (default when run with no arguments):
    python main.py
    → Prompts for business type + location, shows results live.

  Batch (runs every area × category pair from config.yaml):
    python main.py --batch
    → Searches all configured areas and categories, writes full xlsx output.

Other options:
    --config PATH   Path to config file (default: config.yaml)
    --dry-run       Validate config and print what would run; make no API calls
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from leadscout.config import AppConfig, load_api_key, load_config

console = Console()

BANNER = """
[bold blue]LeadScout[/bold blue] [dim]— local business lead finder[/dim]
"""


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="leadscout",
        description="Find local businesses with fixable web-presence gaps.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py                   # interactive mode\n"
            "  python main.py --batch           # run full config.yaml batch\n"
            "  python main.py --dry-run         # validate config only\n"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Batch mode: search all areas × categories from config.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and show what would run without calling any APIs",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point — routes to interactive or batch mode."""
    args = parse_args()

    console.print(Panel(BANNER, border_style="blue", padding=(0, 2)))

    try:
        config: AppConfig = load_config(args.config)
        api_key: str = load_api_key()
    except Exception as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        sys.exit(1)

    if args.dry_run:
        console.print("[bold yellow]Dry run — no API calls will be made.[/bold yellow]")
        _print_config_summary(config)
        return

    if args.batch:
        from leadscout.pipeline import run_pipeline
        run_pipeline(config=config, api_key=api_key, console=console)
    else:
        # Default: interactive mode
        from leadscout.interactive import run_interactive
        run_interactive(config=config, api_key=api_key, console=console)


def _print_config_summary(config: AppConfig) -> None:
    """Print a human-readable summary of the loaded config."""
    console.print(f"  [bold]Search areas:[/bold] {', '.join(config.search_areas)}")
    console.print(f"  [bold]Categories:[/bold]   {', '.join(config.categories)}")
    console.print(f"  [bold]Min rating:[/bold]   {config.filters.min_rating}")
    console.print(f"  [bold]Min reviews:[/bold]  {config.filters.min_review_count}")
    console.print(f"  [bold]Checks:[/bold]       {len(config.audit.enabled_checks)} enabled")
    console.print(f"  [bold]Output:[/bold]       {config.output.path}")


if __name__ == "__main__":
    main()
