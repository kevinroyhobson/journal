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
    anthropic_model: str = "claude-sonnet-4-5"

    save_dir: Path = Path("./save")
    system_prompt: str = (
        "You're helping me talk through my day. Ask follow-up questions to help me "
        "process what happened and how I'm feeling. Be curious but not pushy. Don't rush "
        "to summarize or give advice unless I ask."
    )

    def __post_init__(self):
        self.save_dir = Path(self.save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)


KNOWN_FIELDS = {
    "provider", "ollama_base_url", "model", "fallback_model",
    "anthropic_api_key", "anthropic_model", "save_dir", "system_prompt",
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
