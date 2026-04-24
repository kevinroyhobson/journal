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
        self.model = config.conversation_model

    async def check_connection(self) -> bool:
        """Check that an API key is configured."""
        return bool(self.api_key)

    async def select_model(self) -> str:
        """Return the configured model name."""
        return self.model

    async def _stream(
        self, api_messages: list[dict], system_prompt: str | None = None,
        model: str | None = None, tools: list | None = None,
    ) -> AsyncIterator[str]:
        """Core streaming implementation over raw API message dicts."""
        payload: dict = {
            "model": model or self.model,
            "messages": api_messages,
            "stream": True,
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = tools

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        # Reset per-stream state
        self._content_blocks: dict[int, dict] = {}
        self._stop_reason: str | None = None
        self._input_json: dict[int, str] = {}
        self._query_yielded: set[int] = set()

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

                    event_type = data.get("type")

                    if event_type == "content_block_start":
                        index = data.get("index", 0)
                        block = data.get("content_block", {})
                        self._content_blocks[index] = dict(block)
                        if block.get("type") == "server_tool_use":
                            self._input_json[index] = ""
                            yield "\n*Searching the web...*\n\n"

                    elif event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        index = data.get("index", 0)
                        delta_type = delta.get("type")

                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                # Accumulate in content block for pause_turn
                                if index in self._content_blocks:
                                    self._content_blocks[index]["text"] = (
                                        self._content_blocks[index].get("text", "") + text
                                    )
                                yield text

                        elif delta_type == "input_json_delta":
                            partial = delta.get("partial_json", "")
                            if index in self._input_json:
                                self._input_json[index] += partial
                                # Try to extract query once JSON is complete
                                if index not in self._query_yielded:
                                    try:
                                        input_data = json.loads(self._input_json[index])
                                        query = input_data.get("query", "")
                                        if query:
                                            self._query_yielded.add(index)
                                            yield f'*Searching for: "{query}"...*\n\n'
                                            self._content_blocks[index]["input"] = input_data
                                    except json.JSONDecodeError:
                                        pass

                    elif event_type == "content_block_stop":
                        index = data.get("index", 0)
                        # Finalize server_tool_use input if not yet parsed
                        if index in self._input_json and index not in self._query_yielded:
                            try:
                                input_data = json.loads(self._input_json[index])
                                self._content_blocks[index]["input"] = input_data
                            except json.JSONDecodeError:
                                pass

                    elif event_type == "message_delta":
                        self._stop_reason = data.get("delta", {}).get("stop_reason")

    async def chat_stream(
        self, messages: list[Message], system_prompt: str | None = None,
        model: str | None = None, tools: list | None = None,
    ) -> AsyncIterator[str]:
        """Stream a chat completion via SSE."""
        api_messages = []
        for msg in messages:
            if msg.role == "system":
                continue
            api_messages.append({"role": msg.role, "content": msg.content})

        async for chunk in self._stream(api_messages, system_prompt, model, tools):
            yield chunk

    async def chat_stream_with_tools(
        self, messages: list[Message], system_prompt: str | None = None,
        model: str | None = None, tools: list | None = None,
    ) -> AsyncIterator[str]:
        """Stream with automatic pause_turn loop for server-side tools."""
        api_messages = []
        for msg in messages:
            if msg.role == "system":
                continue
            api_messages.append({"role": msg.role, "content": msg.content})

        while True:
            async for chunk in self._stream(api_messages, system_prompt, model, tools):
                yield chunk

            if self._stop_reason != "pause_turn":
                break

            # Send accumulated content blocks back as assistant message to continue
            content_blocks = [
                self._content_blocks[i]
                for i in sorted(self._content_blocks)
            ]
            api_messages.append({"role": "assistant", "content": content_blocks})
