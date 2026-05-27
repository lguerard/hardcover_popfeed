# hardcover_popfeed

Sync your reading library and progress from [Hardcover](https://hardcover.app)
to [Popfeed](https://popfeed.social) via the AT Protocol.

## Requirements

- Python 3.11+
- A [Hardcover API token](https://hardcover.app/account/api)
- A Popfeed / Bluesky account and app password

## Installation

```bash
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable                 | Required | Description                                       |
| ------------------------ | -------- | ------------------------------------------------- |
| `HARDCOVER_TOKEN`        | Yes      | Hardcover API token                               |
| `POPFEED_IDENTIFIER`     | Yes      | Your Popfeed handle (e.g. `you.bsky.social`)      |
| `POPFEED_PASSWORD`       | Yes      | App password                                      |
| `POPFEED_PDS_URL`        | No       | PDS URL (default: `https://bsky.social`)          |
| `POPFEED_BOOKS_LIST_URI` | No       | Pre-configured list AT URI; auto-created if unset |
| `DRY_RUN`                | No       | Set to `true` to log without writing              |

## Usage

```bash
# Run the sync
hardcover-popfeed

# Dry run — logs actions without writing anything
hardcover-popfeed --dry-run

# Use a specific .env file
hardcover-popfeed --env-file /path/to/.env
```

## How It Works

1. Fetches all books from your Hardcover library (all statuses).
2. Authenticates with the Popfeed PDS via AT Protocol.
3. Finds or creates a "Books" list on your Popfeed profile.
4. For each book, creates or updates a `social.popfeed.feed.listItem`
   record with reading status, progress, and rating.

### Status Mapping

| Hardcover Status | Popfeed Status |
| ---------------- | -------------- |
| Want to Read (1) | backlog        |
| Reading (2)      | in_progress    |
| Read (3)         | finished       |
| Paused (4)       | in_progress    |
| Abandoned (5)    | abandoned      |
