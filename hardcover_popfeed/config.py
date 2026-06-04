"""Configuration loading from environment variables."""

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _ensure_https(url: str) -> str:
    """Ensure a URL has an https:// scheme.

    If the URL has no scheme (or only ``http://``), it is
    normalised to ``https://``.

    Parameters:
        url (str): Raw URL string, possibly without a scheme.

    Returns:
        str: URL with ``https://`` scheme.
    """
    if url.startswith("https://"):
        return url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return "https://" + url


@dataclass
class Config:
    """Application configuration loaded from environment variables.

    Parameters:
        hardcover_token (str): Hardcover API token.
        popfeed_identifier (str): Popfeed/Bluesky handle or DID.
        popfeed_password (str): Popfeed/Bluesky app password.
        popfeed_pds_url (str): AT Protocol PDS base URL.
        popfeed_books_list_uri (Optional[str]): Pre-configured list URI.
        dry_run (bool): If True, log actions without writing to Popfeed.
    """

    hardcover_token: str
    popfeed_identifier: str
    popfeed_password: str
    popfeed_pds_url: str = "https://eurosky.social"
    popfeed_books_list_uri: Optional[str] = field(default=None)
    dry_run: bool = False

    @classmethod
    def from_env(
        cls,
        env_file: str = ".env",
        dry_run: bool = False,
    ) -> "Config":
        """Load configuration from environment, optionally from a file.

        Parameters:
            env_file (str): Path to a .env file to load.
            dry_run (bool): Override for the dry-run flag.

        Returns:
            Config: Populated configuration instance.

        Raises:
            ConfigError: If a required variable is missing.
        """
        load_dotenv(dotenv_path=env_file)

        missing: list[str] = []

        hardcover_token = os.environ.get("HARDCOVER_TOKEN", "").strip()
        if not hardcover_token:
            missing.append("HARDCOVER_TOKEN")

        popfeed_identifier = os.environ.get("POPFEED_IDENTIFIER", "").strip()
        if not popfeed_identifier:
            missing.append("POPFEED_IDENTIFIER")

        popfeed_password = os.environ.get("POPFEED_PASSWORD", "").strip()
        if not popfeed_password:
            missing.append("POPFEED_PASSWORD")

        if missing:
            raise ConfigError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        env_dry_run = os.environ.get("DRY_RUN", "").strip().lower()
        resolved_dry_run = dry_run or env_dry_run in ("1", "true", "yes")

        raw_pds_url = (
            os.environ.get("POPFEED_PDS_URL") or "https://eurosky.social"
        ).strip()
        pds_url = _ensure_https(raw_pds_url)

        return cls(
            hardcover_token=hardcover_token,
            popfeed_identifier=popfeed_identifier,
            popfeed_password=popfeed_password,
            popfeed_pds_url=pds_url,
            popfeed_books_list_uri=(
                os.environ.get("POPFEED_BOOKS_LIST_URI", "").strip() or None
            ),
            dry_run=resolved_dry_run,
        )
