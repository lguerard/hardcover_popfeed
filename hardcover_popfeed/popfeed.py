"""Popfeed-specific operations built on top of the AT Protocol client."""

import logging
from datetime import datetime, timezone
from typing import Optional

from hardcover_popfeed.atproto import AtProtoClient
from hardcover_popfeed.models import HardcoverBook, PopfeedIdentifiers

logger = logging.getLogger(__name__)

_COLLECTION_LIST = "social.popfeed.feed.list"
_COLLECTION_LIST_ITEM = "social.popfeed.feed.listItem"

# Hardcover status_id → Popfeed listItem status
_STATUS_MAP: dict[int, str] = {
    1: "social.popfeed.feed.listItem#backlog",
    2: "social.popfeed.feed.listItem#in_progress",
    3: "social.popfeed.feed.listItem#finished",
    4: "social.popfeed.feed.listItem#in_progress",  # Paused → in_progress
    5: "social.popfeed.feed.listItem#abandoned",
}

_BOOKS_LIST_NAME = "Books"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        str: Current timestamp in ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` form.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_identifiers(book: HardcoverBook) -> PopfeedIdentifiers:
    """Build Popfeed identifiers from a Hardcover book.

    Parameters:
        book (HardcoverBook): Source book record.

    Returns:
        PopfeedIdentifiers: Populated identifier object.
    """
    return PopfeedIdentifiers(
        isbn10=book.isbn_10 or None,
        isbn13=book.isbn_13 or None,
        other=f"hardcover:{book.book_id}" if not (
            book.isbn_10 or book.isbn_13
        ) else None,
    )


def _identifiers_match(
    existing_ids: dict, desired_ids: PopfeedIdentifiers
) -> bool:
    """Check if existing Popfeed identifiers match desired ones.

    Matching priority: isbn13 > isbn10 > other.

    Parameters:
        existing_ids (dict): Identifiers stored in the Popfeed record.
        desired_ids (PopfeedIdentifiers): Desired identifiers.

    Returns:
        bool: True if at least one identifier matches.
    """
    if desired_ids.isbn13 and existing_ids.get("isbn13") == desired_ids.isbn13:
        return True
    if desired_ids.isbn10 and existing_ids.get("isbn10") == desired_ids.isbn10:
        return True
    if desired_ids.other and existing_ids.get("other") == desired_ids.other:
        return True
    return False


def _build_list_item_record(
    book: HardcoverBook,
    list_uri: str,
    identifiers: PopfeedIdentifiers,
    now: str,
) -> dict:
    """Build the Popfeed listItem record value for a book.

    Parameters:
        book (HardcoverBook): Source Hardcover book.
        list_uri (str): AT URI of the parent list.
        identifiers (PopfeedIdentifiers): Book identifiers.
        now (str): ISO-8601 timestamp for ``addedAt``/``updatedAt``.

    Returns:
        dict: Record value ready for createRecord/putRecord.
    """
    status = _STATUS_MAP.get(
        book.status_id, "social.popfeed.feed.listItem#backlog"
    )

    record: dict = {
        "$type": _COLLECTION_LIST_ITEM,
        "listUri": list_uri,
        "creativeWorkType": "book",
        "status": status,
        "identifiers": identifiers.as_dict(),
        "addedAt": book.date_added or now,
        "updatedAt": now,
    }

    if book.title:
        record["title"] = book.title

    if book.cover_url:
        record["image"] = book.cover_url

    # Attach reading progress for in-progress books
    if book.status_id in (2, 4) and book.latest_read:
        progress: dict = {"status": "in_progress"}
        if book.latest_read.progress_pages is not None:
            progress["currentPage"] = book.latest_read.progress_pages
        if book.pages is not None:
            progress["totalPages"] = book.pages
        progress["updatedAt"] = now
        record["bookProgress"] = progress

    # Attach rating for finished books
    if book.status_id == 3 and book.rating is not None:
        record["rating"] = book.rating

    return record


class PopfeedClient:
    """High-level Popfeed operations for syncing books.

    Parameters:
        atproto (AtProtoClient): Authenticated AT Protocol client.
        dry_run (bool): If True, log but do not write records.
    """

    def __init__(
        self, atproto: AtProtoClient, dry_run: bool = False
    ) -> None:
        """Initialise with an authenticated AT Protocol client.

        Parameters:
            atproto (AtProtoClient): Authenticated AT Protocol client.
            dry_run (bool): If True, log but do not write records.
        """
        self._atproto = atproto
        self._dry_run = dry_run

    def ensure_books_list(
        self, hint_uri: Optional[str] = None
    ) -> str:
        """Find or create the user's Books list on Popfeed.

        Parameters:
            hint_uri (Optional[str]): Pre-configured list URI to use.

        Returns:
            str: AT URI of the Books list.
        """
        if hint_uri:
            logger.info("Using pre-configured Books list: %s", hint_uri)
            return hint_uri

        did = self._atproto.session.did
        logger.info("Searching for existing Books list...")

        for record in self._atproto.iter_all_records(did, _COLLECTION_LIST):
            value: dict = record.get("value", {})
            if value.get("name") == _BOOKS_LIST_NAME:
                uri: str = record["uri"]
                logger.info("Found existing Books list: %s", uri)
                return uri

        logger.info("No Books list found; creating one.")
        return self._create_books_list(did)

    def _create_books_list(self, did: str) -> str:
        """Create a new Books list on Popfeed.

        Parameters:
            did (str): The user's DID.

        Returns:
            str: AT URI of the newly created list.
        """
        record = {
            "$type": _COLLECTION_LIST,
            "name": _BOOKS_LIST_NAME,
            "listType": "books",
            "createdAt": _now_iso(),
        }
        if self._dry_run:
            logger.info("[dry-run] Would create Books list")
            return f"at://{did}/{_COLLECTION_LIST}/dry-run"
        result = self._atproto.create_record(
            did=did,
            collection=_COLLECTION_LIST,
            record=record,
        )
        uri: str = result["uri"]
        logger.info("Created Books list: %s", uri)
        return uri

    def _find_existing_list_item(
        self, did: str, identifiers: PopfeedIdentifiers
    ) -> Optional[dict]:
        """Search all listItems to find one matching the given identifiers.

        Parameters:
            did (str): The user's DID.
            identifiers (PopfeedIdentifiers): Desired book identifiers.

        Returns:
            Optional[dict]: The matching record (uri, cid, value),
                or None if not found.
        """
        for record in self._atproto.iter_all_records(
            did, _COLLECTION_LIST_ITEM
        ):
            value: dict = record.get("value", {})
            if value.get("creativeWorkType") != "book":
                continue
            existing_ids: dict = value.get("identifiers", {})
            if _identifiers_match(existing_ids, identifiers):
                return record
        return None

    def sync_book(self, book: HardcoverBook, list_uri: str) -> None:
        """Sync a single Hardcover book to the Popfeed Books list.

        Creates a new listItem record if none exists, or updates the
        existing one if the status or progress has changed.

        Parameters:
            book (HardcoverBook): The Hardcover book to sync.
            list_uri (str): AT URI of the Books list.
        """
        did = self._atproto.session.did
        identifiers = _build_identifiers(book)
        now = _now_iso()
        desired_record = _build_list_item_record(
            book, list_uri, identifiers, now
        )

        existing = self._find_existing_list_item(did, identifiers)

        if existing is None:
            logger.info(
                "[create] %r (status_id=%d)", book.title, book.status_id
            )
            if not self._dry_run:
                self._atproto.create_record(
                    did=did,
                    collection=_COLLECTION_LIST_ITEM,
                    record=desired_record,
                )
            else:
                logger.info("[dry-run] Would create listItem for %r", book.title)
            return

        existing_value: dict = existing.get("value", {})
        if not _needs_update(existing_value, desired_record):
            logger.debug("No update needed for %r", book.title)
            return

        rkey: str = existing["uri"].split("/")[-1]
        logger.info(
            "[update] %r (status_id=%d)", book.title, book.status_id
        )
        if not self._dry_run:
            self._atproto.put_record(
                did=did,
                collection=_COLLECTION_LIST_ITEM,
                rkey=rkey,
                record=desired_record,
            )
        else:
            logger.info("[dry-run] Would update listItem for %r", book.title)


def _needs_update(existing: dict, desired: dict) -> bool:
    """Determine if an existing listItem needs to be updated.

    Compares status, rating, and bookProgress fields.

    Parameters:
        existing (dict): Current record value from Popfeed.
        desired (dict): Desired record value.

    Returns:
        bool: True if any relevant field differs.
    """
    if existing.get("status") != desired.get("status"):
        return True
    if existing.get("rating") != desired.get("rating"):
        return True
    if existing.get("bookProgress") != desired.get("bookProgress"):
        return True
    return False
