"""Popfeed-specific operations built on top of the AT Protocol client."""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from hardcover_popfeed.atproto import AtProtoClient
from hardcover_popfeed.models import HardcoverBook, PopfeedIdentifiers

logger = logging.getLogger(__name__)

_COLLECTION_LIST = "social.popfeed.feed.list"
_COLLECTION_LIST_ITEM = "social.popfeed.feed.listItem"

# Hardcover status_id → Popfeed list type (status_id 5 = abandoned, intentionally excluded)
_LIST_TYPE_MAP: dict[int, str] = {
    1: "to_read_books",
    2: "currently_reading_books",
    3: "read_books",
    4: "currently_reading_books",  # paused → currently reading
}

# Popfeed list type → display name (includes the shared Recent list)
_LIST_NAMES: dict[str, str] = {
    "to_read_books": "Want to Read",
    "currently_reading_books": "Currently Reading",
    "read_books": "Read Books",
    "recent": "Recent",
}


def _book_id_key(identifiers: PopfeedIdentifiers) -> str:
    """Return a stable, rkey-safe identity segment for a book.

    Priority isbn13 > isbn10 > other (``hardcover:<id>``).  The value is
    stripped to alphanumerics so the resulting record key matches the AT
    Protocol rkey grammar.

    Parameters:
        identifiers (PopfeedIdentifiers): The book identifiers.

    Returns:
        str: An identity segment such as ``"isbn13.9780985545505"`` or
            ``"hc.2114801"``.
    """
    if identifiers.isbn13:
        return f"isbn13.{re.sub(r'[^0-9Xx]', '', identifiers.isbn13)}"
    if identifiers.isbn10:
        return f"isbn10.{re.sub(r'[^0-9Xx]', '', identifiers.isbn10)}"
    other = identifiers.other or ""
    return f"hc.{re.sub(r'[^0-9A-Za-z]', '', other)}"


def _status_rkey(id_key: str) -> str:
    """Deterministic rkey for a book's single status-list item.

    The same rkey is reused as the book moves between the Want to Read,
    Currently Reading, and Read Books lists, so putRecord moves the record in
    place instead of leaving a stale copy in the old list.
    """
    return f"b.s.{id_key}"


def _recent_rkey(id_key: str) -> str:
    """Deterministic rkey for a finished book's Recent-list item."""
    return f"b.r.{id_key}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        str: Current timestamp in ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` form.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_datetime_iso(value: Optional[str], fallback: str) -> str:
    """Normalise a date or datetime string to a full ISO-8601 datetime.

    AT Protocol's ``datetime`` type requires a full timestamp with timezone.
    Hardcover often returns bare dates (e.g. ``"2026-05-22"``); this helper
    converts them to ``"2026-05-22T00:00:00Z"`` so the indexer accepts the
    record.

    Parameters:
        value (Optional[str]): Source date or datetime string.
        fallback (str): ISO-8601 datetime to use when ``value`` is absent or
            cannot be parsed.

    Returns:
        str: ISO-8601 datetime string ending in ``Z``.
    """
    if not value:
        return fallback

    text = value.strip()
    if not text:
        return fallback

    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{text}T00:00:00")
        except ValueError:
            logger.warning(
                "Invalid datetime value %r; using fallback", value
            )
            return fallback

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _is_book_complete(book: HardcoverBook) -> bool:
    """Return True when progress_pages reaches the total page count.

    Used to promote a currently-reading book to read when Hardcover has not
    yet updated status_id to 3 despite the user reaching the last page.

    Parameters:
        book (HardcoverBook): Source book record.

    Returns:
        bool: True if progress_pages >= pages and both are known.
    """
    return (
        book.latest_read is not None
        and book.latest_read.progress_pages is not None
        and book.pages is not None
        and book.pages > 0
        and book.latest_read.progress_pages >= book.pages
    )


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
        other=f"hardcover:{book.book_id}"
        if not (book.isbn_10 or book.isbn_13)
        else None,
    )


