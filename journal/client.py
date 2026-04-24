"""Ollama API client with streaming support."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from journal.config import Config, DEFAULT_CONFIG


@dataclass
class Message:
    """A chat message."""

    role: str  # "user", "assistant", or "system"
    content: str


class OllamaClient:
    """Async client for Ollama API."""

    def __init__(self, config: Config = DEFAULT_CONFIG):
        self.config = config
        self.base_url = config.ollama_base_url
        self._model: str | None = None

    async def check_connection(self) -> bool:
        """Check if Ollama server is running."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
                return response.status_code == 200
        except httpx.RequestError:
            return False

    async def get_available_models(self) -> list[str]:
        """Get list of available models."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/api/tags", timeout=10.0)
                response.raise_for_status()
                data = response.json()
                return [model["name"] for model in data.get("models", [])]
        except httpx.RequestError:
            return []

    async def select_model(self) -> str:
        """Select the best available model."""
        if self._model:
            return self._model

        available = await self.get_available_models()

        # Try primary model first
        if self.config.model in available:
            self._model = self.config.model
        # Try fallback
        elif self.config.fallback_model in available:
            self._model = self.config.fallback_model
        # Try any llama model
        else:
            llama_models = [m for m in available if "llama" in m.lower()]
            if llama_models:
                self._model = llama_models[0]
            elif available:
                self._model = available[0]
            else:
                raise RuntimeError(
                    f"No models available. Run: ollama pull {self.config.model}"
                )

        return self._model

    async def chat_stream(
        self, messages: list[Message], system_prompt: str | None = None,
        model: str | None = None, tools: list | None = None,
    ) -> AsyncIterator[str]:
        """Stream a chat completion response."""
        model = model or await self.select_model()

        # Build messages list
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            api_messages.append({"role": msg.role, "content": msg.content})

        payload = {
            "model": model,
            "messages": api_messages,
            "stream": True,
        }

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if "message" in data and "content" in data["message"]:
                                yield data["message"]["content"]
                        except json.JSONDecodeError:
                            continue
