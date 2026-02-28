"""Weekly and monthly memory compaction."""

from datetime import date, datetime

from cryptography.fernet import InvalidToken
from rich.console import Console

from journal.client import Message
from journal.config import Config
from journal.render import StreamingRenderer, print_error, print_info, print_success
from journal.storage import (
    _week_start,
    load_compact_metadata,
    load_entries_for_month,
    load_entries_for_week,
    load_entries_since,
    save_compact_metadata,
    save_monthly_memory,
    save_weekly_memory,
)


async def compact(client, console: Console, passphrase: str, config: Config):
    """Generate weekly and monthly memory summaries from new entries."""
    print_info(console, "Checking for entries to compact...")

    # Load compact metadata
    try:
        meta = load_compact_metadata(passphrase, config)
    except InvalidToken:
        print_error(console, "Failed to decrypt compact metadata.")
        return
    except Exception as e:
        print_error(console, f"Failed to load compact metadata: {e}")
        return

    last_compact_str = meta.get("last_compact")
    if last_compact_str:
        last_compact = datetime.fromisoformat(last_compact_str)
    else:
        last_compact = datetime.min

    # Find entries since last compact
    try:
        new_entries = load_entries_since(last_compact, passphrase, config)
    except InvalidToken:
        print_error(console, "Failed to decrypt entries.")
        return
    except Exception as e:
        print_error(console, f"Failed to load entries: {e}")
        return

    if not new_entries:
        print_info(console, "No new entries to compact.")
        return

    # Determine affected weeks and months
    affected_weeks: set[date] = set()
    affected_months: set[str] = set()
    for entry in new_entries:
        d = entry.timestamp.date()
        affected_weeks.add(_week_start(d))
        affected_months.add(d.strftime("%Y-%m"))

    print_info(
        console,
        f"Found {len(new_entries)} new entries across "
        f"{len(affected_weeks)} weeks and {len(affected_months)} months.",
    )

    # Generate weekly summaries
    for week_start_date in sorted(affected_weeks):
        week_label = week_start_date.strftime("%B %d, %Y")
        print_info(console, f"Compacting week of {week_label}...")

        try:
            week_entries = load_entries_for_week(
                week_start_date, passphrase, config
            )
        except Exception as e:
            print_error(console, f"Failed to load week entries: {e}")
            continue

        if not week_entries:
            continue

        entries_text = "\n\n".join(
            f"--- {e.date_label} ---\n{e.content}"
            for e in week_entries
        )

        prompt = (
            f"Here are all of my journal entries from the week of {week_label}:\n\n"
            f"{entries_text}\n\n"
            "Summarize this week. Focus on key events, emotional themes, and what "
            "I was working through. Keep it concise — a few paragraphs."
        )

        renderer = StreamingRenderer(console)
        renderer.start()
        try:
            messages = [Message(role="user", content=prompt)]
            async for chunk in client.chat_stream(
                messages, system_prompt=None,
                model=config.generation_model,
            ):
                renderer.update(chunk)
        except Exception as e:
            renderer.finish()
            print_error(console, f"Failed to generate weekly summary: {e}")
            continue
        renderer.finish()

        summary = renderer.get_content().strip()
        try:
            save_weekly_memory(week_start_date, summary, passphrase, config)
            print_success(console, f"Weekly summary saved for week of {week_label}.")
        except Exception as e:
            print_error(console, f"Failed to save weekly summary: {e}")

    # Generate monthly summaries
    for month_str in sorted(affected_months):
        try:
            d = datetime.strptime(month_str, "%Y-%m")
            month_label = d.strftime("%B %Y")
        except ValueError:
            month_label = month_str

        print_info(console, f"Compacting {month_label}...")

        try:
            month_entries = load_entries_for_month(
                month_str, passphrase, config
            )
        except Exception as e:
            print_error(console, f"Failed to load month entries: {e}")
            continue

        if not month_entries:
            continue

        entries_text = "\n\n".join(
            f"--- {e.date_label} ---\n{e.content}"
            for e in month_entries
        )

        prompt = (
            f"Here are all of my journal entries from {month_label}:\n\n"
            f"{entries_text}\n\n"
            "Summarize this month at a high level. Focus on major themes, significant "
            "events, and overall trajectory. This should be briefer and higher-level "
            "than a weekly summary. Keep it to 1-2 paragraphs."
        )

        renderer = StreamingRenderer(console)
        renderer.start()
        try:
            messages = [Message(role="user", content=prompt)]
            async for chunk in client.chat_stream(
                messages, system_prompt=None,
                model=config.generation_model,
            ):
                renderer.update(chunk)
        except Exception as e:
            renderer.finish()
            print_error(console, f"Failed to generate monthly summary: {e}")
            continue
        renderer.finish()

        summary = renderer.get_content().strip()
        try:
            save_monthly_memory(month_str, summary, passphrase, config)
            print_success(console, f"Monthly summary saved for {month_label}.")
        except Exception as e:
            print_error(console, f"Failed to save monthly summary: {e}")

    # Save updated compact metadata
    try:
        save_compact_metadata(
            {"last_compact": datetime.now().isoformat()},
            passphrase,
            config,
        )
        print_success(console, "Compact complete.")
    except Exception as e:
        print_error(console, f"Failed to save compact metadata: {e}")
