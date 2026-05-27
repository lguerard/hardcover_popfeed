"""Data models for Hardcover and Popfeed entities."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HardcoverBookRead:
    """A single reading session or progress record.

    Parameters:
        progress_pages (Optional[int]): Pages read so far.
        started_at (Optional[str]): ISO-8601 start date.
        finished_at (Optional[str]): ISO-8601 finish date.
    """

    progress_pages: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


@dataclass
class HardcoverBook:
    """A book entry from the Hardcover library.

    Parameters:
        user_book_id (int): Unique ID of the user_book record.
        status_id (int): Reading status (1=Want to Read, 2=Reading,
            3=Read, 4=Paused, 5=Abandoned).
        rating (Optional[float]): User rating (0–5).
        date_added (Optional[str]): ISO-8601 date added.
        last_read_date (Optional[str]): ISO-8601 last read date.
        updated_at (Optional[str]): ISO-8601 last update timestamp.
        book_id (int): Hardcover book ID.
        title (str): Book title.
        pages (Optional[int]): Total pages.
        release_date (Optional[str]): ISO-8601 release date.
        cover_url (Optional[str]): URL of the cover image.
        isbn_10 (Optional[str]): ISBN-10 identifier.
        isbn_13 (Optional[str]): ISBN-13 identifier.
        latest_read (Optional[HardcoverBookRead]): Most recent read
            session.
    """

    user_book_id: int
    status_id: int
    rating: Optional[float] = None
    date_added: Optional[str] = None
    last_read_date: Optional[str] = None
    updated_at: Optional[str] = None
    book_id: int = 0
    title: str = ""
    pages: Optional[int] = None
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    isbn_10: Optional[str] = None
    isbn_13: Optional[str] = None
    latest_read: Optional[HardcoverBookRead] = field(default=None)


@dataclass
class PopfeedIdentifiers:
    """Identifiers used to match a book on Popfeed.

    Parameters:
        isbn10 (Optional[str]): ISBN-10 value.
        isbn13 (Optional[str]): ISBN-13 value.
        other (Optional[str]): Fallback identifier (e.g. hardcover:<id>).
    """

    isbn10: Optional[str] = None
    isbn13: Optional[str] = None
    other: Optional[str] = None

    def as_dict(self) -> dict:
        """Return a dict containing only non-None identifier fields.

        Returns:
            dict: Mapping of identifier key to value.
        """
        result: dict = {}
        if self.isbn13:
            result["isbn13"] = self.isbn13
        if self.isbn10:
            result["isbn10"] = self.isbn10
        if self.other:
            result["other"] = self.other
        return result
