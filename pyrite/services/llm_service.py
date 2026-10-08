"""
Provider-agnostic LLM Abstraction Service.

Supports Anthropic (Claude), OpenAI (GPT), OpenRouter (200+ models),
Ollama (local), and a stub/no-op provider. SDKs are optional dependencies
imported only when needed.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from ..config import Settings
from ..exceptions import MissingOptionalDependencyError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy SDK imports — return the module or None
# ---------------------------------------------------------------------------


def _import_anthropic():
    """Try to import the anthropic SDK. Returns module or None."""
    try:
        import anthropic

        return anthropic
    except ImportError:
        return None


def _import_openai():
    """Try to import the openai SDK. Returns module or None."""
    try:
        import openai

        return openai
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# LLMService
# ---------------------------------------------------------------------------


class LLMService:
    """Provider-agnostic LLM interface.

    Providers:
        - ``"anthropic"`` — uses the ``anthropic`` SDK directly
        - ``"openai"`` — uses the ``openai`` SDK
        - ``"openrouter"`` — OpenRouter via OpenAI-compatible API
        - ``"ollama"`` — Ollama local models via OpenAI-compatible API
        - ``"gemini"`` — Google Gemini via OpenAI-compatible API
        - ``"stub"`` / ``"none"`` / ``""`` — no-op that returns empty strings / vectors
    """

    # Providers that use the OpenAI SDK under the hood
    _OPENAI_COMPAT_PROVIDERS = ("openai", "gemini", "openrouter", "ollama", "local")

    def __init__(
        self,
        settings: Settings,
        usage_service: Any = None,
        user_id: int | None = None,
    ) -> None:
        self._settings = settings
        self._provider = settings.ai_provider or "stub"
        if self._provider in ("none", ""):
            self._provider = "stub"
        # llm-usage-tracking-and-quotas: optional injected recorder, so
        # MCP/CLI callers that don't wire tracking keep working exactly
        # as before (no-op when unset).
        self._usage_service = usage_service
        self._user_id = user_id

    # -- public helpers -----------------------------------------------------

    @property
    def provider_name(self) -> str:
        return self._provider

    def _requires_api_key(self) -> bool:
        """Whether the current provider requires an API key."""
        return self._provider not in ("stub", "none", "", "ollama")

    def status(self) -> dict[str, Any]:
        """Return a JSON-safe status dict for the /api/ai/status endpoint."""
        if self._provider in ("stub", "none", ""):
            configured = False
        elif self._provider == "ollama":
            # Ollama doesn't require an API key — just needs provider + model
            configured = True
        else:
            configured = bool(self._settings.ai_api_key)
        return {
            "configured": configured,
            "provider": self._provider,
            "model": self._settings.ai_model,
        }

    # -- core API -----------------------------------------------------------

    def with_user_key(
        self,
        api_key: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> LLMService:
        """Return a new LLMService instance with user-provided overrides.

        This creates a copy of the underlying Settings with the user's API key
        (and optionally provider/model) substituted in, so the original service
        remains unmodified.
        """
        settings = Settings(
            ai_provider=provider or self._settings.ai_provider,
            ai_api_key=api_key,
            ai_model=model or self._settings.ai_model,
            ai_api_base=self._settings.ai_api_base,
        )
        # Apply default base URL for Gemini when switching provider
        if settings.ai_provider == "gemini" and not settings.ai_api_base:
            settings.ai_api_base = "https://generativelanguage.googleapis.com/v1beta/openai/"
        return LLMService(settings, usage_service=self._usage_service, user_id=self._user_id)

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
        cache_system: bool = False,
        kind: str = "chat",
    ) -> str:
        """Generate a completion and return the full text.

        Args:
            prompt: User-turn content.
            system: Optional system prompt.
            max_tokens: Response token cap.
            cache_system: When True and provider is Anthropic, the system
                prompt is sent as a content block tagged
                ``cache_control: {"type": "ephemeral"}`` so the provider
                caches it for ~5 minutes at ~10% input cost. Use for
                long, stable system prompts replayed across calls (RAG
                chat, on-save QA, summarize). No-op for non-Anthropic
                providers. See Tier A r2200.
            kind: Usage-tracking category recorded alongside token counts
                (e.g. "chat", "summarize", "auto-tag") -- must match what
                per-kind quota checks (QuotaService.check_llm_quota) query
                for, or usage recorded here becomes invisible to them.
        """
        if self._provider == "stub":
            return ""
        if self._provider == "anthropic":
            return self._anthropic_complete(prompt, system, max_tokens, cache_system, kind)
        if self._provider in self._OPENAI_COMPAT_PROVIDERS:
            return self._openai_complete(prompt, system, max_tokens)
        return ""

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        cache_system: bool = False,
    ) -> AsyncIterator[str]:
        """Stream completion tokens as an async iterator.

        See :meth:`complete` for ``cache_system`` semantics.
        """
        if self._provider == "stub":
            return
        if self._provider == "anthropic":
            for chunk in self._anthropic_stream(prompt, system, cache_system):
                yield chunk
            return
        if self._provider in self._OPENAI_COMPAT_PROVIDERS:
            for chunk in self._openai_stream(prompt, system):
                yield chunk
            return

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Produce embedding vectors for the given texts."""
        if self._provider == "stub":
            return [[] for _ in texts]
        if self._provider == "anthropic":
            # Anthropic does not have an embeddings API — fall back to empty
            return [[] for _ in texts]
        if self._provider in self._OPENAI_COMPAT_PROVIDERS:
            return self._openai_embed(texts)
        return [[] for _ in texts]

    # -- Anthropic backend --------------------------------------------------

    def _get_anthropic_client(self):
        mod = _import_anthropic()
        if mod is None:
            raise MissingOptionalDependencyError(
                "The 'anthropic' package is required for the Anthropic provider. "
                "Install it with: pip install anthropic"
            )
        kwargs: dict[str, Any] = {"api_key": self._settings.ai_api_key}
        if self._settings.ai_api_base:
            kwargs["base_url"] = self._settings.ai_api_base
        return mod.Anthropic(**kwargs)

    @staticmethod
    def _anthropic_system_arg(system: str, cache_system: bool):
        """Build the ``system`` kwarg in the shape Anthropic expects.

        Plain string (back-compat) when ``cache_system`` is False; a single
        text block with ``cache_control: ephemeral`` when True. Wrapping
        the system prompt is what makes the provider cache it for ~5
        minutes — repeated calls then pay ~10% of the input price on the
        cached tokens (Tier A r2200).
        """
        if not cache_system:
            return system
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _log_anthropic_cache_usage(response) -> None:
        """If the response's usage carries cache token counts, emit a one-
        line INFO log so operators can measure cache hit rate after rollout.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        created = getattr(usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if created or read:
            logger.info(
                "Anthropic prompt-cache usage: creation=%d read=%d (input=%d output=%d)",
                created,
                read,
                getattr(usage, "input_tokens", 0) or 0,
                getattr(usage, "output_tokens", 0) or 0,
            )

    def _record_anthropic_usage(self, response, kind: str = "chat") -> None:
        """Record token usage via the injected usage_service, if configured.
        No-op when usage_service is unset (default) -- callers that don't
        wire tracking keep working exactly as before."""
        if self._usage_service is None:
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self._usage_service.record_usage(
            user_id=self._user_id,
            provider=self._provider,
            model=self._settings.ai_model,
            kind=kind,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    def _anthropic_complete(
        self,
        prompt: str,
        system: str | None,
        max_tokens: int,
        cache_system: bool = False,
        kind: str = "chat",
    ) -> str:
        client = self._get_anthropic_client()
        kwargs: dict[str, Any] = {
            "model": self._settings.ai_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = self._anthropic_system_arg(system, cache_system)
        response = client.messages.create(**kwargs)
        self._log_anthropic_cache_usage(response)
        self._record_anthropic_usage(response, kind=kind)
        return response.content[0].text

    def _anthropic_stream(
        self,
        prompt: str,
        system: str | None,
        cache_system: bool = False,
    ):
        client = self._get_anthropic_client()
        kwargs: dict[str, Any] = {
            "model": self._settings.ai_model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        if system:
            kwargs["system"] = self._anthropic_system_arg(system, cache_system)
        with client.messages.stream(**kwargs) as stream:
            yield from stream.text_stream

    def _resolve_base_url(self) -> str | None:
        """Resolve the effective base URL for the current provider."""
        if self._settings.ai_api_base:
            return self._settings.ai_api_base
        # Default base URLs for providers that need them
        if self._provider == "ollama":
            return "http://localhost:11434/v1"
        return None

    def test_connection(self) -> dict[str, Any]:
        """Actually test the connection to the configured provider.

        Returns a dict with ``ok`` (bool) and ``message`` (str).
        For Ollama, validates the model exists on the server.
        """
        status = self.status()
        if not status["configured"]:
            return {"ok": False, "message": "AI provider is not configured"}

        try:
            if self._provider == "anthropic":
                # Send a minimal request to verify credentials
                client = self._get_anthropic_client()
                client.messages.create(
                    model=self._settings.ai_model,
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                )
                return {
                    "ok": True,
                    "message": f"Connected: {self._provider} ({self._settings.ai_model})",
                }

            if self._provider in self._OPENAI_COMPAT_PROVIDERS:
                client = self._get_openai_client()
                if self._provider == "ollama":
                    # Ollama supports model listing — verify the model exists
                    base = self._resolve_base_url() or "http://localhost:11434/v1"
                    # Strip /v1 to get the Ollama API root
                    ollama_root = base.rstrip("/")
                    if ollama_root.endswith("/v1"):
                        ollama_root = ollama_root[:-3]
                    import json as _json
                    import urllib.request

                    req = urllib.request.Request(f"{ollama_root}/api/tags", method="GET")
                    try:
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            data = _json.loads(resp.read())
                    except (urllib.error.URLError, OSError) as exc:
                        return {
                            "ok": False,
                            "message": f"Cannot reach Ollama at {ollama_root}: {exc}",
                        }
                    available = [m["name"] for m in data.get("models", [])]
                    model = self._settings.ai_model
                    # Ollama model names can be "llama3.2" or "llama3.2:latest"
                    matched = any(
                        model == name or model == name.split(":")[0] for name in available
                    )
                    if not matched:
                        short_list = ", ".join(available[:8])
                        suffix = f" (and {len(available) - 8} more)" if len(available) > 8 else ""
                        return {
                            "ok": False,
                            "message": f"Ollama is running but model '{model}' not found. Available: {short_list}{suffix}",
                        }
                    return {"ok": True, "message": f"Connected: Ollama ({model})"}
                else:
                    # For other OpenAI-compat providers, do a minimal completion
                    client.chat.completions.create(
                        model=self._settings.ai_model,
                        messages=[{"role": "user", "content": "hi"}],
                        max_tokens=1,
                    )
                    return {
                        "ok": True,
                        "message": f"Connected: {self._provider} ({self._settings.ai_model})",
                    }

        except Exception as exc:
            msg = str(exc)
            # Truncate long error messages
            if len(msg) > 200:
                msg = msg[:200] + "..."
            return {"ok": False, "message": f"Connection failed: {msg}"}

        return {"ok": False, "message": f"Unknown provider: {self._provider}"}

    # -- OpenAI backend (also OpenRouter, Ollama) ---------------------------

    def _get_openai_client(self):
        mod = _import_openai()
        if mod is None:
            raise MissingOptionalDependencyError(
                "The 'openai' package is required for the OpenAI/OpenRouter/Ollama provider. "
                "Install it with: pip install openai"
            )
        kwargs: dict[str, Any] = {"api_key": self._settings.ai_api_key or "no-key"}
        base_url = self._resolve_base_url()
        if base_url:
            kwargs["base_url"] = base_url
        return mod.OpenAI(**kwargs)

    def _openai_complete(self, prompt: str, system: str | None, max_tokens: int) -> str:
        client = self._get_openai_client()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model=self._settings.ai_model,
            messages=messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def _openai_stream(self, prompt: str, system: str | None):
        client = self._get_openai_client()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model=self._settings.ai_model,
            messages=messages,
            stream=True,
        )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def _openai_embed(self, texts: list[str]) -> list[list[float]]:
        client = self._get_openai_client()
        response = client.embeddings.create(
            model=self._settings.embedding_model or "text-embedding-3-small",
            input=texts,
        )
        return [item.embedding for item in response.data]
