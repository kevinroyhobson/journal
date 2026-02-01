"""Save and load encrypted conversations."""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from cryptography.fernet import InvalidToken

from journal.client import Message
from journal.config import Config, DEFAULT_CONFIG
from journal.crypto import decrypt, encrypt


@dataclass
class SavedEntry:
    """A saved journal entry."""

    filepath: Path
    timestamp: datetime
    content: str  # The journal entry text
    messages: list[Message] | None = None  # Optional conversation transcript


def save_journal_entry(
    content: str,
    passphrase: str,
    config: Config = DEFAULT_CONFIG,
) -> Path:
    """Save a journal entry to an encrypted file.

    Returns the path to the saved file.
    """
    timestamp = datetime.now()
    filename = timestamp.strftime("%Y-%m-%dT%H-%M-%S") + ".enc"
    filepath = config.save_dir / filename

    data = {
        "timestamp": timestamp.isoformat(),
        "content": content,
    }
    plaintext = json.dumps(data, indent=2)

    encrypted = encrypt(plaintext, passphrase)
    filepath.write_bytes(encrypted)

    return filepath


def list_entries(config: Config = DEFAULT_CONFIG) -> dict[str, list[Path]]:
    """List all saved entries grouped by date.

    Returns a dict mapping date strings to lists of file paths.
    """
    entries: dict[str, list[Path]] = defaultdict(list)

    for filepath in sorted(config.save_dir.glob("*.enc")):
        # Parse date from filename
        try:
            date_str = filepath.stem.split("T")[0]
            entries[date_str].append(filepath)
        except (IndexError, ValueError):
            continue

    return dict(entries)


def load_entry(filepath: Path, passphrase: str) -> SavedEntry:
    """Load and decrypt a single entry.

    Raises InvalidToken if passphrase is wrong.
    """
    encrypted = filepath.read_bytes()
    plaintext = decrypt(encrypted, passphrase)
    data = json.loads(plaintext)

    timestamp = datetime.fromisoformat(data["timestamp"])

    # Handle both new format (content) and old format (messages)
    content = data.get("content", "")
    messages = None
    if "messages" in data:
        messages = [Message(role=m["role"], content=m["content"]) for m in data["messages"]]
        # If no content but has messages, generate content from messages for display
        if not content:
            content = "\n\n".join(
                f"**{m.role.title()}:** {m.content}" for m in messages
            )

    return SavedEntry(filepath=filepath, timestamp=timestamp, content=content, messages=messages)


def load_entries_for_date(
    date_str: str, passphrase: str, config: Config = DEFAULT_CONFIG
) -> list[SavedEntry]:
    """Load all entries for a specific date.

    Raises InvalidToken if passphrase is wrong.
    """
    entries = []
    all_entries = list_entries(config)

    if date_str not in all_entries:
        return []

    for filepath in all_entries[date_str]:
        entry = load_entry(filepath, passphrase)
        entries.append(entry)

    return entries


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

    for date_str, filepaths in all_dates.items():
        try:
            entry_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if entry_date >= cutoff:
            for filepath in filepaths:
                entries.append(load_entry(filepath, passphrase))

    entries.sort(key=lambda e: e.timestamp)
    return entries


def load_memory(passphrase: str, config: Config = DEFAULT_CONFIG) -> str | None:
    """Load the long-running memory file.

    Returns the decrypted memory text, or None if no memory file exists.
    Raises InvalidToken if passphrase is wrong.
    """
    memory_file = config.save_dir / "memory" / "long.enc"
    if not memory_file.exists():
        return None

    encrypted = memory_file.read_bytes()
    return decrypt(encrypted, passphrase)


def save_memory(content: str, passphrase: str, config: Config = DEFAULT_CONFIG) -> Path:
    """Save the long-running memory to an encrypted file.

    Creates the memory directory if needed. Returns the file path.
    """
    memory_dir = config.save_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_file = memory_dir / "long.enc"

    encrypted = encrypt(content, passphrase)
    memory_file.write_bytes(encrypted)

    return memory_file


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
