from __future__ import annotations

import asyncio
from collections import OrderedDict
import json
from collections.abc import AsyncIterator

import httpx

from app.config import get_settings


_EMBED_CACHE_MAX = 256
_EMBED_CACHE: OrderedDict[tuple[str, str], list[float]] = OrderedDict()


def _prepare_prompt(prompt: str, think: bool) -> str:
    """Qwen models are more reliable with an explicit prompt-level no-think flag."""
    if think:
        return prompt
    if prompt.lstrip().startswith("/no_think"):
        return prompt
    return f"/no_think\n{prompt}"


class OllamaClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def _post(self, path: str, payload: dict, timeout: int) -> httpx.Response:
        last_error: Exception | None = None
        for attempt, delay in enumerate((0, 2, 5), start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(f"{self.settings.ollama_base_url}{path}", json=payload)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code < 500 or attempt == 3:
                    raise
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_error = exc
                if attempt == 3:
                    raise
        raise RuntimeError("Ollama 请求重试失败") from last_error

    async def embed(self, texts: list[str]) -> list[list[float]]:
        cached: list[list[float] | None] = []
        missing: list[str] = []
        missing_positions: list[int] = []
        for index, text in enumerate(texts):
            key = (self.settings.embedding_model, text)
            value = _EMBED_CACHE.get(key)
            if value is None:
                cached.append(None)
                missing.append(text)
                missing_positions.append(index)
            else:
                _EMBED_CACHE.move_to_end(key)
                cached.append(value)

        if missing:
            response = await self._post(
                "/api/embed",
                {"model": self.settings.embedding_model, "input": missing, "keep_alive": "2m"},
                600,
            )
            embeddings = response.json()["embeddings"]
            for text, position, embedding in zip(missing, missing_positions, embeddings, strict=True):
                key = (self.settings.embedding_model, text)
                _EMBED_CACHE[key] = embedding
                _EMBED_CACHE.move_to_end(key)
                while len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
                    _EMBED_CACHE.popitem(last=False)
                cached[position] = embedding

        return [embedding for embedding in cached if embedding is not None]

    async def embed_uncached(self, texts: list[str]) -> list[list[float]]:
        response = await self._post(
            "/api/embed",
            {"model": self.settings.embedding_model, "input": texts, "keep_alive": "2m"},
            600,
        )
        return response.json()["embeddings"]

    async def generate(self, prompt: str, *, json_mode: bool = False, num_predict: int = 500) -> str:
        payload: dict[str, object] = {
            "model": self.settings.chat_model,
            "prompt": _prepare_prompt(prompt, self.settings.ollama_think),
            "stream": False,
            "think": self.settings.ollama_think,
            "keep_alive": "10m",
            "options": {"num_ctx": 3072, "num_predict": num_predict, "temperature": 0.1},
        }
        if json_mode:
            payload["format"] = "json"
        response = await self._post("/api/generate", payload, 600)
        return response.json().get("response", "").strip()

    async def generate_json(self, prompt: str) -> dict:
        raw = await self.generate(prompt, json_mode=True, num_predict=300)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    async def generate_stream(self, prompt: str, *, num_predict: int = 500) -> AsyncIterator[str]:
        payload: dict[str, object] = {
            "model": self.settings.chat_model,
            "prompt": _prepare_prompt(prompt, self.settings.ollama_think),
            "stream": True,
            "think": self.settings.ollama_think,
            "keep_alive": "10m",
            "options": {"num_ctx": 3072, "num_predict": num_predict, "temperature": 0.1},
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, read=600)) as client:
            async with client.stream("POST", f"{self.settings.ollama_base_url}/api/generate", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    item = json.loads(line)
                    token = item.get("response", "")
                    if token:
                        yield token
