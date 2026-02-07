"""Context loading and system prompt assembly."""

from datetime import date, datetime

from cryptography.fernet import InvalidToken
from rich.console import Console

from journal.config import Config
from journal.render import print_error, print_info
from journal.storage import (
    SavedEntry,
    list_monthly_memories,
    list_weekly_memories,
    load_memory,
    load_recent_entries,
)


class Context:
    """Holds session context loaded at startup and assembles the system prompt."""

    def __init__(self):
        self.memory: str | None = None
        self.recent_entries: list[SavedEntry] = []
        self.monthly_memories: list[tuple[str, str]] = []
        self.weekly_memories: list[tuple[date, str]] = []

    def load(self, passphrase: str, config: Config, console: Console):
        """Load all context at startup. Prints status/errors to console."""
        # Long-running memory
        try:
            self.memory = load_memory(passphrase, config)
            if self.memory:
                print_info(console, "Memory loaded.")
        except InvalidToken:
            print_error(
                console,
                "Failed to decrypt memory file. "
                "It may have been saved with a different passphrase.",
            )
        except Exception as e:
            print_error(console, f"Failed to load memory: {e}")

        # Recent journal entries
        try:
            self.recent_entries = load_recent_entries(passphrase, config)
            if self.recent_entries:
                print_info(
                    console,
                    f"Loaded {len(self.recent_entries)} entries from the past week.",
                )
        except InvalidToken:
            print_error(
                console,
                "Failed to decrypt recent entries. "
                "They may have been saved with a different passphrase.",
            )
        except Exception as e:
            print_error(console, f"Failed to load recent entries: {e}")

        # Monthly memories (last 3 months)
        try:
            all_monthly = list_monthly_memories(passphrase, config)
            self.monthly_memories = all_monthly[-3:]
            if self.monthly_memories:
                print_info(
                    console,
                    f"Loaded {len(self.monthly_memories)} monthly memories.",
                )
        except InvalidToken:
            print_error(console, "Failed to decrypt monthly memories.")
        except Exception as e:
            print_error(console, f"Failed to load monthly memories: {e}")

        # Weekly memories (current month)
        try:
            all_weekly = list_weekly_memories(passphrase, config)
            current_month = date.today().strftime("%Y-%m")
            self.weekly_memories = [
                (w, c) for w, c in all_weekly if w.isoformat().startswith(current_month)
            ]
            if self.weekly_memories:
                print_info(
                    console,
                    f"Loaded {len(self.weekly_memories)} weekly memories.",
                )
        except InvalidToken:
            print_error(console, "Failed to decrypt weekly memories.")
        except Exception as e:
            print_error(console, f"Failed to load weekly memories: {e}")

    def build_system_prompt(self, base_prompt: str) -> str:
        """Assemble the full system prompt from base + memory + summaries + entries."""
        now = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
        parts = [base_prompt, f"The current date and time is {now}."]

        if self.memory:
            parts.append(
                "Here is what you know about me from previous sessions:\n"
                f"{self.memory}"
            )

        if self.monthly_memories:
            memories_text = []
            for month_str, content in self.monthly_memories:
                try:
                    d = datetime.strptime(month_str, "%Y-%m")
                    label = d.strftime("%B %Y")
                except ValueError:
                    label = month_str
                memories_text.append(f"--- {label} ---\n{content}")
            parts.append(
                "Here are my monthly summaries from recent months:\n\n"
                + "\n\n".join(memories_text)
            )

        if self.weekly_memories:
            memories_text = []
            for week_start, content in self.weekly_memories:
                label = f"Week of {week_start.strftime('%B %d, %Y')}"
                memories_text.append(f"--- {label} ---\n{content}")
            parts.append(
                "Here are my weekly summaries from this month:\n\n"
                + "\n\n".join(memories_text)
            )

        if self.recent_entries:
            entries_text = []
            for entry in self.recent_entries:
                entries_text.append(f"--- {entry.date_label} ---\n{entry.content}")
            parts.append(
                "Here are my journal entries from the past week:\n\n"
                + "\n\n".join(entries_text)
            )

        return "\n\n".join(parts)
