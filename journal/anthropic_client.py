"""Anthropic Messages API client with SSE streaming support."""

import json
from collections.abc import AsyncIterator

import httpx

from journal.client import Message
from journal.config import Config

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


class AnthropicClient:
    """Async client for Anthropic Messages API using httpx SSE."""

    def __init__(self, config: Config):
        self.config = config
        self.api_key = config.anthropic_api_key
        self.model = config.anthropic_model

    async def check_connection(self) -> bool:
        """Check that an API key is configured."""
        return bool(self.api_key)

    async def select_model(self) -> str:
        """Return the configured model name."""
        return self.model

    async def chat_stream(
        self, messages: list[Message], system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        """Stream a chat completion via SSE."""
        api_messages = []
        for msg in messages:
            if msg.role == "system":
                continue
            api_messages.append({"role": msg.role, "content": msg.content})

        payload: dict = {
            "model": self.model,
            "messages": api_messages,
            "stream": True,
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        if system_prompt:
            payload["system"] = system_prompt

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
                timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "content_block_delta":
                        text = data.get("delta", {}).get("text", "")
                        if text:
                            yield text
