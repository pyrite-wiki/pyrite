"""Tests for LLM Abstraction Service."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from pyrite.config import Settings
from pyrite.exceptions import PluginError

# ---------------------------------------------------------------------------
# Config round-trip tests
# ---------------------------------------------------------------------------


class TestSettingsAIFields:
    """Test that new AI settings fields work correctly."""

    def test_default_settings(self):
        s = Settings()
        assert s.ai_provider == "stub"
        assert s.ai_model == "claude-sonnet-4-20250514"
        assert s.ai_api_base == ""

    def test_custom_provider(self):
        s = Settings(ai_provider="anthropic", ai_model="claude-sonnet-4-20250514")
        assert s.ai_provider == "anthropic"
        assert s.ai_model == "claude-sonnet-4-20250514"

    def test_openrouter_provider(self):
        s = Settings(
            ai_provider="openai",
            ai_model="openai/gpt-4o",
            ai_api_base="https://openrouter.ai/api/v1",
        )
        assert s.ai_provider == "openai"
        assert s.ai_api_base == "https://openrouter.ai/api/v1"

    def test_ollama_provider(self):
        s = Settings(
            ai_provider="ollama",
            ai_model="llama3",
        )
        assert s.ai_provider == "ollama"

    def test_openrouter_native_provider(self):
        s = Settings(
            ai_provider="openrouter",
            ai_model="anthropic/claude-sonnet-4",
            ai_api_key="sk-or-test",
        )
        assert s.ai_provider == "openrouter"


# ---------------------------------------------------------------------------
# LLMService tests
# ---------------------------------------------------------------------------


class TestLLMServiceStub:
    """Test stub provider returns empty results."""

    def test_stub_complete(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="stub")
        svc = LLMService(settings)
        result = asyncio.run(svc.complete("Hello"))
        assert result == ""

    def test_stub_embed(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="stub")
        svc = LLMService(settings)
        result = asyncio.run(svc.embed(["hello", "world"]))
        assert result == [[], []]

    def test_stub_stream(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="stub")
        svc = LLMService(settings)

        async def collect():
            chunks = []
            async for chunk in svc.stream("Hello"):
                chunks.append(chunk)
            return chunks

        result = asyncio.run(collect())
        assert result == []

    def test_empty_provider_is_stub(self):
        """Empty or 'none' provider acts as stub."""
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="none")
        svc = LLMService(settings)
        result = asyncio.run(svc.complete("Hello"))
        assert result == ""

    def test_status(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="stub", ai_model="test-model")
        svc = LLMService(settings)
        status = svc.status()
        assert status["configured"] is False
        assert status["provider"] == "stub"
        assert status["model"] == "test-model"


class TestLLMServiceProviderSelection:
    """Test provider selection logic."""

    def test_anthropic_selected(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="anthropic", ai_model="claude-sonnet-4-20250514")
        svc = LLMService(settings)
        assert svc.provider_name == "anthropic"

    def test_openai_selected(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="openai", ai_model="gpt-4o")
        svc = LLMService(settings)
        assert svc.provider_name == "openai"

    def test_configured_status_for_real_providers(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="anthropic", ai_api_key="sk-test-key")
        svc = LLMService(settings)
        status = svc.status()
        assert status["configured"] is True


class TestLLMServiceGemini:
    """Test Gemini provider routes through OpenAI backend."""

    def test_gemini_selected(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="gemini", ai_model="gemini-2.0-flash")
        svc = LLMService(settings)
        assert svc.provider_name == "gemini"

    def test_gemini_configured_status(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="gemini", ai_api_key="test-key")
        svc = LLMService(settings)
        status = svc.status()
        assert status["configured"] is True
        assert status["provider"] == "gemini"

    def test_gemini_complete_uses_openai_backend(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="gemini",
            ai_api_key="test-key",
            ai_model="gemini-2.0-flash",
            ai_api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        svc = LLMService(settings)

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Gemini says hi!"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.OpenAI.return_value = mock_client

        with patch("pyrite.services.llm_service._import_openai", return_value=mock_module):
            result = asyncio.run(svc.complete("Hello"))

        assert result == "Gemini says hi!"
        mock_module.OpenAI.assert_called_once()
        call_kwargs = mock_module.OpenAI.call_args[1]
        assert "generativelanguage.googleapis.com" in call_kwargs["base_url"]

    def test_gemini_default_base_url_from_config(self):
        """Settings.__post_init__ sets Gemini base URL when not provided."""
        s = Settings(ai_provider="gemini", ai_api_key="test-key")
        assert "generativelanguage.googleapis.com" in s.ai_api_base

    def test_gemini_settings_valid(self):
        s = Settings(ai_provider="gemini", ai_model="gemini-2.0-flash")
        assert s.ai_provider == "gemini"
        assert s.ai_model == "gemini-2.0-flash"


class TestLLMServiceOllama:
    """Test Ollama provider routes through OpenAI backend."""

    def test_ollama_selected(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="ollama", ai_model="llama3.2")
        svc = LLMService(settings)
        assert svc.provider_name == "ollama"

    def test_ollama_configured_without_api_key(self):
        """Ollama should be configured even without an API key."""
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="ollama", ai_model="llama3.2")
        svc = LLMService(settings)
        status = svc.status()
        assert status["configured"] is True
        assert status["provider"] == "ollama"

    def test_ollama_does_not_require_api_key(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="ollama", ai_model="llama3.2")
        svc = LLMService(settings)
        assert svc._requires_api_key() is False

    def test_ollama_complete_uses_openai_backend(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="ollama", ai_model="llama3.2")
        svc = LLMService(settings)

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Ollama says hi!"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.OpenAI.return_value = mock_client

        with patch("pyrite.services.llm_service._import_openai", return_value=mock_module):
            result = asyncio.run(svc.complete("Hello"))

        assert result == "Ollama says hi!"
        mock_module.OpenAI.assert_called_once()
        call_kwargs = mock_module.OpenAI.call_args[1]
        assert call_kwargs["base_url"] == "http://localhost:11434/v1"

    def test_ollama_default_base_url(self):
        """Ollama should auto-resolve to localhost:11434/v1."""
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="ollama", ai_model="llama3.2")
        svc = LLMService(settings)
        assert svc._resolve_base_url() == "http://localhost:11434/v1"

    def test_ollama_custom_base_url(self):
        """User can override the Ollama base URL."""
        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="ollama",
            ai_model="llama3.2",
            ai_api_base="http://my-server:11434/v1",
        )
        svc = LLMService(settings)
        assert svc._resolve_base_url() == "http://my-server:11434/v1"


class TestLLMServiceOpenRouter:
    """Test OpenRouter as a first-class provider."""

    def test_openrouter_selected(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="openrouter",
            ai_api_key="sk-or-test",
            ai_model="anthropic/claude-sonnet-4",
        )
        svc = LLMService(settings)
        assert svc.provider_name == "openrouter"

    def test_openrouter_configured_with_key(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="openrouter", ai_api_key="sk-or-test")
        svc = LLMService(settings)
        status = svc.status()
        assert status["configured"] is True

    def test_openrouter_not_configured_without_key(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="openrouter")
        svc = LLMService(settings)
        status = svc.status()
        assert status["configured"] is False


class TestLLMServiceMissingSDK:
    """Test that missing SDK raises helpful error."""

    def test_anthropic_missing_sdk_error(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="anthropic", ai_api_key="sk-test")
        svc = LLMService(settings)

        with patch.dict("sys.modules", {"anthropic": None}):
            with patch("pyrite.services.llm_service._import_anthropic", return_value=None):
                with pytest.raises(PluginError, match="anthropic"):
                    asyncio.run(svc.complete("Hello"))

    def test_openai_missing_sdk_error(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="openai", ai_api_key="sk-test")
        svc = LLMService(settings)

        with patch("pyrite.services.llm_service._import_openai", return_value=None):
            with pytest.raises(PluginError, match="openai"):
                asyncio.run(svc.complete("Hello"))

    def test_anthropic_missing_sdk_message_reaches_callers_unmasked(self):
        """#506 item 2: "Install it with: pip install 'pyrite[ai]'" names no
        server-side detail -- it's the same fixed hint for every caller --
        so it's safe by construction. #501 gave the base PluginError a
        fixed, generic public_message for raise sites that DO carry unsafe
        detail (a traceback fragment, a real path); this specific hint needs
        its own narrow subclass with public_message=None."""
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="anthropic", ai_api_key="sk-test")
        svc = LLMService(settings)

        with patch("pyrite.services.llm_service._import_anthropic", return_value=None):
            with pytest.raises(PluginError) as excinfo:
                asyncio.run(svc.complete("Hello"))

        exc = excinfo.value
        assert exc.public_message is None
        assert "pip install" in str(exc)

    def test_openai_missing_sdk_message_reaches_callers_unmasked(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="openai", ai_api_key="sk-test")
        svc = LLMService(settings)

        with patch("pyrite.services.llm_service._import_openai", return_value=None):
            with pytest.raises(PluginError) as excinfo:
                asyncio.run(svc.complete("Hello"))

        exc = excinfo.value
        assert exc.public_message is None
        assert "pip install" in str(exc)


class TestLLMServiceAnthropicMocked:
    """Test Anthropic provider with mocked SDK."""

    def test_complete_calls_anthropic(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="anthropic", ai_api_key="sk-test", ai_model="claude-sonnet-4-20250514"
        )
        svc = LLMService(settings)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello back!")]
        mock_client.messages.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.Anthropic.return_value = mock_client

        with patch("pyrite.services.llm_service._import_anthropic", return_value=mock_module):
            result = asyncio.run(svc.complete("Hello", system="Be helpful"))

        assert result == "Hello back!"
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["max_tokens"] == 1024
        assert call_kwargs["system"] == "Be helpful"


class TestLLMServiceUsageTracking:
    """llm-usage-tracking-and-quotas: LLMService records usage via an
    injected LLMUsageService + user_id, when both are provided. No
    usage_service configured (the default) is a silent no-op -- MCP/CLI
    callers that don't wire tracking must not break."""

    def test_complete_records_usage_when_tracking_configured(self):
        from unittest.mock import MagicMock

        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="anthropic", ai_api_key="sk-test", ai_model="claude-sonnet-4-20250514"
        )
        usage_service = MagicMock()
        svc = LLMService(settings, usage_service=usage_service, user_id=42)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello back!")]
        mock_response.usage.input_tokens = 12
        mock_response.usage.output_tokens = 7
        mock_response.usage.cache_creation_input_tokens = 0
        mock_response.usage.cache_read_input_tokens = 0
        mock_client.messages.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.Anthropic.return_value = mock_client

        with patch("pyrite.services.llm_service._import_anthropic", return_value=mock_module):
            asyncio.run(svc.complete("Hello"))

        usage_service.record_usage.assert_called_once()
        call_kwargs = usage_service.record_usage.call_args.kwargs
        assert call_kwargs["user_id"] == 42
        assert call_kwargs["provider"] == "anthropic"
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["input_tokens"] == 12
        assert call_kwargs["output_tokens"] == 7

    def test_complete_records_usage_with_caller_supplied_kind(self):
        """The recorded kind must reflect what the caller actually asked
        for (summarize/auto-tag/chat/...), not a hardcoded default --
        otherwise a quota check scoped to e.g. kind='summarize' can never
        see rows a summarize call actually produced."""
        from unittest.mock import MagicMock

        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="anthropic", ai_api_key="sk-test", ai_model="claude-sonnet-4-20250514"
        )
        usage_service = MagicMock()
        svc = LLMService(settings, usage_service=usage_service, user_id=42)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Summary text")]
        mock_response.usage.input_tokens = 12
        mock_response.usage.output_tokens = 7
        mock_response.usage.cache_creation_input_tokens = 0
        mock_response.usage.cache_read_input_tokens = 0
        mock_client.messages.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.Anthropic.return_value = mock_client

        with patch("pyrite.services.llm_service._import_anthropic", return_value=mock_module):
            asyncio.run(svc.complete("Summarize this", kind="summarize"))

        call_kwargs = usage_service.record_usage.call_args.kwargs
        assert call_kwargs["kind"] == "summarize"

    def test_complete_default_kind_is_chat(self):
        """Callers that don't specify kind keep the existing default."""
        from unittest.mock import MagicMock

        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="anthropic", ai_api_key="sk-test", ai_model="claude-sonnet-4-20250514"
        )
        usage_service = MagicMock()
        svc = LLMService(settings, usage_service=usage_service, user_id=42)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hi")]
        mock_response.usage.input_tokens = 1
        mock_response.usage.output_tokens = 1
        mock_response.usage.cache_creation_input_tokens = 0
        mock_response.usage.cache_read_input_tokens = 0
        mock_client.messages.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.Anthropic.return_value = mock_client

        with patch("pyrite.services.llm_service._import_anthropic", return_value=mock_module):
            asyncio.run(svc.complete("Hi"))

        call_kwargs = usage_service.record_usage.call_args.kwargs
        assert call_kwargs["kind"] == "chat"

    def test_complete_without_usage_service_does_not_raise(self):
        """Default construction (no usage tracking wired) must behave
        exactly as before -- MCP/CLI callers that don't pass
        usage_service shouldn't break or silently need updating."""
        from unittest.mock import MagicMock

        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="anthropic", ai_api_key="sk-test", ai_model="claude-sonnet-4-20250514"
        )
        svc = LLMService(settings)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello back!")]
        mock_response.usage.input_tokens = 12
        mock_response.usage.output_tokens = 7
        mock_client.messages.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.Anthropic.return_value = mock_client

        with patch("pyrite.services.llm_service._import_anthropic", return_value=mock_module):
            result = asyncio.run(svc.complete("Hello"))

        assert result == "Hello back!"

    def test_with_user_key_preserves_usage_tracking_context(self):
        """with_user_key() (BYOK override) must carry the usage_service
        and user_id forward -- otherwise a BYOK user's usage silently
        stops being tracked the moment they use their own key."""
        from unittest.mock import MagicMock

        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="anthropic")
        usage_service = MagicMock()
        svc = LLMService(settings, usage_service=usage_service, user_id=42)

        byok_svc = svc.with_user_key(api_key="sk-user-key")

        assert byok_svc._usage_service is usage_service
        assert byok_svc._user_id == 42


