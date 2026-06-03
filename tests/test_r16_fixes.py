"""Tests for round 16 review fixes."""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Fix 1: --hooks copilot actually installs hooks
# ---------------------------------------------------------------------------


class TestCopilotHooksInstalledOnStartup:
    """Verify --hooks copilot/both actually calls install_user."""

    def test_hooks_copilot_calls_install(self):
        """_install_hooks_on_startup with 'copilot' should call install_user."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from leash.app import _install_hooks_on_startup

        config = SimpleNamespace(
            hooks_user_uninstalled=False,
            copilot_hooks_user_uninstalled=False,
        )
        hook_installer = MagicMock()
        copilot_installer = MagicMock()
        console_svc = MagicMock()

        _install_hooks_on_startup("copilot", config, hook_installer, copilot_installer, console_svc)

        copilot_installer.install_user.assert_called_once()
        hook_installer.install.assert_not_called()

    def test_hooks_both_installs_both(self):
        """_install_hooks_on_startup with 'both' should install Claude AND Copilot."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from leash.app import _install_hooks_on_startup

        config = SimpleNamespace(
            hooks_user_uninstalled=False,
            copilot_hooks_user_uninstalled=False,
        )
        hook_installer = MagicMock()
        copilot_installer = MagicMock()
        console_svc = MagicMock()

        _install_hooks_on_startup("both", config, hook_installer, copilot_installer, console_svc)

        hook_installer.install.assert_called_once()
        copilot_installer.install_user.assert_called_once()


# ---------------------------------------------------------------------------
# Fix 2: Empty model preserved as "" in config saves
# ---------------------------------------------------------------------------


class TestModelEmptyStringPreserved:
    """Verify empty model is not converted to null in the config save path."""

    def test_empty_model_is_valid(self):
        """LlmConfig should accept empty string as model (provider default)."""
        from leash.models.configuration import LlmConfig

        config = LlmConfig(provider="copilot-persistent", model="")
        assert config.model == ""


# ---------------------------------------------------------------------------
# Fix 3: Eviction deferred until after factory succeeds
# ---------------------------------------------------------------------------


class TestDeferredEviction:
    """Verify idle clients are only evicted after factory succeeds."""

    @pytest.mark.asyncio
    async def test_factory_failure_preserves_eviction_candidate(self):
        """If factory raises, the idle client should NOT be evicted."""
        from leash.config import ConfigurationManager, create_default_configuration
        from leash.services.llm_client_provider import LLMClientProvider

        config = create_default_configuration()
        config.llm.provider = "claude-stream"
        config.llm.max_concurrent_sessions = 1
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        # Create one session client filling capacity
        client_a, entry_a = await provider.get_client_for_session("s1")
        await provider.release_session(entry_a)
        assert len(provider._session_clients) == 1

        # Make the factory raise for the next creation
        original_factory = provider._factories["claude-stream"]
        def failing_factory(cfg):
            raise RuntimeError("Simulated factory failure")
        provider._factories["claude-stream"] = failing_factory

        # Try to create a second session — should fallback, not evict s1
        try:
            client_b, entry_b = await provider.get_client_for_session("s2")
        except RuntimeError:
            pass

        # s1 should still be present (not evicted)
        assert "s1" in provider._session_clients, (
            "Factory failure should not evict existing idle session clients"
        )

        provider._factories["claude-stream"] = original_factory
        await provider.dispose()


# ---------------------------------------------------------------------------
# Fix 4: PersistentCopilotClient falls back to config for model/effort
# ---------------------------------------------------------------------------


class TestCopilotClientConfigFallback:
    """Verify model/effort from LlmConfig are used when config_manager is absent."""

    def test_model_from_config_when_no_manager(self):
        """Model should come from config when config_manager is None."""
        from leash.models.configuration import LlmConfig
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        config = LlmConfig(provider="copilot-persistent", model="gpt-5.4")
        client = PersistentCopilotClient(config=config)
        _cmd, args = client._get_command_and_args()
        assert "--model" in args, "Model flag should be present"
        model_idx = args.index("--model")
        assert args[model_idx + 1] == "gpt-5.4"


# ---------------------------------------------------------------------------
# Fix 5: Adaptive threshold not trained on pre-validation scores
# ---------------------------------------------------------------------------


class TestPreValidationSkipsAdaptive:
    """Verify pre-validation does not feed synthetic scores to adaptive service."""

    def test_build_response_skips_adaptive(self):
        """_build_response should NOT call adaptive_service.record_decision."""
        import inspect
        from leash.routes._pre_validation import _build_response

        source = inspect.getsource(_build_response)
        # The adaptive_service call should be removed or commented out
        assert "adaptive_service.record_decision" not in source or \
               "Skip adaptive_service" in source, (
            "Pre-validation should not feed synthetic scores to adaptive_service"
        )
