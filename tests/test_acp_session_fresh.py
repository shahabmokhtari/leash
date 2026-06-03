"""Tests for fresh-session-per-query ACP behaviour.

Verifies that ``_try_acp_query`` creates a new ACP session on every query
rather than reusing a cached session ID, and that the process is reused
across queries (only started once).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leash.models.configuration import LlmConfig


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_llm_providers.py)
# ---------------------------------------------------------------------------


def _acp_response(rpc_id: int, result: dict) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": result}).encode() + b"\n"


def _acp_text_update(text: str) -> bytes:
    return json.dumps({
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            }
        },
    }).encode() + b"\n"


def _make_mock_proc(*, returncode=None, stdout_lines=None):
    mock_proc = AsyncMock()
    mock_proc.returncode = returncode
    mock_proc.pid = 12345
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


def _patch_acp_asyncio(mock_proc):
    patcher = patch("leash.services.acp_client_base.asyncio")

    class _Ctx:
        def __enter__(self_ctx):
            mock_asyncio = patcher.__enter__()
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            mock_asyncio.sleep = AsyncMock()
            mock_asyncio.create_task = MagicMock(side_effect=lambda coro: asyncio.ensure_future(coro))
            mock_asyncio.CancelledError = asyncio.CancelledError
            mock_asyncio.to_thread = asyncio.to_thread
            return mock_asyncio

        def __exit__(self_ctx, *args):
            return patcher.__exit__(*args)

    return _Ctx()


@pytest.fixture(autouse=True)
def _reset_rpc_counter():
    """Reset and restore the global RPC counter for each test."""
    import leash.services.acp_client_base as acp_base
    old = acp_base._next_rpc_id
    acp_base._next_rpc_id = 0
    yield
    acp_base._next_rpc_id = old


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFreshSessionPerQuery:
    """Verify that each ACP query creates a fresh session."""

    @pytest.fixture
    def llm_config(self) -> LlmConfig:
        return LlmConfig(provider="claude-persistent", model="sonnet")

    async def test_session_reused_within_limit(self, llm_config: LlmConfig):
        """Consecutive queries should reuse the same session (no new session/new)."""
        from leash.services.persistent_claude_client import PersistentClaudeClient


        inner_json = '{"safetyScore": 85, "reasoning": "ok", "category": "safe"}'

        # First query: initialize(1) + session/new(2) + prompt(3)
        # Second query: reuses session → just prompt(4)
        stdout_lines = [
            # Query 1
            _acp_response(1, {"protocolVersion": 1}),     # initialize
            _acp_response(2, {"sessionId": "s-first"}),    # session/new
            _acp_text_update(inner_json),
            _acp_response(3, {"stopReason": "end_turn"}),  # prompt
            # Query 2 (reuses session — no session/new)
            _acp_text_update(inner_json),
            _acp_response(4, {"stopReason": "end_turn"}),  # prompt
            b"",
        ]

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)
        written_data: list[bytes] = []
        mock_proc.stdin.write = MagicMock(side_effect=lambda data: written_data.append(data))

        with _patch_acp_asyncio(mock_proc):
            client = PersistentClaudeClient(config=llm_config)

            r1 = await client.query("prompt 1")
            assert r1.success is True

            r2 = await client.query("prompt 2")
            assert r2.success is True

        # Only ONE session/new call (first query only)
        session_new_count = 0
        for data in written_data:
            try:
                msg = json.loads(data.decode())
                if msg.get("method") == "session/new":
                    session_new_count += 1
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        assert session_new_count == 1, f"Expected 1 session/new call, got {session_new_count}"

    async def test_session_refreshed_after_max_queries(self, llm_config: LlmConfig):
        """After _MAX_QUERIES_PER_SESSION queries, a fresh session is created."""
        from leash.services.persistent_claude_client import PersistentClaudeClient
        import leash.services.acp_client_base as acp_base


        # Set max to 2 for easy testing
        old_max = acp_base._MAX_QUERIES_PER_SESSION
        acp_base._MAX_QUERIES_PER_SESSION = 2

        try:
            inner_json = '{"safetyScore": 90, "reasoning": "ok", "category": "safe"}'

            # Query 1: init(1) + session/new(2) + prompt(3)
            # Query 2: reuses session → prompt(4)
            # Query 3: new session(5) + prompt(6) (limit reached)
            stdout_lines = [
                _acp_response(1, {"protocolVersion": 1}),
                _acp_response(2, {"sessionId": "s-1"}),
                _acp_text_update(inner_json),
                _acp_response(3, {"stopReason": "end_turn"}),
                _acp_text_update(inner_json),
                _acp_response(4, {"stopReason": "end_turn"}),
                _acp_response(5, {"sessionId": "s-2"}),
                _acp_text_update(inner_json),
                _acp_response(6, {"stopReason": "end_turn"}),
                b"",
            ]

            mock_proc = _make_mock_proc(stdout_lines=stdout_lines)
            written_data: list[bytes] = []
            mock_proc.stdin.write = MagicMock(side_effect=lambda data: written_data.append(data))

            with _patch_acp_asyncio(mock_proc):
                client = PersistentClaudeClient(config=llm_config)

                await client.query("prompt 1")
                await client.query("prompt 2")
                await client.query("prompt 3")

            session_new_count = 0
            for data in written_data:
                try:
                    msg = json.loads(data.decode())
                    if msg.get("method") == "session/new":
                        session_new_count += 1
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            assert session_new_count == 2, f"Expected 2 session/new calls, got {session_new_count}"
        finally:
            acp_base._MAX_QUERIES_PER_SESSION = old_max

    async def test_process_reused_across_queries(self, llm_config: LlmConfig):
        """Process should be started only once across multiple queries."""
        from leash.services.persistent_claude_client import PersistentClaudeClient


        inner_json = '{"safetyScore": 90, "reasoning": "ok", "category": "safe"}'

        stdout_lines = [
            _acp_response(1, {"protocolVersion": 1}),
            _acp_response(2, {"sessionId": "s-1"}),
            _acp_text_update(inner_json),
            _acp_response(3, {"stopReason": "end_turn"}),
            _acp_text_update(inner_json),
            _acp_response(4, {"stopReason": "end_turn"}),
            b"",
        ]

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)

        with _patch_acp_asyncio(mock_proc) as mock_asyncio:
            client = PersistentClaudeClient(config=llm_config)

            await client.query("prompt 1")
            await client.query("prompt 2")

            # create_subprocess_exec should have been called exactly once
            assert mock_asyncio.create_subprocess_exec.call_count == 1

    async def test_session_new_failure_returns_none_without_killing(self, llm_config: LlmConfig):
        """If session/new fails, the query should return None but keep the process alive."""
        from leash.services.persistent_claude_client import PersistentClaudeClient


        # initialize succeeds, session/new returns error
        stdout_lines = [
            _acp_response(1, {"protocolVersion": 1}),   # initialize
            json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "nope"}}).encode() + b"\n",
            b"",
        ]

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)

        mock_fallback = AsyncMock()
        mock_fallback.query = AsyncMock(return_value=MagicMock(
            success=True, safety_score=50, reasoning="fallback", category="cautious",
        ))

        with _patch_acp_asyncio(mock_proc):
            client = PersistentClaudeClient(config=llm_config)
            client._fallback_client = mock_fallback

            result = await client.query("test")

            # Should have fallen back
            assert result.reasoning == "fallback"

            # Process should still be alive (not terminated)
            assert client._process is not None
            mock_proc.terminate.assert_not_called()

    async def test_failure_invalidates_session(self, llm_config: LlmConfig):
        """After a prompt timeout, the session should be invalidated for next query."""
        from leash.services.persistent_claude_client import PersistentClaudeClient

        client = PersistentClaudeClient(config=llm_config)
        # Simulate a session was established
        client._session_id = "old-session"
        client._session_query_count = 3

        # After kill_process, session should be cleared
        await client._kill_process()
        assert client._session_id is None
        assert client._session_query_count == 0

    async def test_initialized_flag_starts_false(self, llm_config: LlmConfig):
        """Verify _initialized flag starts False before process launch."""
        from leash.services.persistent_claude_client import PersistentClaudeClient

        client = PersistentClaudeClient(config=llm_config)
        assert client._initialized is False


class TestSessionMeta:
    """Verify _meta is passed in session/new to minimise latency."""

    def test_claude_build_session_meta_disables_tools(self):
        from leash.services.persistent_claude_client import PersistentClaudeClient

        config = LlmConfig(provider="claude-persistent", model="sonnet")
        client = PersistentClaudeClient(config=config)
        meta = client._build_session_meta()

        assert meta is not None
        assert meta["disableBuiltInTools"] is True

    def test_claude_build_session_meta_overrides_system_prompt(self):
        from leash.services.persistent_claude_client import PersistentClaudeClient

        config = LlmConfig(provider="claude-persistent", model="sonnet")
        client = PersistentClaudeClient(config=config)
        meta = client._build_session_meta()

        # Should use our minimal system prompt, not Claude Code's full preset
        assert isinstance(meta["systemPrompt"], str)
        assert "security" in meta["systemPrompt"].lower()

    def test_claude_build_session_meta_sets_max_turns(self):
        from leash.services.persistent_claude_client import PersistentClaudeClient

        config = LlmConfig(provider="claude-persistent", model="sonnet")
        client = PersistentClaudeClient(config=config)
        meta = client._build_session_meta()

        assert meta["claudeCode"]["options"]["maxTurns"] == 1

    def test_claude_build_session_meta_includes_model(self):
        from leash.services.persistent_claude_client import PersistentClaudeClient

        config = LlmConfig(provider="claude-persistent", model="opus")
        client = PersistentClaudeClient(config=config)
        meta = client._build_session_meta()

        assert "model" in meta["claudeCode"]["options"]
        assert "opus" in meta["claudeCode"]["options"]["model"]

    def test_copilot_build_session_meta_returns_none(self):
        """Copilot ACP doesn't support _meta, so it should return None."""
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        config = LlmConfig(provider="copilot-persistent", model="sonnet")
        client = PersistentCopilotClient(config=config)
        meta = client._build_session_meta()

        assert meta is None

    async def test_session_new_includes_meta(self):
        """Verify session/new request includes _meta when provided."""
        from leash.services.persistent_claude_client import PersistentClaudeClient


        inner_json = '{"safetyScore": 85, "reasoning": "ok", "category": "safe"}'
        stdout_lines = [
            _acp_response(1, {"protocolVersion": 1}),
            _acp_response(2, {"sessionId": "s-1"}),
            _acp_text_update(inner_json),
            _acp_response(3, {"stopReason": "end_turn"}),
            b"",
        ]

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)
        written_data: list[bytes] = []
        mock_proc.stdin.write = MagicMock(side_effect=lambda data: written_data.append(data))

        config = LlmConfig(provider="claude-persistent", model="sonnet")
        with _patch_acp_asyncio(mock_proc):
            client = PersistentClaudeClient(config=config)
            await client.query("test")

        # Find session/new and verify _meta is included
        for data in written_data:
            try:
                msg = json.loads(data.decode())
                if msg.get("method") == "session/new":
                    params = msg["params"]
                    assert "_meta" in params, "session/new should include _meta"
                    assert params["_meta"]["disableBuiltInTools"] is True
                    assert isinstance(params["_meta"]["systemPrompt"], str)
                    break
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        else:
            pytest.fail("session/new not found in written data")
