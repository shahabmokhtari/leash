"""Tests for round 15 review fixes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fix 1: asyncio.shield prevents CancelledError from leaking in_use counter
# ---------------------------------------------------------------------------


class TestShieldedSessionRelease:
    """Verify that release_session is shielded from CancelledError."""

    @pytest.mark.asyncio
    async def test_claude_hook_release_shielded(self):
        """release_session should complete even if the task is cancelled."""
        from leash.routes.claude_hook import handle_claude_hook

        # Verify the source code contains asyncio.shield around release_session
        import inspect
        source = inspect.getsource(handle_claude_hook)
        assert "asyncio.shield" in source, (
            "claude_hook.handle_claude_hook must use asyncio.shield around "
            "release_session to prevent CancelledError from leaking in_use"
        )

    @pytest.mark.asyncio
    async def test_copilot_hook_release_shielded(self):
        """release_session should complete even if the task is cancelled."""
        from leash.routes.copilot_hook import handle_copilot_hook

        import inspect
        source = inspect.getsource(handle_copilot_hook)
        assert "asyncio.shield" in source, (
            "copilot_hook.handle_copilot_hook must use asyncio.shield around "
            "release_session to prevent CancelledError from leaking in_use"
        )


# ---------------------------------------------------------------------------
# Fix 2: Switching from persistent to non-persistent invalidates sessions
# ---------------------------------------------------------------------------


class TestProviderSwitchInvalidation:
    """Verify session clients are cleaned up when switching provider types."""

    @pytest.mark.asyncio
    async def test_switch_from_persistent_invalidates_sessions(self):
        """When switching from a persistent to non-persistent provider,
        existing session clients should be invalidated."""
        from leash.config import ConfigurationManager, create_default_configuration
        from leash.services.llm_client_provider import LLMClientProvider

        # Start with claude-stream (persistent)
        config = create_default_configuration()
        config.llm.provider = "claude-stream"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # Create a session client
        client_a, entry_a = await provider.get_client_for_session("s1")
        assert entry_a is not None
        await provider.release_session(entry_a)

        # Verify session client exists
        assert len(provider._session_clients) == 1

        # Switch to non-persistent provider
        config.llm.provider = "anthropic-api"

        # get_client should trigger session invalidation
        await provider.get_client()

        # Session clients should be cleaned up (or scheduled for cleanup)
        # The settings_changed path should have fired
        assert len(provider._session_clients) == 0, (
            "Switching from persistent to non-persistent provider should "
            "invalidate existing session clients"
        )

        await provider.dispose()


# ---------------------------------------------------------------------------
# Fix 3: Copilot hooks lifecycle on shutdown — superseded.
# As of the user-driven hook lifecycle change, shutdown ALWAYS removes
# user-level Copilot runtime hooks (sessionStart is preserved by the
# installer because it is user-toggled separately).
# ---------------------------------------------------------------------------


class TestCopilotHooksRemovedOnShutdown:
    """Verify shutdown removes Copilot user-level runtime hooks."""

    def test_shutdown_removes_copilot_user_level_hooks(self):
        """The shutdown cleanup MUST call copilot_hook_installer.uninstall_user()."""
        import inspect
        from leash.app import lifespan

        source = inspect.getsource(lifespan)
        yield_idx = source.index("yield")
        shutdown_source = source[yield_idx:]

        assert "copilot_hook_installer.uninstall_user()" in shutdown_source, (
            "Shutdown must remove Copilot user-level runtime hooks so they "
            "don't linger and error out when Leash is not running."
        )


# ---------------------------------------------------------------------------
# Fix 4: _is_bootstrapping removed from stream client (dead code)
# ---------------------------------------------------------------------------


class TestStreamClientNoBootstrappingField:
    """Verify _is_bootstrapping is not used in PersistentClaudeStreamClient."""

    def test_no_is_bootstrapping_attribute(self):
        """PersistentClaudeStreamClient should not have _is_bootstrapping."""
        from leash.models.configuration import LlmConfig
        from leash.services.persistent_claude_stream_client import PersistentClaudeStreamClient

        config = LlmConfig(provider="claude-stream", model="sonnet")
        client = PersistentClaudeStreamClient(config=config)

        assert not hasattr(client, "_is_bootstrapping"), (
            "_is_bootstrapping is dead code in PersistentClaudeStreamClient — "
            "it should be removed"
        )

    def test_kill_process_does_not_set_bootstrapping(self):
        """_kill_process should not reference _is_bootstrapping."""
        import inspect
        from leash.services.persistent_claude_stream_client import PersistentClaudeStreamClient

        source = inspect.getsource(PersistentClaudeStreamClient._kill_process)
        assert "_is_bootstrapping" not in source, (
            "_kill_process should not set _is_bootstrapping — it's dead code"
        )
