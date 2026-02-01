"""Markdown and output rendering with Rich."""

import time
import threading
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from journal.storage import SavedEntry


class StreamingRenderer:
    """Renders streaming LLM output with live markdown updates."""

    def __init__(self, console: Console):
        self.console = console
        self.buffer = ""
        self._live: Live | None = None
        self._thinking = True
        self._start_time: float = 0
        self._timer_thread: threading.Thread | None = None
        self._stop_timer = False

    def _format_thinking(self) -> Text:
        """Format the thinking indicator with elapsed time."""
        elapsed = time.time() - self._start_time
        return Text(f"Thinking... {elapsed:.1f}s", style="dim italic")

    def _timer_loop(self):
        """Background thread to update thinking timer."""
        while not self._stop_timer and self._thinking:
            if self._live and self._thinking:
                self._live.update(self._format_thinking())
            time.sleep(0.1)

    def start(self):
        """Start the live rendering context."""
        self.buffer = ""
        self._thinking = True
        self._start_time = time.time()
        self._think_time = 0.0
        self._stop_timer = False

        self._live = Live(
            self._format_thinking(),
            console=self.console,
            refresh_per_second=10,
            transient=True,
        )
        self._live.start()

        # Start timer thread
        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()

    def update(self, chunk: str):
        """Add a chunk to the buffer and update display."""
        if self._thinking:
            self._thinking = False
            self._stop_timer = True
            self._think_time = time.time() - self._start_time

        self.buffer += chunk
        if self._live:
            self._live.update(Markdown(self.buffer))

    def finish(self):
        """Finish streaming and render final output."""
        self._stop_timer = True
        self._thinking = False

        if self._live:
            self._live.stop()
            self._live = None

        # Print thinking time
        if self._think_time > 0:
            self.console.print(f"[dim italic]Thought for {self._think_time:.1f}s[/dim italic]")

        # Print final rendered markdown
        if self.buffer:
            self.console.print(Markdown(self.buffer))
            self.console.print()

    def get_content(self) -> str:
        """Get the accumulated content."""
        return self.buffer


def print_welcome(console: Console, model: str = ""):
    """Print welcome message."""
    subtitle = f"{model}-Powered Journaling" if model else "Journaling"
    console.print()
    console.print(
        Panel(
            f"[bold #ffff00]Journal[/bold #ffff00] - {subtitle}\n\n"
            "Commands: /help, /write, /read, /memory, /clear, /exit\n"
            "Multi-line: [bold]Shift+Enter[/bold] or [bold]Alt+Enter[/bold] for newline",
            title="Welcome",
            border_style="#ffff00",
        )
    )
    console.print()


def print_help(console: Console):
    """Print help message."""
    help_text = """
**Available Commands:**

- `/help` - Show this help message
- `/write` - Save current conversation (encrypted)
- `/read` - List and view saved entries
- `/memory` - View and edit long-running memory
- `/clear` - Clear conversation history
- `/exit` or `/quit` - Exit the application

**Input:**

- Type your message and press **Enter** to send
- For multi-line input, press **Shift+Enter** or **Alt+Enter** for newline
- Use **Up/Down** arrows to navigate history
"""
    console.print(Markdown(help_text))
    console.print()


def print_error(console: Console, message: str):
    """Print an error message."""
    console.print(f"[bold red]Error:[/bold red] {message}")
    console.print()


def print_success(console: Console, message: str):
    """Print a success message."""
    console.print(f"[bold green]Success:[/bold green] {message}")
    console.print()


def print_info(console: Console, message: str):
    """Print an info message."""
    console.print(f"[bold blue]Info:[/bold blue] {message}")
    console.print()


def print_saved_entries(console: Console, entries: dict[str, list[Path]]):
    """Print list of saved entries grouped by date."""
    if not entries:
        console.print("[dim]No saved entries found.[/dim]")
        console.print()
        return

    console.print("[bold]Saved Entries:[/bold]")
    console.print()

    for date_str in sorted(entries.keys(), reverse=True):
        count = len(entries[date_str])
        console.print(f"  [#ffff00]{date_str}[/#ffff00] ({count} {'entry' if count == 1 else 'entries'})")

    console.print()
    console.print("[dim]Enter a date (YYYY-MM-DD) to view entries, or press Enter to cancel.[/dim]")
    console.print()


def print_entry(console: Console, entry: SavedEntry, index: int):
    """Print a single saved entry."""
    time_str = entry.timestamp.strftime("%H:%M:%S")

    console.print(f"[bold #ffff00]Entry {index + 1}[/bold #ffff00] - {time_str}")
    console.print("-" * 40)
    console.print()

    # Display the journal entry content
    console.print(Markdown(entry.content))

    console.print()
