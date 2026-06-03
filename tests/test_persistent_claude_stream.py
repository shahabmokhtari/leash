"""Tests for PersistentClaudeStreamClient (claude-stream provider)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leash.models.configuration import LlmConfig
from leash.models.llm_response import LLMResponse
from leash.services.persistent_claude_stream_client import PersistentClaudeStreamClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stream_assistant(text: str) -> bytes:
    """Build a stream-json assistant message line."""
    return json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }).encode() + b"\n"


def _stream_result(result_text: str, session_id: str = "sess-1") -> bytes:
    """Build a stream-json result message line."""
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "result": result_text,
        "session_id": session_id,
        "cost_usd": 0.001,
    }).encode() + b"\n"


def _make_mock_proc(*, returncode=None, stdout_lines=None):
    """Create a mock asyncio subprocess with the given stdout lines."""
    mock_proc = AsyncMock()
    mock_proc.returncode = returncode
    mock_proc.pid = 99999
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(side_effect=stdout_lines or [b""])
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.readline = AsyncMock(return_value=b"")
    mock_proc.terminate = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()
    return mock_proc


def _patch_stream_asyncio(mock_proc):
    """Return a patch context manager for asyncio in the stream client module."""
    patcher = patch("leash.services.persistent_claude_stream_client.asyncio")

    class _Ctx:
        def __enter__(self_ctx):
            mock_asyncio = patcher.__enter__()
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            mock_asyncio.sleep = AsyncMock()
            mock_asyncio.create_task = MagicMock(side_effect=lambda coro: asyncio.ensure_future(coro))
            mock_asyncio.CancelledError = asyncio.CancelledError
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            return mock_asyncio

        def __exit__(self_ctx, *args):
            return patcher.__exit__(*args)

    return _Ctx()


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


class TestInstantiation:
    """Verify class construction and config validation."""

    def test_creates_with_valid_config(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        assert client is not None
        assert client._disposed is False

    def test_raises_on_none_config(self):
        with pytest.raises(ValueError, match="config is required"):
            PersistentClaudeStreamClient(config=None)

    def test_creates_fallback_client(self):
        from leash.services.claude_cli_client import ClaudeCliClient

        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        assert isinstance(client._fallback_client, ClaudeCliClient)


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------


class TestBuildUserMessage:
    """Verify _build_user_message produces correct stream-json format."""

    def test_simple_message(self):
        msg = PersistentClaudeStreamClient._build_user_message("hello world")
        parsed = json.loads(msg)
        assert parsed["type"] == "user"
        assert parsed["message"]["role"] == "user"
        assert parsed["message"]["content"] == "hello world"

    def test_special_characters(self):
        text = 'say "hello" & <goodbye>'
        msg = PersistentClaudeStreamClient._build_user_message(text)
        parsed = json.loads(msg)
        assert parsed["message"]["content"] == text

    def test_multiline_content(self):
        text = "line 1\nline 2\nline 3"
        msg = PersistentClaudeStreamClient._build_user_message(text)
        parsed = json.loads(msg)
        assert parsed["message"]["content"] == text


# ---------------------------------------------------------------------------
# Result line parsing
# ---------------------------------------------------------------------------


class TestParseResultLine:
    """Verify _parse_result_line extracts text from result messages."""

    def test_extracts_result_text(self):
        data = {
            "type": "result",
            "subtype": "success",
            "result": '{"safetyScore": 90, "reasoning": "safe", "category": "safe"}',
            "session_id": "s-1",
        }
        text = PersistentClaudeStreamClient._parse_result_line(data)
        assert text == '{"safetyScore": 90, "reasoning": "safe", "category": "safe"}'

    def test_returns_none_for_non_result(self):
        data = {"type": "assistant", "message": {}}
        assert PersistentClaudeStreamClient._parse_result_line(data) is None

    def test_returns_none_for_missing_result_field(self):
        data = {"type": "result", "subtype": "success"}
        assert PersistentClaudeStreamClient._parse_result_line(data) is None


# ---------------------------------------------------------------------------
# Assistant chunk parsing
# ---------------------------------------------------------------------------


class TestParseAssistantChunks:
    """Verify _parse_assistant_chunks extracts text from assistant messages."""

    def test_extracts_text_blocks(self):
        data = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "text", "text": "world"},
                ],
            },
        }
        text = PersistentClaudeStreamClient._parse_assistant_chunks(data)
        assert text == "Hello world"

    def test_ignores_non_text_blocks(self):
        data = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1"},
                    {"type": "text", "text": "only this"},
                ],
            },
        }
        text = PersistentClaudeStreamClient._parse_assistant_chunks(data)
        assert text == "only this"

    def test_returns_empty_for_non_assistant(self):
        data = {"type": "result", "result": "x"}
        assert PersistentClaudeStreamClient._parse_assistant_chunks(data) == ""

    def test_handles_empty_content(self):
        data = {"type": "assistant", "message": {"content": []}}
        assert PersistentClaudeStreamClient._parse_assistant_chunks(data) == ""


# ---------------------------------------------------------------------------
# _parse_assistant_text (parse_response + heuristic fallback)
# ---------------------------------------------------------------------------


class TestParseAssistantText:
    """Verify _parse_assistant_text with structured JSON and heuristic fallback."""

    def test_parses_json_response(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        text = '{"safetyScore": 85, "reasoning": "ok", "category": "safe"}'
        result = client._parse_assistant_text(text)
        assert result.success is True
        assert result.safety_score == 85
        assert result.category == "safe"

    def test_falls_back_to_heuristic(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        text = "This operation is safe and harmless. Standard routine."
        result = client._parse_assistant_text(text)
        assert result.success is True
        assert result.safety_score >= 80

    def test_returns_failure_for_empty(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        result = client._parse_assistant_text("")
        assert result.success is False


# ---------------------------------------------------------------------------
# Command args building
# ---------------------------------------------------------------------------


class TestBuildCommandArgs:
    """Verify _build_command_args produces the right CLI flags."""

    def test_includes_stream_json_flags(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        args = client._build_command_args()
        assert "--output-format" in args
        assert "stream-json" in args
        assert "--input-format" in args
        assert args[args.index("--output-format") + 1] == "stream-json"
        assert args[args.index("--input-format") + 1] == "stream-json"

    def test_includes_verbose_flag(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        args = client._build_command_args()
        assert "--verbose" in args

    def test_includes_security_flags(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        args = client._build_command_args()
        assert "--dangerously-skip-permissions" in args
        assert "--no-session-persistence" in args
        assert "--settings" in args

    def test_includes_model(self):
        config = LlmConfig(provider="claude-stream", model="opus")
        client = PersistentClaudeStreamClient(config=config)
        args = client._build_command_args()
        model_idx = args.index("--model") + 1
        assert args[model_idx] == "claude-opus-4-6-20250918"

    def test_includes_system_prompt(self):
        config = LlmConfig(provider="claude-stream", model="sonnet", system_prompt="Be safe")
        client = PersistentClaudeStreamClient(config=config)
        args = client._build_command_args()
        assert "--system-prompt" in args
        sp_idx = args.index("--system-prompt") + 1
        assert args[sp_idx] == "Be safe"

    def test_no_system_prompt_when_none(self):
        config = LlmConfig(provider="claude-stream", model="sonnet", system_prompt=None)
        client = PersistentClaudeStreamClient(config=config)
        args = client._build_command_args()
        assert "--system-prompt" not in args

    def test_settings_disables_hooks_and_mcp(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        args = client._build_command_args()
        settings_idx = args.index("--settings") + 1
        settings = json.loads(args[settings_idx])
        assert settings["disableAllHooks"] is True
        assert settings["enableAllProjectMcpServers"] is False
        assert settings["enableMcpServerCreation"] is False


# ---------------------------------------------------------------------------
# Process lifecycle (mocked subprocess)
# ---------------------------------------------------------------------------


class TestProcessLifecycle:
    """Verify process start, query, and failure handling."""

    @pytest.fixture
    def llm_config(self) -> LlmConfig:
        return LlmConfig(provider="claude-stream", model="sonnet")

    async def test_query_with_result_line(self, llm_config: LlmConfig):
        """Test a successful query that gets a result line with JSON."""
        inner_json = '{"safetyScore": 85, "reasoning": "ok", "category": "safe"}'

        stdout_lines = [
            _stream_assistant("thinking..."),
            _stream_result(inner_json),
            b"",
        ]

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)

        with _patch_stream_asyncio(mock_proc):
            client = PersistentClaudeStreamClient(config=llm_config)
            response = await client.query("test prompt")

        assert response.success is True
        assert response.safety_score == 85
        assert response.category == "safe"

    async def test_query_uses_assistant_chunks_when_result_empty(self, llm_config: LlmConfig):
        """When result field is None, fall back to collected assistant text."""
        inner_json = '{"safetyScore": 70, "reasoning": "cautious", "category": "cautious"}'

        stdout_lines = [
            _stream_assistant(inner_json),
            # Result with no result field
            json.dumps({"type": "result", "subtype": "success"}).encode() + b"\n",
            b"",
        ]

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)

        with _patch_stream_asyncio(mock_proc):
            client = PersistentClaudeStreamClient(config=llm_config)
            response = await client.query("test prompt")

        assert response.success is True
        assert response.safety_score == 70

    async def test_fallback_on_process_failure(self, llm_config: LlmConfig):
        """When the process exits immediately, fall back to one-shot client."""
        mock_proc = _make_mock_proc(returncode=1)

        mock_fallback = AsyncMock()
        mock_fallback.query = AsyncMock(return_value=LLMResponse(
            success=True, safety_score=50, reasoning="fallback", category="cautious",
        ))

        with _patch_stream_asyncio(mock_proc):
            client = PersistentClaudeStreamClient(config=llm_config)
            client._fallback_client = mock_fallback
            response = await client.query("test prompt")

        assert response.success is True
        assert response.reasoning == "fallback"

    async def test_fallback_on_stdout_eof(self, llm_config: LlmConfig):
        """When stdout closes unexpectedly, fall back to one-shot client."""
        stdout_lines = [b""]  # Immediate EOF

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)

        mock_fallback = AsyncMock()
        mock_fallback.query = AsyncMock(return_value=LLMResponse(
            success=True, safety_score=60, reasoning="eof fallback", category="cautious",
        ))

        with _patch_stream_asyncio(mock_proc):
            client = PersistentClaudeStreamClient(config=llm_config)
            # Manually set process as alive so it tries to query
            client._process = mock_proc
            client._fallback_client = mock_fallback
            response = await client.query("test prompt")

        assert response.success is True
        assert response.reasoning == "eof fallback"


# ---------------------------------------------------------------------------
# Dispose
# ---------------------------------------------------------------------------


class TestDispose:
    """Verify dispose prevents further queries."""

    async def test_dispose_prevents_further_queries(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        await client.dispose()
        with pytest.raises(RuntimeError, match="disposed"):
            await client.query("should fail")

    async def test_dispose_terminates_process(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)

        mock_proc = _make_mock_proc()
        mock_proc.returncode = None
        client._process = mock_proc

        await client.dispose()
        assert client._disposed is True
        assert client._process is None
        mock_proc.terminate.assert_called_once()

    async def test_double_dispose_is_safe(self):
        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        await client.dispose()
        await client.dispose()  # Should not raise


# ---------------------------------------------------------------------------
# Provider registry integration
# ---------------------------------------------------------------------------


class TestProviderRegistration:
    """Verify claude-stream is registered in the LLM client provider."""

    def test_claude_stream_in_factories(self):
        from leash.config import ConfigurationManager, create_default_configuration
        from leash.services.llm_client_provider import LLMClientProvider

        config_mgr = ConfigurationManager(config=create_default_configuration())
        provider = LLMClientProvider(config_manager=config_mgr)
        assert "claude-stream" in provider._factories

    async def test_creates_correct_client_type(self):
        from leash.config import ConfigurationManager, create_default_configuration
        from leash.services.llm_client_provider import LLMClientProvider

        config = create_default_configuration()
        config.llm.provider = "claude-stream"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client = await provider.get_client()
        assert isinstance(client, PersistentClaudeStreamClient)
        await provider.dispose()
