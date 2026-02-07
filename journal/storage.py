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
    passphrase: str,
    config: Config = DEFAULT_CONFIG,
) -> str:
    """Save a journal entry to an encrypted S3 object.

    Returns the S3 key of the saved object.
    """
    timestamp = datetime.now()
    filename = timestamp.strftime("%Y-%m-%dT%H-%M-%S") + ".enc"
    key = config.s3_prefix + filename

    data = {
        "timestamp": timestamp.isoformat(),
        "content": content,
    }
    plaintext = json.dumps(data, indent=2)

    encrypted = encrypt(plaintext, passphrase)
    _s3_client().put_object(Bucket=config.s3_bucket, Key=key, Body=encrypted)

    return key


def list_entries(config: Config = DEFAULT_CONFIG) -> dict[str, list[str]]:
    """List all saved entries grouped by date.

    Returns a dict mapping date strings to lists of S3 keys.
    """
    s3 = _s3_client()
    entries: dict[str, list[str]] = defaultdict(list)

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.s3_bucket, Prefix=config.s3_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.removeprefix(config.s3_prefix)

            # Skip non-entry files (e.g. memory/long.enc)
            if "/" in filename or not filename.endswith(".enc"):
                continue

            try:
                date_str = filename.split("T")[0]
                entries[date_str].append(key)
            except (IndexError, ValueError):
                continue

    # Sort keys within each date
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
    key = config.s3_prefix + "memory/long.enc"
    try:
        response = _s3_client().get_object(Bucket=config.s3_bucket, Key=key)
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
    key = config.s3_prefix + "memory/long.enc"
    encrypted = encrypt(content, passphrase)
    _s3_client().put_object(Bucket=config.s3_bucket, Key=key, Body=encrypted)
    return key


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