def _build_list_item_record(
    book: HardcoverBook,
    list_uri: str,
    list_type: str,
    identifiers: PopfeedIdentifiers,
    now: str,
) -> dict:
    """Build the Popfeed listItem record value for a book.

    Parameters:
        book (HardcoverBook): Source Hardcover book.
        list_uri (str): AT URI of the parent list.
        list_type (str): Popfeed list type (e.g. ``"read_books"``).
        identifiers (PopfeedIdentifiers): Book identifiers.
        now (str): ISO-8601 timestamp for ``addedAt``/``updatedAt``.

    Returns:
        dict: Record value ready for createRecord/putRecord.
    """
    record: dict = {
        "$type": _COLLECTION_LIST_ITEM,
        "listUri": list_uri,
        "listType": list_type,
        "creativeWorkType": "book",
        "identifiers": identifiers.as_dict(),
        "addedAt": _to_datetime_iso(book.date_added, fallback=now),
        "updatedAt": now,
    }

    if book.title:
        record["title"] = book.title

    if book.cover_url:
        record["posterUrl"] = book.cover_url

    # Attach reading progress for currently-reading books
    if list_type == "currently_reading_books" and book.latest_read:
        progress: dict = {"status": "in_progress"}
        if book.latest_read.progress_pages is not None:
            progress["currentPage"] = book.latest_read.progress_pages
        if book.pages is not None:
            progress["totalPages"] = book.pages
        progress["updatedAt"] = now
        record["bookProgress"] = progress

    # Attach rating for finished books
    if list_type == "read_books" and book.rating is not None:
        record["rating"] = book.rating

    return record


def _resolve_finish_date(book: HardcoverBook, fallback: str) -> str:
    """Return the best available finish date for a completed book.

    Prefers ``latest_read.finished_at``, falls back to
    ``last_read_date``, then to ``fallback``.

    Parameters:
        book (HardcoverBook): Source Hardcover book.
        fallback (str): ISO-8601 datetime to use when no date is found.

    Returns:
        str: ISO-8601 datetime string.
    """
    if book.latest_read and book.latest_read.finished_at:
        result = _to_datetime_iso(
            book.latest_read.finished_at, fallback=fallback
        )
        if result != fallback:
            return result
    return _to_datetime_iso(book.last_read_date, fallback=fallback)


def _build_recent_list_item_record(
    book: HardcoverBook,
    recent_list_uri: str,
    identifiers: PopfeedIdentifiers,
    now: str,
) -> dict:
    """Build a Popfeed listItem record for the Recent list.

    Only finished books are added to the Recent list. The completion
    date is sourced from the most recent reading session when available.

    Parameters:
        book (HardcoverBook): Source Hardcover book (must be finished).
        recent_list_uri (str): AT URI of the Recent list.
        identifiers (PopfeedIdentifiers): Book identifiers.
        now (str): ISO-8601 timestamp used as ``updatedAt`` and fallback.

    Returns:
        dict: Record value ready for createRecord/putRecord.
    """
    finished_at = _resolve_finish_date(book, fallback=now)

    record: dict = {
        "$type": _COLLECTION_LIST_ITEM,
        "listUri": recent_list_uri,
        "creativeWorkType": "book",
        "identifiers": identifiers.as_dict(),
        "status": "finished",
        "addedAt": finished_at,
        "completedAt": finished_at,
        "updatedAt": now,
    }

    if book.title:
        record["title"] = book.title

    if book.cover_url:
        record["posterUrl"] = book.cover_url

    if book.rating is not None:
        record["rating"] = book.rating

    return record


