"""Save and load encrypted journal entries in S3."""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import boto3
from botocore.exceptions import ClientError
from cryptography.fernet import InvalidToken

from journal.client import Message
from journal.config import Config, DEFAULT_CONFIG
from journal.crypto import decrypt, encrypt

ENTRIES_PREFIX = "entries/"
RAW_PREFIX = "raw/"
MEMORY_KEY = "memory/long.enc"
MONTHLY_PREFIX = "memory/months/"
WEEKLY_PREFIX = "memory/weeks/"
COMPACT_META_KEY = "memory/compact.enc"


def _s3_client():
    """Create an S3 client using the default boto3 credential chain."""
    return boto3.client("s3")


@dataclass
class SavedEntry:
    """A saved journal entry."""

    key: str
    timestamp: datetime
    content: str
    messages: list[Message] | None = None

    @property
    def date_label(self) -> str:
        """Human-readable timestamp, e.g. 'January 05, 2025, 3:42pm'."""
        return (
            self.timestamp.strftime("%B %d, %Y, ")
            + self.timestamp.strftime("%I:%M%p").lstrip("0").lower()
        )


def save_journal_entry(
    content: str,
    messages: list[Message],
    passphrase: str,
    config: Config = DEFAULT_CONFIG,
) -> str:
    """Save a journal entry and raw conversation to encrypted S3 objects.

    The entry goes to entries/{filename} and the raw conversation goes
    to raw/{filename} with the same timestamp, so they can be correlated
    by filename.

    Returns the S3 key of the journal entry.
    """
    s3 = _s3_client()
    timestamp = datetime.now()
    filename = timestamp.strftime("%Y-%m-%dT%H-%M-%S") + ".enc"

    # Save journal entry
    entry_key = ENTRIES_PREFIX + filename
    entry_data = {
        "timestamp": timestamp.isoformat(),
        "content": content,
    }
    encrypted = encrypt(json.dumps(entry_data, indent=2), passphrase)
    s3.put_object(Bucket=config.s3_bucket, Key=entry_key, Body=encrypted)

    # Save raw conversation
    raw_key = RAW_PREFIX + filename
    raw_data = {
        "timestamp": timestamp.isoformat(),
        "messages": [{"role": m.role, "content": m.content} for m in messages],
    }
    encrypted = encrypt(json.dumps(raw_data, indent=2), passphrase)
    s3.put_object(Bucket=config.s3_bucket, Key=raw_key, Body=encrypted)

    return entry_key


def list_entries(config: Config = DEFAULT_CONFIG) -> dict[str, list[str]]:
    """List all saved entries grouped by date.

    Returns a dict mapping date strings to lists of S3 keys.
    """
    s3 = _s3_client()
    entries: dict[str, list[str]] = defaultdict(list)

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.s3_bucket, Prefix=ENTRIES_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.removeprefix(ENTRIES_PREFIX)

            if not filename.endswith(".enc"):
                continue

            try:
                date_str = filename.split("T")[0]
                entries[date_str].append(key)
            except (IndexError, ValueError):
                continue

    for date_str in entries:
        entries[date_str].sort()

    return dict(entries)


def load_entry(key: str, passphrase: str, config: Config = DEFAULT_CONFIG) -> SavedEntry:
    """Load and decrypt a single entry from S3.

    Raises InvalidToken if passphrase is wrong.
    """
    response = _s3_client().get_object(Bucket=config.s3_bucket, Key=key)
    encrypted = response["Body"].read()
    plaintext = decrypt(encrypted, passphrase)
    data = json.loads(plaintext)

    timestamp = datetime.fromisoformat(data["timestamp"])

    # Handle both new format (content) and old format (messages)
    content = data.get("content", "")
    messages = None
    if "messages" in data:
        messages = [Message(role=m["role"], content=m["content"]) for m in data["messages"]]
        if not content:
            content = "\n\n".join(
                f"**{m.role.title()}:** {m.content}" for m in messages
            )

    return SavedEntry(key=key, timestamp=timestamp, content=content, messages=messages)


def load_entries_for_date(
    date_str: str, passphrase: str, config: Config = DEFAULT_CONFIG
) -> list[SavedEntry]:
    """Load all entries for a specific date.

    Raises InvalidToken if passphrase is wrong.
    """
    all_entries = list_entries(config)

    if date_str not in all_entries:
        return []

    return [load_entry(key, passphrase, config) for key in all_entries[date_str]]


