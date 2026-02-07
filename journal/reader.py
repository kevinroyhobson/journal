"""Browse journal entries, conversations, and memories."""

from datetime import date, datetime

from cryptography.fernet import InvalidToken
from prompt_toolkit import PromptSession
from rich.console import Console

from journal.config import Config
from journal.render import (
    print_conversation,
    print_entry,
    print_error,
    print_memory_summary,
    print_read_menu,
    print_saved_entries,
)
from journal.storage import (
    list_entries,
    list_monthly_memories,
    list_raw_conversations,
    list_weekly_memories,
    load_entries_for_date,
    load_raw_conversation,
)


class Reader:
    """Read-only browsing of entries, conversations, and memories."""

    def __init__(
        self,
        console: Console,
        simple_session: PromptSession,
        passphrase: str,
        config: Config,
    ):
        self.console = console
        self.simple_session = simple_session
        self.passphrase = passphrase
        self.config = config
        self.memory: str | None = None

    async def handle_read(self):
        """Show read menu and dispatch to the chosen sub-command."""
        print_read_menu(self.console)

        try:
            choice = await self.simple_session.prompt_async("Choice: ", is_password=False)
            choice = choice.strip()
        except (KeyboardInterrupt, EOFError):
            return

        if choice == "1":
            await self._read_entries()
        elif choice == "2":
            await self._read_raw_conversations()
        elif choice == "3":
            await self._read_monthly_memories()
        elif choice == "4":
            await self._read_weekly_memories()
        elif choice == "5":
            await self._read_long_term_memory()
        else:
            print_error(self.console, "Invalid choice.")

    async def _read_entries(self):
        """Browse journal entries by date."""
        entries = list_entries(self.config)
        print_saved_entries(self.console, entries)

        if not entries:
            return

        try:
            date_input = await self.simple_session.prompt_async("Date: ", is_password=False)
            date_input = date_input.strip()
        except (KeyboardInterrupt, EOFError):
            return

        if not date_input:
            return

        if date_input not in entries:
            print_error(self.console, f"No entries found for {date_input}")
            return

        try:
            loaded = load_entries_for_date(date_input, self.passphrase, self.config)
            with self.console.pager(styles=True):
                for i, entry in enumerate(loaded):
                    print_entry(self.console, entry, i)
        except InvalidToken:
            print_error(
                self.console,
                "Failed to decrypt entries. "
                "These may have been saved with a different passphrase.",
            )
        except Exception as e:
            print_error(self.console, f"Failed to load entries: {e}")

    async def _read_raw_conversations(self):
        """Browse raw conversation transcripts by date."""
        convos = list_raw_conversations(self.config)
        print_saved_entries(self.console, convos)

        if not convos:
            return

        try:
            date_input = await self.simple_session.prompt_async("Date: ", is_password=False)
            date_input = date_input.strip()
        except (KeyboardInterrupt, EOFError):
            return

        if not date_input:
            return

        if date_input not in convos:
            print_error(self.console, f"No conversations found for {date_input}")
            return

        try:
            with self.console.pager(styles=True):
                for i, key in enumerate(convos[date_input]):
                    ts, messages = load_raw_conversation(key, self.passphrase, self.config)
                    time_str = ts.strftime("%H:%M:%S")
                    print_conversation(self.console, messages, i, time_str)
        except InvalidToken:
            print_error(
                self.console,
                "Failed to decrypt conversations. "
                "These may have been saved with a different passphrase.",
            )
        except Exception as e:
            print_error(self.console, f"Failed to load conversations: {e}")

    async def _read_monthly_memories(self):
        """Browse monthly memory summaries."""
        try:
            memories = list_monthly_memories(self.passphrase, self.config)
        except InvalidToken:
            print_error(self.console, "Failed to decrypt monthly memories.")
            return
        except Exception as e:
            print_error(self.console, f"Failed to load monthly memories: {e}")
            return

        if not memories:
            self.console.print("[dim]No monthly memories found.[/dim]")
            self.console.print()
            return

        self.console.print("[bold]Monthly Memories:[/bold]")
        self.console.print()
        for month_str, _ in memories:
            self.console.print(f"  [#ffff00]{month_str}[/#ffff00]")
        self.console.print()
        self.console.print("[dim]Enter a month (YYYY-MM) to view, or press Enter to cancel.[/dim]")
        self.console.print()

        try:
            month_input = await self.simple_session.prompt_async("Month: ", is_password=False)
            month_input = month_input.strip()
        except (KeyboardInterrupt, EOFError):
            return

        if not month_input:
            return

        match = [c for m, c in memories if m == month_input]
        if not match:
            print_error(self.console, f"No monthly memory found for {month_input}")
            return

        try:
            d = datetime.strptime(month_input, "%Y-%m")
            label = d.strftime("%B %Y")
        except ValueError:
            label = month_input

        with self.console.pager(styles=True):
            print_memory_summary(self.console, label, match[0])

    async def _read_weekly_memories(self):
        """Browse weekly memory summaries."""
        try:
            memories = list_weekly_memories(self.passphrase, self.config)
        except InvalidToken:
            print_error(self.console, "Failed to decrypt weekly memories.")
            return
        except Exception as e:
            print_error(self.console, f"Failed to load weekly memories: {e}")
            return

        if not memories:
            self.console.print("[dim]No weekly memories found.[/dim]")
            self.console.print()
            return

        self.console.print("[bold]Weekly Memories:[/bold]")
        self.console.print()
        for week_date, _ in memories:
            label = f"Week of {week_date.strftime('%B %d, %Y')}"
            self.console.print(f"  [#ffff00]{week_date.isoformat()}[/#ffff00]  {label}")
        self.console.print()
        self.console.print("[dim]Enter a week start date (YYYY-MM-DD) to view, or press Enter to cancel.[/dim]")
        self.console.print()

        try:
            week_input = await self.simple_session.prompt_async("Week: ", is_password=False)
            week_input = week_input.strip()
        except (KeyboardInterrupt, EOFError):
            return

        if not week_input:
            return

        match = [c for w, c in memories if w.isoformat() == week_input]
        if not match:
            print_error(self.console, f"No weekly memory found for {week_input}")
            return

        try:
            w = date.fromisoformat(week_input)
            label = f"Week of {w.strftime('%B %d, %Y')}"
        except ValueError:
            label = week_input

        with self.console.pager(styles=True):
            print_memory_summary(self.console, label, match[0])

    async def _read_long_term_memory(self):
        """Display the long-term memory."""
        if not self.memory:
            self.console.print("[dim]No long-term memory yet.[/dim]")
            self.console.print()
            return

        with self.console.pager(styles=True):
            print_memory_summary(self.console, "Long-Term Memory", self.memory)
