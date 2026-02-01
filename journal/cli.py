"""Main TUI loop for the journal application."""

import argparse
import asyncio
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from cryptography.fernet import InvalidToken
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.markdown import Markdown

from journal.anthropic_client import AnthropicClient
from journal.client import Message, OllamaClient
from journal.config import DEFAULT_CONFIG, Config, load_config
from journal.render import (
    StreamingRenderer,
    print_entry,
    print_error,
    print_help,
    print_info,
    print_saved_entries,
    print_success,
    print_welcome,
)
from journal.storage import (
    SavedEntry,
    list_entries,
    load_entries_for_date,
    load_memory,
    load_recent_entries,
    save_journal_entry,
    save_memory,
)


class JournalApp:
    """Main journal application."""

    def __init__(self, config: Config = DEFAULT_CONFIG):
        self.config = config
        self.console = Console()
        if config.provider == "anthropic":
            self.client = AnthropicClient(config)
        else:
            self.client = OllamaClient(config)
        self.messages: list[Message] = []
        self.passphrase: str = ""
        self.memory: str | None = None
        self.recent_entries: list[SavedEntry] = []

        # Set up key bindings for multi-line input
        self.bindings = KeyBindings()

        # Enter submits
        @self.bindings.add("enter")
        def _(event):
            """Submit on Enter."""
            event.current_buffer.validate_and_handle()

        # Alt+Enter inserts newline
        @self.bindings.add("escape", "enter")
        def _(event):
            """Insert newline on Alt+Enter."""
            event.current_buffer.insert_text("\n")

        # Shift+Enter for newline (kitty keyboard protocol — bonus for supported terminals)
        @self.bindings.add("escape", "[", "1", "3", ";", "2", "u")
        def _(event):
            """Insert newline on Shift+Enter (kitty protocol)."""
            event.current_buffer.insert_text("\n")

        self.session: PromptSession = PromptSession(
            history=InMemoryHistory(),
            key_bindings=self.bindings,
            multiline=True,  # Enable multiline for paste support
            enable_open_in_editor=True,
        )

        # Separate session for simple prompts (no multiline)
        self.simple_session: PromptSession = PromptSession()

    def _get_prompt(self) -> HTML:
        """Get the formatted prompt with top line and chevron."""
        width = self.console.width or 80
        line = "─" * (width - 2)
        return HTML(f'<style fg="#ffff00">{line}</style>\n<style fg="#ffff00"><b>❯</b></style> ')

    def _get_bottom_toolbar(self) -> HTML:
        """Get the bottom toolbar (line below input)."""
        width = self.console.width or 80
        line = "─" * (width - 2)
        return HTML(f'<style fg="#ffff00">{line}</style>')

    @property
    def _system_prompt(self) -> str:
        """System prompt augmented with memory and recent entries."""
        now = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
        parts = [self.config.system_prompt, f"The current date and time is {now}."]

        if self.memory:
            parts.append(
                "Here is what you know about me from previous sessions:\n"
                f"{self.memory}"
            )

        if self.recent_entries:
            entries_text = []
            for entry in self.recent_entries:
                date_label = entry.timestamp.strftime("%B %d, %Y")
                entries_text.append(f"--- {date_label} ---\n{entry.content}")
            parts.append(
                "Here are my journal entries from the past week:\n\n"
                + "\n\n".join(entries_text)
            )

        return "\n\n".join(parts)

    async def get_passphrase(self) -> str:
        """Prompt for encryption passphrase."""
        self.console.print()
        self.console.print("[bold]Enter encryption passphrase:[/bold]")
        passphrase = await self.simple_session.prompt_async("Passphrase: ", is_password=True)
        self.console.print()
        return passphrase

    async def check_server(self) -> bool:
        """Check if the LLM provider is reachable."""
        if not await self.client.check_connection():
            if self.config.provider == "anthropic":
                print_error(
                    self.console,
                    "Anthropic API key not configured.\n"
                    "Set ANTHROPIC_API_KEY environment variable or add "
                    '"anthropic_api_key" to ~/.config/journal/config.json',
                )
            else:
                print_error(
                    self.console,
                    "Cannot connect to Ollama server at "
                    f"{self.config.ollama_base_url}\n"
                    "Please start Ollama with: ollama serve",
                )
            return False
        return True

    async def get_model_info(self) -> str | None:
        """Get and display model info."""
        try:
            model = await self.client.select_model()
            print_info(self.console, f"Using model: {model}")
            return model
        except RuntimeError as e:
            print_error(self.console, str(e))
            return None

    async def opener(self):
        """Generate an opening message from the model."""
        prompt = [Message(role="user", content="Start the conversation.")]
        renderer = StreamingRenderer(self.console)
        renderer.start()

        try:
            async for chunk in self.client.chat_stream(
                prompt, system_prompt=self._system_prompt
            ):
                renderer.update(chunk)
        except Exception as e:
            renderer.finish()
            self.console.print(f"[bold red]Chat error:[/bold red] {type(e).__name__}: {e}")
            self.console.print(f"[dim]{traceback.format_exc()}[/dim]")
            return

        renderer.finish()
        self.messages.append(Message(role="assistant", content=renderer.get_content()))

    async def chat(self, user_input: str):
        """Send a message and stream the response."""
        # Add user message to history
        self.messages.append(Message(role="user", content=user_input))

        # Stream the response
        renderer = StreamingRenderer(self.console)
        renderer.start()

        try:
            async for chunk in self.client.chat_stream(
                self.messages, system_prompt=self._system_prompt
            ):
                renderer.update(chunk)
        except Exception as e:
            renderer.finish()
            self.console.print(f"[bold red]Chat error:[/bold red] {type(e).__name__}: {e}")
            self.console.print(f"[dim]{traceback.format_exc()}[/dim]")
            # Remove the failed user message
            self.messages.pop()
            return

        renderer.finish()

        # Add assistant response to history
        self.messages.append(Message(role="assistant", content=renderer.get_content()))

    async def generate_journal_entry(self, edit_request: str | None = None, current_draft: str | None = None) -> str:
        """Generate a journal entry from the conversation."""
        now = datetime.now()
        today = now.strftime("%B %d, %Y, ") + now.strftime("%I:%M%p").lstrip("0").lower()

        conversation_text = "\n".join(
            f"{m.role.upper()}: {m.content}" for m in self.messages
        )

        if edit_request and current_draft:
            prompt = (
                f"Current draft:\n{current_draft}\n\n"
                f"Edit request: {edit_request}\n\n"
                "Revise the journal entry according to the request. "
                "Write ONLY the revised journal entry, nothing else."
            )
        else:
            prompt = (
                f"Today's date is {today}.\n\n"
                f"Write a journal entry based on this conversation. Include what "
                f"happened, but focus on what I seemed to be processing or working "
                f"through. Note any tensions or unresolved feelings.\n\n"
                f"Conversation:\n{conversation_text}"
            )

        # Generate with streaming feedback
        renderer = StreamingRenderer(self.console)
        renderer.start()

        messages = [Message(role="user", content=prompt)]
        try:
            async for chunk in self.client.chat_stream(messages, system_prompt=None):
                renderer.update(chunk)
        finally:
            renderer.finish()

        return renderer.get_content().strip()

    async def _update_memory(self):
        """Ask the LLM if anything from this conversation should be remembered."""
        conversation_text = "\n".join(
            f"{m.role.upper()}: {m.content}" for m in self.messages
        )

        current = self.memory or "(empty — first session)"

        prompt = (
            "You maintain a long-running memory about the person you journal with. "
            "This memory is loaded at the start of every session so you can reference "
            "it and already know important context about their life.\n\n"
            f"Current memory:\n{current}\n\n"
            f"Today's conversation:\n{conversation_text}\n\n"
            "Based on this conversation, write an updated version of the memory. "
            "Include everything from the current memory that's still relevant, "
            "and add any new important context (people, ongoing situations, "
            "recurring themes, important events). Keep it concise — factual "
            "notes, not prose. If nothing new is worth remembering, respond "
            "with exactly: NO_CHANGES"
        )

        print_info(self.console, "Updating memory...")

        messages = [Message(role="user", content=prompt)]
        renderer = StreamingRenderer(self.console)
        renderer.start()

        try:
            async for chunk in self.client.chat_stream(messages, system_prompt=None):
                renderer.update(chunk)
        except Exception as e:
            renderer.finish()
            print_error(self.console, f"Failed to update memory: {e}")
            return

        renderer.finish()
        result = renderer.get_content().strip()

        if result == "NO_CHANGES":
            print_info(self.console, "Memory unchanged.")
            return

        try:
            save_memory(result, self.passphrase, self.config)
            self.memory = result
            print_success(self.console, "Memory updated.")
        except Exception as e:
            print_error(self.console, f"Failed to save memory: {e}")

    async def _revise_memory(self, edit_request: str, current: str) -> str:
        """Ask the LLM to revise the memory based on an edit request."""
        prompt = (
            f"Current memory:\n{current}\n\n"
            f"Edit request: {edit_request}\n\n"
            "Revise the memory according to the request. "
            "Write ONLY the revised memory, nothing else."
        )

        renderer = StreamingRenderer(self.console)
        renderer.start()

        messages = [Message(role="user", content=prompt)]
        try:
            async for chunk in self.client.chat_stream(messages, system_prompt=None):
                renderer.update(chunk)
        finally:
            renderer.finish()

        return renderer.get_content().strip()

    async def handle_memory(self):
        """Handle /memory command - view and edit long-running memory."""
        current = self.memory or ""

        if not current:
            print_info(self.console, "No memory yet.")
            return

        # Interactive editing loop
        while True:
            with self.console.pager(styles=True):
                self.console.print()
                self.console.print("[bold cyan]--- Memory ---[/bold cyan]")
                self.console.print()
                self.console.print(Markdown(current))
                self.console.print()
                self.console.print("[bold cyan]--------------[/bold cyan]")

            self.console.print(
                "[dim]Options: Type edits to request changes, 'save' to save, 'cancel' to abort[/dim]"
            )

            try:
                response = await self.simple_session.prompt_async("Action: ", is_password=False)
                response = response.strip()
            except (KeyboardInterrupt, EOFError):
                print_info(self.console, "Cancelled.")
                return

            if not response or response.lower() == "cancel":
                print_info(self.console, "Cancelled.")
                return

            if response.lower() == "save":
                break

            # User wants edits - revise with their feedback
            try:
                current = await self._revise_memory(edit_request=response, current=current)
            except Exception as e:
                print_error(self.console, f"Failed to revise: {e}")
                continue

        # Save
        try:
            save_memory(current, self.passphrase, self.config)
            self.memory = current
            print_success(self.console, "Memory saved.")
        except Exception as e:
            print_error(self.console, f"Failed to save memory: {e}")

    async def handle_write(self):
        """Handle /write command - interactive journal entry creation."""
        if not self.messages:
            print_error(self.console, "No conversation to save.")
            return

        self.console.print()

        try:
            draft = await self.generate_journal_entry()
        except Exception as e:
            print_error(self.console, f"Failed to generate entry: {e}")
            return

        # Interactive editing loop
        while True:
            self.console.print()
            self.console.print("[bold cyan]--- Draft Journal Entry ---[/bold cyan]")
            self.console.print()
            self.console.print(Markdown(draft))
            self.console.print()
            self.console.print("[bold cyan]---------------------------[/bold cyan]")
            self.console.print()

            self.console.print(
                "[dim]Options: Type edits to request changes, 'save' to save, 'cancel' to abort[/dim]"
            )

            try:
                response = await self.simple_session.prompt_async("Action: ", is_password=False)
                response = response.strip()
            except (KeyboardInterrupt, EOFError):
                print_info(self.console, "Cancelled.")
                return

            if not response or response.lower() == "cancel":
                print_info(self.console, "Cancelled.")
                return

            if response.lower() == "save":
                break

            # User wants edits - regenerate with their feedback
            try:
                draft = await self.generate_journal_entry(edit_request=response, current_draft=draft)
            except Exception as e:
                print_error(self.console, f"Failed to revise: {e}")
                continue

        # Save the final entry
        try:
            filepath = save_journal_entry(draft, self.passphrase, self.config)
            print_success(self.console, f"Journal entry saved to {filepath}")
        except Exception as e:
            print_error(self.console, f"Failed to save: {e}")
            return

        # Update long-running memory
        await self._update_memory()

    async def handle_read(self):
        """Handle /read command - list and view saved entries."""
        entries = list_entries(self.config)
        print_saved_entries(self.console, entries)

        if not entries:
            return

        # Prompt for date selection
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

        # Load and display entries for that date
        try:
            loaded = load_entries_for_date(date_input, self.passphrase, self.config)
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

    def handle_clear(self):
        """Handle /clear command - clear conversation."""
        self.messages.clear()
        print_success(self.console, "Conversation cleared.")

    def handle_dump(self):
        """Handle /dump command - dump conversation to temp file for reload."""
        dump_file = Path("/tmp/journal_conversation.json")
        data = [{"role": m.role, "content": m.content} for m in self.messages]
        dump_file.write_text(json.dumps(data, indent=2))
        print_success(self.console, f"Conversation dumped to {dump_file}")
        print_info(self.console, "Restart journal and use /load to restore.")

    def handle_load(self):
        """Handle /load command - load conversation from temp file."""
        dump_file = Path("/tmp/journal_conversation.json")
        if not dump_file.exists():
            print_error(self.console, "No dump file found. Use /dump first.")
            return
        data = json.loads(dump_file.read_text())
        self.messages = [Message(role=m["role"], content=m["content"]) for m in data]
        print_success(self.console, f"Loaded {len(self.messages)} messages.")

    async def handle_command(self, command: str) -> bool:
        """Handle a slash command. Returns True if should exit."""
        cmd = command.lower().strip()

        if cmd in ("/exit", "/quit"):
            return True
        elif cmd == "/help":
            print_help(self.console)
        elif cmd == "/write":
            await self.handle_write()
        elif cmd == "/read":
            await self.handle_read()
        elif cmd == "/memory":
            await self.handle_memory()
        elif cmd == "/clear":
            self.handle_clear()
        elif cmd == "/dump":
            self.handle_dump()
        elif cmd == "/load":
            self.handle_load()
        else:
            print_error(self.console, f"Unknown command: {command}")
            print_info(self.console, "Type /help for available commands.")

        return False

    async def run(self):
        """Main application loop."""
        model_name = (
            self.config.anthropic_model
            if self.config.provider == "anthropic"
            else self.config.model
        )
        print_welcome(self.console, model=model_name)

        # Get passphrase
        self.passphrase = await self.get_passphrase()
        if not self.passphrase:
            print_error(self.console, "Passphrase is required.")
            return

        # Load long-running memory
        try:
            self.memory = load_memory(self.passphrase, self.config)
            if self.memory:
                print_info(self.console, "Memory loaded.")
        except InvalidToken:
            print_error(
                self.console,
                "Failed to decrypt memory file. "
                "It may have been saved with a different passphrase.",
            )
        except Exception as e:
            print_error(self.console, f"Failed to load memory: {e}")

        # Load recent journal entries
        try:
            self.recent_entries = load_recent_entries(self.passphrase, self.config)
            if self.recent_entries:
                print_info(
                    self.console,
                    f"Loaded {len(self.recent_entries)} entries from the past week.",
                )
        except InvalidToken:
            print_error(
                self.console,
                "Failed to decrypt recent entries. "
                "They may have been saved with a different passphrase.",
            )
        except Exception as e:
            print_error(self.console, f"Failed to load recent entries: {e}")

        # Check server connection
        if not await self.check_server():
            return

        # Get model info
        if not await self.get_model_info():
            return

        # Kick off with a model-generated opener
        await self.opener()

        # Main input loop
        while True:
            try:
                user_input = await self.session.prompt_async(self._get_prompt())
                # Print closing line after input
                width = self.console.width or 80
                self.console.print(f"[#ffff00]{'─' * (width - 2)}[/#ffff00]")

                user_input = user_input.strip()

                if not user_input:
                    continue

                # Handle slash commands
                if user_input.startswith("/"):
                    if await self.handle_command(user_input):
                        break
                    continue

                # Regular chat
                await self.chat(user_input)

            except KeyboardInterrupt:
                self.console.print()
                continue
            except EOFError:
                break

        self.console.print()
        self.console.print("[dim]Goodbye![/dim]")


def main():
    """Entry point for the application."""
    parser = argparse.ArgumentParser(
        description="Journal - LLM-powered journaling TUI",
    )
    parser.add_argument(
        "-p", "--provider",
        choices=["local", "anthropic"],
        default=None,
        help="LLM provider: 'local' (Ollama) or 'anthropic' (default: local)",
    )
    parser.add_argument(
        "-m", "--model",
        default=None,
        help="Model name (default: provider-specific)",
    )
    args = parser.parse_args()

    config = load_config()

    if args.provider:
        config.provider = args.provider
    if args.model:
        if config.provider == "anthropic":
            config.anthropic_model = args.model
        else:
            config.model = args.model

    try:
        app = JournalApp(config=config)
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