def load_recent_entries(
    passphrase: str, config: Config = DEFAULT_CONFIG, days: int = 7
) -> list[SavedEntry]:
    """Load all journal entries from the past N days.

    Returns entries sorted chronologically (oldest first).
    Raises InvalidToken if passphrase is wrong.
    """
    cutoff = date.today() - timedelta(days=days)
    all_dates = list_entries(config)
    entries = []

    for date_str, keys in all_dates.items():
        try:
            entry_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if entry_date >= cutoff:
            for key in keys:
                entries.append(load_entry(key, passphrase, config))

    entries.sort(key=lambda e: e.timestamp)
    return entries


def load_memory(passphrase: str, config: Config = DEFAULT_CONFIG) -> str | None:
    """Load the long-running memory file from S3.

    Returns the decrypted memory text, or None if no memory file exists.
    Raises InvalidToken if passphrase is wrong.
    """
    try:
        response = _s3_client().get_object(Bucket=config.s3_bucket, Key=MEMORY_KEY)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        raise

    encrypted = response["Body"].read()
    return decrypt(encrypted, passphrase)


def save_memory(content: str, passphrase: str, config: Config = DEFAULT_CONFIG) -> str:
    """Save the long-running memory to an encrypted S3 object.

    Returns the S3 key.
    """
    encrypted = encrypt(content, passphrase)
    _s3_client().put_object(Bucket=config.s3_bucket, Key=MEMORY_KEY, Body=encrypted)
    return MEMORY_KEY


def _week_start(d: date) -> date:
    """Return the Monday of the week containing the given date."""
    return d - timedelta(days=d.weekday())


def save_weekly_memory(
    week_start: date, content: str, passphrase: str, config: Config = DEFAULT_CONFIG
) -> str:
    """Save a weekly memory summary. Returns the S3 key."""
    key = WEEKLY_PREFIX + week_start.isoformat() + ".enc"
    encrypted = encrypt(content, passphrase)
    _s3_client().put_object(Bucket=config.s3_bucket, Key=key, Body=encrypted)
    return key


