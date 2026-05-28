"""Hardcover GraphQL API client."""

import logging
from typing import Any

import httpx

from hardcover_popfeed.models import HardcoverBook, HardcoverBookRead

logger = logging.getLogger(__name__)

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"

# Preferred edition languages (ISO 639-1 codes), in priority order.
_PREFERRED_LANGUAGES: list[str] = ["en", "fr"]

_USER_BOOKS_QUERY = """
query GetUserBooks {
  me {
    user_books(where: { status_id: { _in: [1, 2, 3, 4, 5] } }) {
      id
      status_id
      rating
      date_added
      last_read_date
      updated_at
      edition {
        isbn_10
        isbn_13
        language {
          code2
        }
      }
      book {
        id
        title
        pages
        release_date
        image {
          url
        }
        editions(limit: 20) {
          isbn_10
          isbn_13
          language {
            code2
          }
        }
      }
      user_book_reads(
        limit: 1
        order_by: { finished_at: desc_nulls_last }
      ) {
        progress_pages
        started_at
        finished_at
      }
    }
  }
}
"""


class HardcoverError(Exception):
    """Raised when the Hardcover API returns an error."""


class HardcoverClient:
    """Client for the Hardcover GraphQL API.

    Parameters:
        token (str): Hardcover API token.
    """

    def __init__(self, token: str) -> None:
        """Initialise the client with an auth token.

        Parameters:
            token (str): Hardcover API token.
        """
        self._token = token
        self._http = httpx.Client(
            headers={
                "content-type": "application/json",
                "authorization": token,
            },
            timeout=35.0,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "HardcoverClient":
        """Return self for use as a context manager."""
        return self

    def __exit__(self, *_: Any) -> None:
        """Close the HTTP client on context exit."""
        self.close()

    def _execute(self, query: str) -> dict:
        """Execute a GraphQL query.

        Parameters:
            query (str): GraphQL query string.

        Returns:
            dict: The ``data`` field of the GraphQL response.

        Raises:
            HardcoverError: On HTTP or GraphQL errors.
        """
        try:
            response = self._http.post(
                HARDCOVER_API_URL,
                json={"query": query},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HardcoverError(
                f"HTTP {exc.response.status_code} from Hardcover API"
            ) from exc
        except httpx.RequestError as exc:
            raise HardcoverError(f"Request to Hardcover API failed: {exc}") from exc

        payload: dict = response.json()
        errors = payload.get("errors")
        if errors:
            messages = "; ".join(e.get("message", str(e)) for e in errors)
            raise HardcoverError(f"GraphQL errors: {messages}")

        data = payload.get("data")
        if data is None:
            raise HardcoverError("GraphQL response missing 'data' field")
        return data

    def get_user_books(self) -> list[HardcoverBook]:
        """Fetch all books from the authenticated user's library.

        Returns:
            list[HardcoverBook]: The user's book entries.

        Raises:
            HardcoverError: On API or parsing errors.
        """
        data = self._execute(_USER_BOOKS_QUERY)
        me = data.get("me")
        if not me or not isinstance(me, list) or not me:
            raise HardcoverError("Unexpected 'me' structure in response")

        raw_books: list[dict] = me[0].get("user_books", [])
        books: list[HardcoverBook] = []

        for raw in raw_books:
            book_data: dict = raw.get("book") or {}
            image_data: dict = book_data.get("image") or {}

            # Extract best isbn_10 / isbn_13, preferring English/French editions.
            isbn_10, isbn_13 = _pick_isbns(
                user_edition=raw.get("edition"),
                book_editions=book_data.get("editions") or [],
            )

            # Latest read session
            reads_raw: list[dict] = raw.get("user_book_reads") or []
            latest_read: HardcoverBookRead | None = None
            if reads_raw:
                r = reads_raw[0]
                latest_read = HardcoverBookRead(
                    progress_pages=r.get("progress_pages"),
                    started_at=r.get("started_at"),
                    finished_at=r.get("finished_at"),
                )

            rating_raw = raw.get("rating")
            rating: float | None = None
            if rating_raw is not None:
                try:
                    rating = float(rating_raw)
                except (TypeError, ValueError):
                    pass

            books.append(
                HardcoverBook(
                    user_book_id=raw["id"],
                    status_id=raw["status_id"],
                    rating=rating,
                    date_added=raw.get("date_added"),
                    last_read_date=raw.get("last_read_date"),
                    updated_at=raw.get("updated_at"),
                    book_id=book_data.get("id", 0),
                    title=book_data.get("title", ""),
                    pages=book_data.get("pages"),
                    release_date=book_data.get("release_date"),
                    cover_url=image_data.get("url"),
                    isbn_10=isbn_10,
                    isbn_13=isbn_13,
                    latest_read=latest_read,
                )
            )

        logger.info("Fetched %d books from Hardcover", len(books))
        return books


def _edition_language(edition: dict) -> str | None:
    """Return the ISO 639-1 language code for an edition dict, or None.

    Parameters:
        edition (dict): Raw edition data from the Hardcover API.

    Returns:
        str | None: Two-letter language code (e.g. ``"en"``), or None.
    """
    lang = (edition.get("language") or {}).get("code2")
    return lang.lower() if lang else None


def _isbn10_to_isbn13(isbn10: str) -> str | None:
    """Convert a 10-digit ISBN-10 to an ISBN-13 with the ``978`` prefix.

    Parameters:
        isbn10 (str): The ISBN-10 string (digits only, length 10).

    Returns:
        str | None: The corresponding ISBN-13, or None if conversion fails.
    """
    digits = isbn10.replace("-", "").replace(" ", "")
    if len(digits) != 10 or not digits[:9].isdigit():
        return None
    base = "978" + digits[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(base))
    check = (10 - (total % 10)) % 10
    return base + str(check)


def _pick_isbns(
    user_edition: dict | None,
    book_editions: list[dict],
) -> tuple[str | None, str | None]:
    """Select the best ISBN pair, preferring English/French editions.

    Selection priority:
    1. A preferred-language edition with an ISBN-13.
    2. A preferred-language edition with an ISBN-10 (converted to ISBN-13).
    3. Any edition with an ISBN-13.
    4. Any edition with an ISBN-10 (converted to ISBN-13).

    The user's specific edition is considered before the book's edition list.

    Parameters:
        user_edition (dict | None): The user's specific edition data.
        book_editions (list[dict]): All edition data from the book.

    Returns:
        tuple[str | None, str | None]: ``(isbn_10, isbn_13)`` to use.
    """
    candidates: list[dict] = []
    if user_edition:
        candidates.append(user_edition)
    candidates.extend(book_editions)

    def _is_preferred(edition: dict) -> bool:
        lang = _edition_language(edition)
        return lang in _PREFERRED_LANGUAGES if lang else False

    # Pass 1: preferred language + ISBN-13
    for ed in candidates:
        if _is_preferred(ed) and ed.get("isbn_13"):
            isbn13 = ed["isbn_13"]
            return ed.get("isbn_10") or None, isbn13

    # Pass 2: preferred language + ISBN-10 (convert to ISBN-13)
    for ed in candidates:
        if _is_preferred(ed) and ed.get("isbn_10"):
            isbn10 = ed["isbn_10"]
            isbn13 = _isbn10_to_isbn13(isbn10)
            if isbn13:
                logger.debug("Converted ISBN-10 %s → ISBN-13 %s", isbn10, isbn13)
                return isbn10, isbn13

    # Pass 3: any edition with ISBN-13
    for ed in candidates:
        if ed.get("isbn_13"):
            return ed.get("isbn_10") or None, ed["isbn_13"]

    # Pass 4: any edition with ISBN-10 (convert to ISBN-13)
    for ed in candidates:
        if ed.get("isbn_10"):
            isbn10 = ed["isbn_10"]
            isbn13 = _isbn10_to_isbn13(isbn10)
            if isbn13:
                logger.debug("Converted ISBN-10 %s → ISBN-13 %s", isbn10, isbn13)
                return isbn10, isbn13
            return isbn10, None

    return None, None
