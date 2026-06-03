"""Tests for round 17 review fixes."""

from __future__ import annotations

import asyncio
import inspect

import pytest


class TestGetClientReturnSafety:
    """Verify callers of get_client() are protected from disposal race."""

    def test_debug_route_uses_query_not_get_client(self):
        """debug.py must use llm_provider.query() (ref-counted) not bare get_client()."""
        import inspect
        from leash.routes.debug import debug_llm

        source = inspect.getsource(debug_llm)
        assert "llm_provider.query(" in source, (
            "debug.py must use llm_provider.query() which has ref-counting, "
            "not bare get_client() + client.query()"
        )
        assert "get_client()" not in source, (
            "debug.py should not call bare get_client() — use query() instead"
        )


class TestDisposeSnapshotPattern:
    """Verify dispose() doesn't hold _session_lock during subprocess teardown."""

    def test_dispose_snapshots_under_lock(self):
        """dispose() should snapshot clients under lock, dispose outside."""
        from leash.services.llm_client_provider import LLMClientProvider

        source = inspect.getsource(LLMClientProvider.dispose)
        # The snapshot pattern uses list comprehensions under the lock
        assert "session_dispose" in source or "session_clients.values()" in source
        # Key invariant: _dispose_client should NOT appear between
        # "async with self._session_lock" and the matching clear()
        assert "session_dispose = [" in source, (
            "dispose() should snapshot session clients into a list under the lock"
        )

    @pytest.mark.asyncio
    async def test_dispose_clears_session_clients(self):
        """dispose() should clear all session clients."""
        from leash.config import ConfigurationManager, create_default_configuration
        from leash.services.llm_client_provider import LLMClientProvider

        config = create_default_configuration()
        config.llm.provider = "claude-stream"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # Create a session client
        client_a, entry_a = await provider.get_client_for_session("s1")
        await provider.release_session(entry_a)
        assert len(provider._session_clients) == 1

        # Dispose should clear everything
        await provider.dispose()
        assert len(provider._session_clients) == 0
        assert len(provider._pending_disposal) == 0