class TestLLMServiceOpenAIMocked:
    """Test OpenAI provider with mocked SDK."""

    def test_complete_calls_openai(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="openai", ai_api_key="sk-test", ai_model="gpt-4o")
        svc = LLMService(settings)

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "GPT says hi!"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.OpenAI.return_value = mock_client

        with patch("pyrite.services.llm_service._import_openai", return_value=mock_module):
            result = asyncio.run(svc.complete("Hello", system="Be helpful"))

        assert result == "GPT says hi!"
        mock_client.chat.completions.create.assert_called_once()

    def test_embed_calls_openai(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="openai", ai_api_key="sk-test", ai_model="gpt-4o")
        svc = LLMService(settings)

        mock_client = MagicMock()
        mock_data_0 = MagicMock()
        mock_data_0.embedding = [0.1, 0.2, 0.3]
        mock_data_1 = MagicMock()
        mock_data_1.embedding = [0.4, 0.5, 0.6]
        mock_response = MagicMock()
        mock_response.data = [mock_data_0, mock_data_1]
        mock_client.embeddings.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.OpenAI.return_value = mock_client

        with patch("pyrite.services.llm_service._import_openai", return_value=mock_module):
            result = asyncio.run(svc.embed(["hello", "world"]))

        assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    def test_openai_with_custom_base_url(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="openai",
            ai_api_key="sk-test",
            ai_model="llama3",
            ai_api_base="http://localhost:11434/v1",
        )
        svc = LLMService(settings)

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Ollama says hi!"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create = MagicMock(return_value=mock_response)

        mock_module = MagicMock()
        mock_module.OpenAI.return_value = mock_client

        with patch("pyrite.services.llm_service._import_openai", return_value=mock_module):
            result = asyncio.run(svc.complete("Hello"))

        assert result == "Ollama says hi!"
        # Verify base_url was passed to OpenAI constructor
        mock_module.OpenAI.assert_called_once()
        call_kwargs = mock_module.OpenAI.call_args[1]
        assert call_kwargs["base_url"] == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# API endpoint test
