"""Configuration management for the journal application."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "journal"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    """Application configuration."""

    # Provider selection
    provider: str = "local"  # "local" or "anthropic"

    # Ollama settings
    ollama_base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct"
    fallback_model: str = "qwen2.5:7b-instruct"

    # Anthropic settings
    anthropic_api_key: str = ""
    conversation_model: str = "claude-haiku-4-5-20251001"
    generation_model: str = "claude-opus-4-6"

    # S3 storage
    s3_bucket: str = ""

    system_prompt: str = (
        "You're helping me talk through my day. Ask follow-up questions to help me "
        "process what happened and how I'm feeling. Be curious but not pushy. Don't rush "
        "to summarize or give advice unless I ask."
    )


KNOWN_FIELDS = {
    "provider", "ollama_base_url", "model", "fallback_model",
    "anthropic_api_key", "conversation_model", "generation_model",
    "s3_bucket", "system_prompt",
}


def load_config() -> Config:
    """Load config from ~/.config/journal/config.json and environment."""
    overrides = {}

    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            for key in KNOWN_FIELDS:
                if key in data:
                    overrides[key] = data[key]
        except (json.JSONDecodeError, OSError):
            pass

    # Environment variable overrides config file for API key
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        overrides["anthropic_api_key"] = env_key

    return Config(**overrides)


DEFAULT_CONFIG = Config()
