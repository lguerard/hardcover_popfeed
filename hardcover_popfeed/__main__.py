"""CLI entry point for hardcover-popfeed."""

import argparse
import sys

from hardcover_popfeed.config import Config, ConfigError
from hardcover_popfeed.sync import run_sync


def main() -> None:
    """Parse arguments and run the sync."""
    parser = argparse.ArgumentParser(
        prog="hardcover-popfeed",
        description=(
            "Sync reading progress from Hardcover to Popfeed via AT Protocol."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Log actions without writing anything to Popfeed.",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=".env",
        help="Path to .env file (default: .env).",
    )
    args = parser.parse_args()

    try:
        config = Config.from_env(
            env_file=args.env_file,
            dry_run=args.dry_run,
        )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    run_sync(config)


if __name__ == "__main__":
    main()