# ---------------------------------------------------------------------------


class TestLLMServiceTestConnection:
    """Test the test_connection method."""

    def test_stub_not_configured(self):
        from pyrite.services.llm_service import LLMService

        settings = Settings(ai_provider="stub")
        svc = LLMService(settings)
        result = svc.test_connection()
        assert result["ok"] is False
        assert "not configured" in result["message"].lower()

    def test_ollama_unreachable(self):
        """When Ollama server is not running, test_connection should fail gracefully."""
        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="ollama",
            ai_model="llama3.2",
            ai_api_base="http://localhost:99999/v1",  # unlikely to be running
        )
        svc = LLMService(settings)
        result = svc.test_connection()
        assert result["ok"] is False
        assert (
            "Cannot reach Ollama" in result["message"] or "Connection failed" in result["message"]
        )

    def test_openai_bad_key(self):
        """OpenAI with invalid key should fail."""
        from pyrite.services.llm_service import LLMService

        settings = Settings(
            ai_provider="openai",
            ai_api_key="sk-fake",
            ai_model="gpt-4o",
        )
        svc = LLMService(settings)

        mock_module = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("Invalid API key")
        mock_module.OpenAI.return_value = mock_client

        with patch("pyrite.services.llm_service._import_openai", return_value=mock_module):
            result = svc.test_connection()
        assert result["ok"] is False
        # The provider's text is the operator's: logged, not returned.
        assert "Invalid API key" not in result["message"]
        assert "Connection failed" in result["message"]


