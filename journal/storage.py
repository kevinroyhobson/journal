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


__all__ = [
    "SavedEntry",
    "save_journal_entry",
    "list_entries",
    "load_entry",
    "load_entries_for_date",
    "load_recent_entries",
    "load_memory",
    "save_memory",
    "InvalidToken",
]