def load_weekly_memory(
    week_start: date, passphrase: str, config: Config = DEFAULT_CONFIG
) -> str | None:
    """Load a weekly memory summary, or None if it doesn't exist."""
    key = WEEKLY_PREFIX + week_start.isoformat() + ".enc"
    try:
        response = _s3_client().get_object(Bucket=config.s3_bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        raise
    return decrypt(response["Body"].read(), passphrase)


def list_weekly_memories(
    passphrase: str, config: Config = DEFAULT_CONFIG
) -> list[tuple[date, str]]:
    """Load all weekly memories, sorted ascending by week start date."""
    s3 = _s3_client()
    results = []

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.s3_bucket, Prefix=WEEKLY_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.removeprefix(WEEKLY_PREFIX)
            if not filename.endswith(".enc"):
                continue
            try:
                week_date = date.fromisoformat(filename.removesuffix(".enc"))
            except ValueError:
                continue
            response = s3.get_object(Bucket=config.s3_bucket, Key=key)
            content = decrypt(response["Body"].read(), passphrase)
            results.append((week_date, content))

    results.sort(key=lambda x: x[0])
    return results


def save_monthly_memory(
    month: str, content: str, passphrase: str, config: Config = DEFAULT_CONFIG
) -> str:
    """Save a monthly memory summary. month is 'YYYY-MM'. Returns the S3 key."""
    key = MONTHLY_PREFIX + month + ".enc"
    encrypted = encrypt(content, passphrase)
    _s3_client().put_object(Bucket=config.s3_bucket, Key=key, Body=encrypted)
    return key


def load_monthly_memory(
    month: str, passphrase: str, config: Config = DEFAULT_CONFIG
) -> str | None:
    """Load a monthly memory summary, or None if it doesn't exist."""
    key = MONTHLY_PREFIX + month + ".enc"
    try:
        response = _s3_client().get_object(Bucket=config.s3_bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        raise
    return decrypt(response["Body"].read(), passphrase)


def list_monthly_memories(
    passphrase: str, config: Config = DEFAULT_CONFIG
) -> list[tuple[str, str]]:
    """Load all monthly memories, sorted ascending by month."""
    s3 = _s3_client()
    results = []

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.s3_bucket, Prefix=MONTHLY_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.removeprefix(MONTHLY_PREFIX)
            if not filename.endswith(".enc"):
                continue
            month_str = filename.removesuffix(".enc")
            response = s3.get_object(Bucket=config.s3_bucket, Key=key)
            content = decrypt(response["Body"].read(), passphrase)
            results.append((month_str, content))

    results.sort(key=lambda x: x[0])
    return results


def load_compact_metadata(
    passphrase: str, config: Config = DEFAULT_CONFIG
) -> dict:
    """Load compact metadata. Returns dict with 'last_compact' key, or empty dict."""
    try:
        response = _s3_client().get_object(Bucket=config.s3_bucket, Key=COMPACT_META_KEY)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return {}
        raise
    plaintext = decrypt(response["Body"].read(), passphrase)
    return json.loads(plaintext)


def save_compact_metadata(
    metadata: dict, passphrase: str, config: Config = DEFAULT_CONFIG
) -> None:
    """Save compact metadata."""
    encrypted = encrypt(json.dumps(metadata), passphrase)
    _s3_client().put_object(Bucket=config.s3_bucket, Key=COMPACT_META_KEY, Body=encrypted)


def load_entries_for_week(
    week_start: date, passphrase: str, config: Config = DEFAULT_CONFIG
) -> list[SavedEntry]:
    """Load all entries for a given week (Monday through Sunday), sorted chronologically."""
    week_end = week_start + timedelta(days=7)
    all_dates = list_entries(config)
    entries = []

    for date_str, keys in all_dates.items():
        try:
            entry_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if week_start <= entry_date < week_end:
            for key in keys:
                entries.append(load_entry(key, passphrase, config))

    entries.sort(key=lambda e: e.timestamp)
    return entries


def load_entries_for_month(
    month: str, passphrase: str, config: Config = DEFAULT_CONFIG
) -> list[SavedEntry]:
    """Load all entries for a given month ('YYYY-MM'), sorted chronologically."""
    all_dates = list_entries(config)
    entries = []

    for date_str, keys in all_dates.items():
        if date_str.startswith(month):
            for key in keys:
                entries.append(load_entry(key, passphrase, config))

    entries.sort(key=lambda e: e.timestamp)
    return entries


def load_entries_since(
    since: datetime, passphrase: str, config: Config = DEFAULT_CONFIG
) -> list[SavedEntry]:
    """Load all entries written after the given timestamp, sorted chronologically."""
    all_dates = list_entries(config)
    entries = []

    for keys in all_dates.values():
        for key in keys:
            entry = load_entry(key, passphrase, config)
            if entry.timestamp > since:
                entries.append(entry)

    entries.sort(key=lambda e: e.timestamp)
    return entries


def list_raw_conversations(config: Config = DEFAULT_CONFIG) -> dict[str, list[str]]:
    """List all raw conversations grouped by date.

    Returns a dict mapping date strings to lists of S3 keys.
    """
    s3 = _s3_client()
    entries: dict[str, list[str]] = defaultdict(list)

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.s3_bucket, Prefix=RAW_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.removeprefix(RAW_PREFIX)

            if not filename.endswith(".enc"):
                continue

            try:
                date_str = filename.split("T")[0]
                entries[date_str].append(key)
            except (IndexError, ValueError):
                continue

    for date_str in entries:
        entries[date_str].sort()

    return dict(entries)


def load_raw_conversation(
    key: str, passphrase: str, config: Config = DEFAULT_CONFIG
) -> tuple[datetime, list[Message]]:
    """Load and decrypt a raw conversation from S3.

    Returns (timestamp, messages).
    """
    response = _s3_client().get_object(Bucket=config.s3_bucket, Key=key)
    encrypted = response["Body"].read()
    plaintext = decrypt(encrypted, passphrase)
    data = json.loads(plaintext)

    timestamp = datetime.fromisoformat(data["timestamp"])
    messages = [Message(role=m["role"], content=m["content"]) for m in data["messages"]]
    return timestamp, messages


__all__ = [
    "SavedEntry",
    "save_journal_entry",
    "list_entries",
    "load_entry",
    "load_entries_for_date",
    "load_entries_for_week",
    "load_entries_for_month",
    "load_entries_since",
    "load_recent_entries",
    "load_memory",
    "save_memory",
    "save_weekly_memory",
    "load_weekly_memory",
    "list_weekly_memories",
    "save_monthly_memory",
    "load_monthly_memory",
    "list_monthly_memories",
    "load_compact_metadata",
    "save_compact_metadata",
    "list_raw_conversations",
    "load_raw_conversation",
    "InvalidToken",
]