class PopfeedClient:
    """High-level Popfeed operations for syncing books.

    Parameters:
        atproto (AtProtoClient): Authenticated AT Protocol client.
        dry_run (bool): If True, log but do not write records.
    """

    def __init__(self, atproto: AtProtoClient, dry_run: bool = False) -> None:
        """Initialise with an authenticated AT Protocol client.

        Parameters:
            atproto (AtProtoClient): Authenticated AT Protocol client.
            dry_run (bool): If True, log but do not write records.
        """
        self._atproto = atproto
        self._dry_run = dry_run

    def ensure_status_lists(self) -> dict[str, str]:
        """Find or create all book lists on Popfeed in a single scan.

        Discovers and creates (where missing) the three status-specific
        lists and the shared Recent list.

        Returns:
            dict[str, str]: Mapping of list type to AT URI, including
                ``"recent"`` for the Recent list.
        """
        did = self._atproto.session.did
        needed: set[str] = set(_LIST_NAMES.keys())
        found: dict[str, str] = {}

        logger.info("Searching for existing book lists...")
        for record in self._atproto.iter_all_records(did, _COLLECTION_LIST):
            value: dict = record.get("value", {})
            lt: str = value.get("listType", "")
            if lt in needed:
                found[lt] = record["uri"]
                logger.info("Found %r list: %s", lt, record["uri"])

        for list_type in needed:
            if list_type not in found:
                uri = self._create_typed_list(did, list_type)
                found[list_type] = uri

        return found

    def _create_typed_list(self, did: str, list_type: str) -> str:
        """Create a list on Popfeed with the given type.

        Parameters:
            did (str): The user's DID.
            list_type (str): Popfeed list type (e.g. ``"read_books"``).

        Returns:
            str: AT URI of the newly created list.
        """
        name = _LIST_NAMES[list_type]
        record = {
            "$type": _COLLECTION_LIST,
            "name": name,
            "listType": list_type,
            "authorDid": did,
            "createdAt": _now_iso(),
        }
        if self._dry_run:
            logger.info("[dry-run] Would create %r list", name)
            return f"at://{did}/{_COLLECTION_LIST}/dry-run-{list_type}"
        result = self._atproto.create_record(
            did=did,
            collection=_COLLECTION_LIST,
            record=record,
        )
        uri: str = result["uri"]
        logger.info("Created %r list: %s", name, uri)
        return uri

    def purge_legacy_book_records(self) -> None:
        """Delete book listItems written with non-deterministic (random TID) rkeys.

        Older versions created each book listItem with createRecord, producing a
        random rkey.  The plugin now writes deterministic, dotted rkeys
        (``b.s.…`` / ``b.r.…``), so any book listItem whose rkey contains no dot
        is a legacy duplicate and is removed.  The scan is scoped to
        ``creativeWorkType == "book"`` so records owned by other Popfeed sources
        sharing the same repo (e.g. Jellyfin movies and episodes) are never
        touched, even though they also use random TID rkeys.
        """
        did = self._atproto.session.did
        removed = 0
        for record in self._atproto.iter_all_records(did, _COLLECTION_LIST_ITEM):
            value: dict = record.get("value", {})
            if value.get("creativeWorkType") != "book":
                continue
            rkey = record["uri"].split("/")[-1]
            if "." in rkey:
                continue
            if not self._dry_run:
                self._atproto.delete_record(did, _COLLECTION_LIST_ITEM, rkey)
            removed += 1
        if removed:
            logger.info("Purged %d legacy book record(s).", removed)

    def sync_book(
        self, book: HardcoverBook, list_uris: dict[str, str]
    ) -> None:
        """Sync a single Hardcover book to the appropriate Popfeed lists.

        Routes the book to the correct status-specific list based on
        ``book.status_id``. Finished books (status_id=3) are also added
        to the Recent list. Creates a new listItem if none exists, or
        updates the existing one if anything has changed.

        Parameters:
            book (HardcoverBook): The Hardcover book to sync.
            list_uris (dict[str, str]): Mapping of list type to AT URI
                as returned by :meth:`ensure_status_lists`.
        """
        did = self._atproto.session.did
        list_type = _LIST_TYPE_MAP.get(book.status_id)
        if list_type is None:
            logger.debug(
                "Skipping %r (status_id=%d, not synced)",
                book.title,
                book.status_id,
            )
            return

        # Hardcover sometimes keeps status_id=2 when the user reaches the last
        # page without explicitly marking the book finished.  Promote to
        # read_books so the record lands in the right list and is added to
        # the Recent list, matching user intent.
        if list_type == "currently_reading_books" and _is_book_complete(book):
            logger.info(
                "Promoting %r to read_books (progress_pages=%d >= pages=%d)",
                book.title,
                book.latest_read.progress_pages,  # type: ignore[union-attr]
                book.pages,
            )
            list_type = "read_books"

        list_uri = list_uris[list_type]
        identifiers = _build_identifiers(book)
        id_key = _book_id_key(identifiers)
        status_rkey = _status_rkey(id_key)
        now = _now_iso()
        desired_record = _build_list_item_record(
            book, list_uri, list_type, identifiers, now
        )

        # One deterministic record per book.  putRecord upserts in place and, when
        # the status changes, moves the record between lists by rewriting listUri.
        existing = self._atproto.get_record(
            did, _COLLECTION_LIST_ITEM, status_rkey
        )
        existing_value = existing.get("value") if existing else None
        if existing_value and existing_value.get("addedAt"):
            # Preserve the original add date so unchanged books stay idempotent.
            desired_record["addedAt"] = existing_value["addedAt"]

        if existing_value is not None and not _needs_update(
            existing_value, desired_record
        ):
            logger.debug("No update needed for %r", book.title)
        else:
            verb = "update" if existing_value is not None else "create"
            logger.info(
                "[%s] %r (status_id=%d)", verb, book.title, book.status_id
            )
            if not self._dry_run:
                self._atproto.put_record(
                    did=did,
                    collection=_COLLECTION_LIST_ITEM,
                    rkey=status_rkey,
                    record=desired_record,
                )
            else:
                logger.info(
                    "[dry-run] Would %s listItem for %r", verb, book.title
                )

        # Keep the Recent list in sync: finished books appear, others are removed.
        recent_list_uri = list_uris.get("recent")
        if recent_list_uri:
            if list_type == "read_books":
                self._sync_to_recent_list(
                    book, recent_list_uri, identifiers, id_key, now
                )
            else:
                self._remove_from_recent_list(book, id_key)

    def _sync_to_recent_list(
        self,
        book: HardcoverBook,
        recent_list_uri: str,
        identifiers: PopfeedIdentifiers,
        id_key: str,
        now: str,
    ) -> None:
        """Add or update a finished book in the Recent list.

        Parameters:
            book (HardcoverBook): The finished book to sync.
            recent_list_uri (str): AT URI of the Recent list.
            identifiers (PopfeedIdentifiers): Book identifiers.
            id_key (str): The book's deterministic identity segment.
            now (str): ISO-8601 timestamp used as ``updatedAt`` fallback.
        """
        did = self._atproto.session.did
        recent_rkey = _recent_rkey(id_key)
        desired = _build_recent_list_item_record(
            book, recent_list_uri, identifiers, now
        )

        existing = self._atproto.get_record(
            did, _COLLECTION_LIST_ITEM, recent_rkey
        )
        existing_value = existing.get("value") if existing else None
        if existing_value:
            # Preserve original timestamps so re-runs do not churn the record.
            if existing_value.get("addedAt"):
                desired["addedAt"] = existing_value["addedAt"]
            if existing_value.get("completedAt"):
                desired["completedAt"] = existing_value["completedAt"]

        if existing_value is not None and not _needs_recent_update(
            existing_value, desired
        ):
            logger.debug("No update needed for %r in Recent list", book.title)
            return

        verb = "update" if existing_value is not None else "create"
        logger.info("[%s] %r in Recent list", verb, book.title)
        if not self._dry_run:
            self._atproto.put_record(
                did=did,
                collection=_COLLECTION_LIST_ITEM,
                rkey=recent_rkey,
                record=desired,
            )
        else:
            logger.info(
                "[dry-run] Would %s %r in Recent list", verb, book.title
            )

    def _remove_from_recent_list(
        self, book: HardcoverBook, id_key: str
    ) -> None:
        """Remove a no-longer-finished book from the Recent list, if present.

        Parameters:
            book (HardcoverBook): The book whose status is not ``read_books``.
            id_key (str): The book's deterministic identity segment.
        """
        if self._dry_run:
            return
        did = self._atproto.session.did
        # delete_record tolerates a missing record, so no existence check needed.
        self._atproto.delete_record(
            did, _COLLECTION_LIST_ITEM, _recent_rkey(id_key)
        )


def _needs_update(existing: dict, desired: dict) -> bool:
    """Determine if an existing status-list listItem needs to be updated.

    Compares listUri, listType, rating, bookProgress, and addedAt.

    Parameters:
        existing (dict): Current record value from Popfeed.
        desired (dict): Desired record value.

    Returns:
        bool: True if any relevant field differs.
    """
    if existing.get("listUri") != desired.get("listUri"):
        return True
    if existing.get("listType") != desired.get("listType"):
        return True
    if existing.get("rating") != desired.get("rating"):
        return True
    if existing.get("bookProgress") != desired.get("bookProgress"):
        return True
    if existing.get("addedAt") != desired.get("addedAt"):
        return True
    return False


def _needs_recent_update(existing: dict, desired: dict) -> bool:
    """Determine if an existing Recent listItem needs to be updated.

    Compares the fields relevant to recent finished-book entries:
    status, rating, completedAt, and addedAt.

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
    if existing.get("completedAt") != desired.get("completedAt"):
        return True
    if existing.get("addedAt") != desired.get("addedAt"):
        return True
    return False
