"""Hardcover GraphQL API client."""

import logging
from typing import Any

import httpx

from hardcover_popfeed.models import HardcoverBook, HardcoverBookRead

logger = logging.getLogger(__name__)

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"

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
      book {
        id
        title
        pages
        release_date
        image {
          url
        }
        editions(limit: 5) {
          isbn_10
          isbn_13
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

            # Extract best isbn_10 / isbn_13 from editions
            isbn_10: str | None = None
            isbn_13: str | None = None
            for edition in book_data.get("editions") or []:
                if not isbn_13 and edition.get("isbn_13"):
                    isbn_13 = edition["isbn_13"]
                if not isbn_10 and edition.get("isbn_10"):
                    isbn_10 = edition["isbn_10"]

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
