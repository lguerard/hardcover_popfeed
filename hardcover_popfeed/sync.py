"""Orchestrates the Hardcover → Popfeed sync."""

import logging

from hardcover_popfeed.atproto import AtProtoClient
from hardcover_popfeed.config import Config
from hardcover_popfeed.hardcover import HardcoverClient
from hardcover_popfeed.popfeed import PopfeedClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_sync(config: Config) -> None:
    """Run the full Hardcover → Popfeed synchronisation.

    Fetches all books from the authenticated Hardcover user's library,
    finds or creates the user's Books list on Popfeed, then creates or
    updates a listItem record on Popfeed for each book.

    Parameters:
        config (Config): Application configuration.
    """
    if config.dry_run:
        logger.info("Dry-run mode enabled — no writes will be made.")

    with HardcoverClient(config.hardcover_token) as hc:
        books = hc.get_user_books()

    if not books:
        logger.info("No books found in Hardcover library. Exiting.")
        return

    with AtProtoClient(config.popfeed_pds_url) as atproto:
        atproto.create_session(
            identifier=config.popfeed_identifier,
            password=config.popfeed_password,
        )

        popfeed = PopfeedClient(atproto, dry_run=config.dry_run)

        if config.popfeed_books_list_uri:
            logger.warning(
                "POPFEED_BOOKS_LIST_URI is set but no longer used; "
                "books are now synced into per-status lists "
                "(Want to Read, Currently Reading, Read Books). "
                "You can remove that variable."
            )

        list_uris = popfeed.ensure_status_lists()

        synced = 0
        for book in books:
            try:
                popfeed.sync_book(book, list_uris)
                synced += 1
            except Exception as exc:
                logger.warning("Failed to sync %r: %s", book.title, exc)

    logger.info("Sync complete: %d/%d books processed.", synced, len(books))
