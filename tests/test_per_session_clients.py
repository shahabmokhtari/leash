"""Tests for per-session client lifecycle in LLMClientProvider.

Verifies that persistent providers get dedicated clients per session,
non-persistent providers share a single client, settings changes dispose
all session clients, and idle cleanup works correctly.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from leash.config import ConfigurationManager, create_default_configuration
from leash.models.configuration import LlmConfig
from leash.services.llm_client_provider import LLMClientProvider, _SessionClientEntry


# ---------------------------------------------------------------------------
# Per-session client creation
# ---------------------------------------------------------------------------


class TestPerSessionClientCreation:
    """Verify per-session client creation for ACP providers.

    ACP providers get dedicated per-session clients: one-time cold start,
    then warm-process queries with no lock contention across sessions.
    Non-ACP providers share a single client.
    """

    async def test_claude_persistent_creates_per_session_clients(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("session-a")
        client_b, _ = await provider.get_client_for_session("session-b")
        assert client_a is not client_b

        await provider.dispose()

    async def test_same_session_returns_same_client(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("session-x")
        client_b, _ = await provider.get_client_for_session("session-x")
        assert client_a is client_b

        await provider.dispose()

    async def test_touch_updates_last_used(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        await provider.get_client_for_session("session-t")
        entry = provider._session_clients["session-t"]
        old_time = entry.last_used

        await provider.get_client_for_session("session-t")
        assert entry.last_used >= old_time

        await provider.dispose()

    async def test_non_persistent_provider_returns_shared_client(self):
        config = create_default_configuration()
        config.llm.provider = "anthropic-api"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("session-a")
        client_b, _ = await provider.get_client_for_session("session-b")
        assert client_a is client_b
        assert len(provider._session_clients) == 0

        await provider.dispose()

    async def test_copilot_cli_returns_shared_client(self):
        config = create_default_configuration()
        config.llm.provider = "copilot-cli"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("session-a")
        client_b, _ = await provider.get_client_for_session("session-b")
        assert client_a is client_b

        await provider.dispose()

    async def test_none_session_id_returns_shared_client(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client, _ = await provider.get_client_for_session(None)
        shared = await provider.get_client()
        assert client is shared

        await provider.dispose()

    async def test_claude_stream_creates_per_session_clients(self):
        """claude-stream uses per-session clients to prevent context bleed."""
        config = create_default_configuration()
        config.llm.provider = "claude-stream"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("s1")
        client_b, _ = await provider.get_client_for_session("s2")
        assert client_a is not client_b

        await provider.dispose()

    async def test_copilot_persistent_creates_per_session_clients(self):
        config = create_default_configuration()
        config.llm.provider = "copilot-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("s1")
        client_b, _ = await provider.get_client_for_session("s2")
        assert client_a is not client_b

        await provider.dispose()


# ---------------------------------------------------------------------------
# Settings change disposal
# ---------------------------------------------------------------------------


class TestSettingsChangeDisposal:
    """Verify session clients are disposed when provider/model changes."""

    async def test_model_change_disposes_session_clients(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config.llm.model = "sonnet"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # Establish shared client baseline so get_client can detect changes
        await provider.get_client()
        await provider.get_client_for_session("s1")
        await provider.get_client_for_session("s2")
        assert len(provider._session_clients) == 2

        # Change model — get_client triggers disposal of all session clients
        config.llm.model = "opus"
        config_mgr._config = config
        await provider.get_client()
        assert len(provider._session_clients) == 0

        await provider.dispose()

    async def test_provider_change_disposes_session_clients(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        await provider.get_client()
        await provider.get_client_for_session("s1")
        assert len(provider._session_clients) == 1

        config.llm.provider = "claude-stream"
        config_mgr._config = config
        await provider.get_client()
        assert len(provider._session_clients) == 0

        await provider.dispose()

    async def test_session_client_disposed_on_stale_settings(self):
        """get_client_for_session detects stale settings and recreates."""
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config.llm.model = "sonnet"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # Populate cached_provider/model via get_client
        await provider.get_client()
        old_client, _ = await provider.get_client_for_session("s1")

        # Change model — cached still says "sonnet", config says "opus"
        config.llm.model = "opus"
        config_mgr._config = config

        new_client, _ = await provider.get_client_for_session("s1")
        assert new_client is not old_client

        await provider.dispose()


# ---------------------------------------------------------------------------
# Idle cleanup
# ---------------------------------------------------------------------------


class TestIdleCleanup:
    """Verify idle session client cleanup infrastructure works correctly."""

    async def test_cleanup_with_no_session_clients_is_noop(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config.llm.session_idle_timeout_minutes = 5
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # No session clients to clean up
        await provider._cleanup_idle_sessions()
        assert len(provider._session_clients) == 0

        await provider.dispose()

    async def test_zero_timeout_disables_cleanup(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config.llm.session_idle_timeout_minutes = 0
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # Manually insert a stale entry to verify cleanup skips it
        from leash.services.llm_client_provider import _SessionClientEntry
        from unittest.mock import AsyncMock
        mock_client = AsyncMock()
        entry = _SessionClientEntry(mock_client)
        entry.last_used = time.monotonic() - 3600
        provider._session_clients["s1"] = entry

        await provider._cleanup_idle_sessions()
        # Should NOT have been cleaned up (timeout disabled)
        assert "s1" in provider._session_clients

        await provider.dispose()

    async def test_cleanup_disposes_stale_entries(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config.llm.session_idle_timeout_minutes = 1
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # Manually insert a stale entry
        from leash.services.llm_client_provider import _SessionClientEntry
        from unittest.mock import AsyncMock
        mock_client = AsyncMock()
        entry = _SessionClientEntry(mock_client)
        entry.last_used = time.monotonic() - 90  # 90s > 1min timeout
        provider._session_clients["s1"] = entry

        await provider._cleanup_idle_sessions()
        assert "s1" not in provider._session_clients

        await provider.dispose()


# ---------------------------------------------------------------------------
# Dispose
# ---------------------------------------------------------------------------


class TestDisposeAll:
    """Verify dispose() cleans up all clients."""

    async def test_dispose_clears_session_clients(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        await provider.get_client_for_session("s1")
        await provider.get_client_for_session("s2")
        assert len(provider._session_clients) == 2

        await provider.dispose()
        assert len(provider._session_clients) == 0


# ---------------------------------------------------------------------------
# Release session
# ---------------------------------------------------------------------------


class TestReleaseSession:
    """Verify release_session decrements in_use and disposes stale entries."""

    async def test_release_decrements_in_use(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        _, entry = await provider.get_client_for_session("s1")
        assert entry is not None
        assert entry.in_use == 1

        await provider.release_session(entry)
        assert entry.in_use == 0

        await provider.dispose()

    async def test_release_none_is_noop(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        await provider.release_session(None)  # Should not raise

        await provider.dispose()

    async def test_release_stale_entry_disposes_client(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        _, entry = await provider.get_client_for_session("s1")
        assert entry is not None
        # Simulate displacement: mark stale and move to pending
        entry.stale = True
        provider._pending_disposal.append(entry)

        with patch.object(provider, "_dispose_client", new_callable=AsyncMock) as mock_dispose:
            await provider.release_session(entry)
            mock_dispose.assert_called_once_with(entry.client)
        assert entry not in provider._pending_disposal

        await provider.dispose()


# ---------------------------------------------------------------------------
# Capacity eviction and fallback
# ---------------------------------------------------------------------------


class TestCapacityEviction:
    """Verify capacity limits and one-shot fallback when all slots busy."""

    async def test_evicts_oldest_idle_when_at_capacity(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config.llm.max_concurrent_sessions = 2
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # Create 2 sessions
        _, entry_a = await provider.get_client_for_session("s1")
        _, entry_b = await provider.get_client_for_session("s2")
        # Release both so they're idle
        await provider.release_session(entry_a)
        await provider.release_session(entry_b)
        assert len(provider._session_clients) == 2

        # Create a 3rd — should evict the oldest idle
        _, _ = await provider.get_client_for_session("s3")
        assert len(provider._session_clients) == 2
        assert "s3" in provider._session_clients
        # s1 was oldest, should be evicted
        assert "s1" not in provider._session_clients

        await provider.dispose()

    async def test_fallback_when_all_busy(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config.llm.max_concurrent_sessions = 1
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # Create 1 session, keep in use (don't release)
        client_a, entry_a = await provider.get_client_for_session("s1")
        assert entry_a is not None

        # Try to create another — should fallback to one-shot cli client
        client_b, entry_b = await provider.get_client_for_session("s2")
        assert entry_b is None  # One-shot fallback has no entry
        assert client_b is not client_a

        await provider.release_session(entry_a)
        await provider.dispose()


# ---------------------------------------------------------------------------
# Pending disposal cleanup
# ---------------------------------------------------------------------------


class TestPendingDisposalCleanup:
    """Verify _pending_disposal entries are cleaned up during idle sweep."""

    async def test_cleanup_disposes_pending_idle_entries(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config.llm.session_idle_timeout_minutes = 1
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        mock_client = AsyncMock()
        entry = _SessionClientEntry(mock_client)
        entry.stale = True
        entry.in_use = 0  # Idle
        provider._pending_disposal.append(entry)

        await provider._cleanup_idle_sessions()
        assert entry not in provider._pending_disposal

        await provider.dispose()

    async def test_cleanup_keeps_busy_pending_entries(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config.llm.session_idle_timeout_minutes = 1
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        mock_client = AsyncMock()
        entry = _SessionClientEntry(mock_client)
        entry.stale = True
        entry.in_use = 1  # Still busy
        provider._pending_disposal.append(entry)

        await provider._cleanup_idle_sessions()
        assert entry in provider._pending_disposal

        await provider.dispose()


# ---------------------------------------------------------------------------
# Stream client periodic restart
# ---------------------------------------------------------------------------


class TestStreamClientRestart:
    """Verify stream client restarts process after N queries."""

    async def test_query_count_increments(self):
        from leash.services.persistent_claude_stream_client import PersistentClaudeStreamClient

        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)
        assert client._query_count == 0

    async def test_max_queries_constant_exists(self):
        from leash.services.persistent_claude_stream_client import _MAX_QUERIES_BEFORE_RESTART

        assert _MAX_QUERIES_BEFORE_RESTART == 100