class TestAIStatusEndpoint:
    """Test /api/ai/status endpoint."""

    def test_ai_status_returns_config(self):
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app

        app = create_app()
        with TestClient(app) as client:
            response = client.get("/api/ai/status")
            assert response.status_code == 200
            data = response.json()
            assert "configured" in data
            assert "provider" in data
            assert "model" in data
            assert isinstance(data["configured"], bool)
            assert isinstance(data["provider"], str)
            assert isinstance(data["model"], str)

    def test_ai_test_endpoint_exists(self):
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app

        app = create_app()
        with TestClient(app) as client:
            response = client.post("/api/ai/test")
            assert response.status_code == 200
            data = response.json()
            assert "ok" in data
            assert "message" in data


# ---------------------------------------------------------------------------
# Anthropic prompt caching — Tier A r2200
# ---------------------------------------------------------------------------


class TestAnthropicPromptCaching:
    """Verify LLMService.complete()/stream() pass cache_control through to the
    Anthropic SDK when cache_system=True (Tier A r2200).

    Pre-fix: every call rebuilt the system kwarg as a plain string with no
    cache_control, so cached tokens never landed even though the SDK
    supports them and the workload (RAG chat, on-save QA, summarize) is
    exactly the long-system-prompt-replayed-across-calls shape that prompt
    caching targets.
    """

    def _patch_anthropic(self, complete_text="ok", usage=None):
        """Build a mock anthropic module wired into _import_anthropic.

        Returns the patch context manager and the inner messages.create
        mock so tests can assert the kwargs passed to it.
        """
        from unittest.mock import MagicMock

        # Mock response.content[0].text = "ok" and response.usage carries
        # the cache_read / cache_creation token counts the ticket wants
        # surfaced for hit-rate measurement.
        msg = MagicMock()
        msg.text = complete_text
        response = MagicMock()
        response.content = [msg]
        response.usage = usage or MagicMock(
            input_tokens=100,
            output_tokens=10,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        client = MagicMock()
        client.messages.create.return_value = response

        anthropic_mod = MagicMock()
        anthropic_mod.Anthropic.return_value = client

        return anthropic_mod, client

    def test_complete_without_cache_sends_plain_system_string(self):
        """Default behavior (no caching flag): system is passed as a plain
        string, no cache_control anywhere — back-compat."""
        from pyrite.services.llm_service import LLMService

        anthropic_mod, client = self._patch_anthropic()
        settings = Settings(ai_provider="anthropic", ai_api_key="sk-test")

        with patch("pyrite.services.llm_service._import_anthropic", return_value=anthropic_mod):
            svc = LLMService(settings)
            asyncio.run(svc.complete("hi", system="You are a researcher."))

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["system"] == "You are a researcher.", (
            f"default path must keep system as plain string; got {kwargs['system']!r}"
        )

    def test_complete_with_cache_system_uses_cache_control_block(self):
        """cache_system=True wraps the system prompt in the Anthropic SDK's
        block shape with cache_control: ephemeral. This is what makes the
        provider cache the system prompt for ~5 minutes at ~10% input cost.
        """
        from pyrite.services.llm_service import LLMService

        anthropic_mod, client = self._patch_anthropic()
        settings = Settings(ai_provider="anthropic", ai_api_key="sk-test")

        with patch("pyrite.services.llm_service._import_anthropic", return_value=anthropic_mod):
            svc = LLMService(settings)
            asyncio.run(
                svc.complete("hi", system="A long detailed system prompt.", cache_system=True)
            )

        kwargs = client.messages.create.call_args.kwargs
        system_arg = kwargs["system"]
        assert isinstance(system_arg, list), (
            f"cache_system=True must use the list-of-blocks shape; got {type(system_arg)}"
        )
        assert len(system_arg) == 1
        block = system_arg[0]
        assert block["type"] == "text"
        assert block["text"] == "A long detailed system prompt."
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_complete_with_cache_system_but_no_system_does_not_set_system(self):
        """cache_system=True with system=None is a no-op — don't accidentally
        send an empty cached block."""
        from pyrite.services.llm_service import LLMService

        anthropic_mod, client = self._patch_anthropic()
        settings = Settings(ai_provider="anthropic", ai_api_key="sk-test")

        with patch("pyrite.services.llm_service._import_anthropic", return_value=anthropic_mod):
            svc = LLMService(settings)
            asyncio.run(svc.complete("hi", system=None, cache_system=True))

        kwargs = client.messages.create.call_args.kwargs
        assert "system" not in kwargs, "no system text means no system kwarg, cached or otherwise"

    def test_complete_logs_cache_token_counts_when_returned(self, caplog):
        """When the Anthropic response carries cache_read_input_tokens or
        cache_creation_input_tokens, the service emits an INFO log line so
        operators can measure cache hit rate after rollout (Tier A r2200
        acceptance: 'log line records cache_read_input_tokens')."""
        import logging

        from pyrite.services.llm_service import LLMService

        usage = MagicMock(
            input_tokens=100,
            output_tokens=10,
            cache_creation_input_tokens=80,
            cache_read_input_tokens=0,
        )
        anthropic_mod, _ = self._patch_anthropic(usage=usage)
        settings = Settings(ai_provider="anthropic", ai_api_key="sk-test")

        with patch("pyrite.services.llm_service._import_anthropic", return_value=anthropic_mod):
            svc = LLMService(settings)
            with caplog.at_level(logging.INFO, logger="pyrite.services.llm_service"):
                asyncio.run(svc.complete("hi", system="sys", cache_system=True))

        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "cache" in m.lower() and ("80" in m or "creation" in m.lower()) for m in messages
        ), f"expected a cache-token log line; got {messages}"
