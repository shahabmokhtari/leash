"""Tests for ConfigurationManager."""

from __future__ import annotations

from pathlib import Path

from leash.config import ConfigurationManager, create_default_configuration
from leash.models import Configuration, HandlerConfig, HookEventConfig


class TestConfigurationManager:
    async def test_load_creates_default_config_when_file_not_exists(self, tmp_path: Path):
        """Loading from a non-existent path should produce default config."""
        config_path = tmp_path / "nonexistent" / "config.json"
        manager = ConfigurationManager(config_path=config_path)
        config = await manager.load()

        assert config is not None
        assert config.llm.provider == "claude-stream"
        assert config.server.port == 5050
        # File should have been created
        assert config_path.exists()

    async def test_load_from_disk(self, tmp_path: Path):
        """Write a config, then load it back."""
        config_path = tmp_path / "config.json"
        manager = ConfigurationManager(config_path=config_path)

        # Save default
        await manager.load()
        # Modify and save
        cfg = manager.get_configuration()
        cfg.server.port = 9999
        await manager.update(cfg)

        # Load in a new manager
        manager2 = ConfigurationManager(config_path=config_path)
        loaded = await manager2.load()
        assert loaded.server.port == 9999

    async def test_save_creates_directory_if_missing(self, tmp_path: Path):
        """Save should create the parent directory if it does not exist."""
        config_path = tmp_path / "deep" / "nested" / "config.json"
        manager = ConfigurationManager(config_path=config_path)
        await manager.save()
        assert config_path.exists()

    def test_default_config_has_handlers(self):
        """Default config should have built-in handlers."""
        config = create_default_configuration()
        assert "PreToolUse" in config.hook_handlers
        assert len(config.hook_handlers["PreToolUse"].handlers) > 0
        assert "SessionStart" in config.hook_handlers
        assert len(config.hook_handlers["SessionStart"].handlers) > 0

    def test_get_handlers_for_hook(self):
        """Should return matching handlers for a hook event."""
        config = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    enabled=True,
                    handlers=[
                        HandlerConfig(name="bash-analyzer", matcher="Bash"),
                        HandlerConfig(name="file-read", matcher="Read"),
                    ],
                )
            }
        )
        manager = ConfigurationManager(config=config)
        handlers = manager.get_handlers_for_hook("PermissionRequest")
        assert len(handlers) == 2
        assert any(h.name == "bash-analyzer" for h in handlers)

    def test_get_handlers_for_hook_returns_empty_when_disabled(self):
        config = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    enabled=False,
                    handlers=[HandlerConfig(name="bash", matcher="Bash")],
                )
            }
        )
        manager = ConfigurationManager(config=config)
        assert manager.get_handlers_for_hook("PermissionRequest") == []

    def test_get_handlers_for_hook_returns_empty_for_unknown(self):
        config = Configuration(hook_handlers={})
        manager = ConfigurationManager(config=config)
        assert manager.get_handlers_for_hook("NonExistent") == []

    def test_find_matching_handler(self):
        """Should find the correct handler by tool name."""
        config = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    handlers=[
                        HandlerConfig(name="bash", matcher="Bash"),
                        HandlerConfig(name="write", matcher="Write|Edit"),
                    ]
                )
            }
        )
        manager = ConfigurationManager(config=config)

        handler = manager.find_matching_handler("PermissionRequest", "Edit")
        assert handler is not None
        assert handler.name == "write"

    def test_find_matching_handler_with_provider(self):
        """Provider-specific handlers should take precedence."""
        config = Configuration(
            hook_handlers={
                "PreToolUse": HookEventConfig(
                    handlers=[
                        HandlerConfig(name="copilot-handler", matcher="*", client="copilot"),
                        HandlerConfig(name="default-handler", matcher="*"),
                    ]
                )
            }
        )
        manager = ConfigurationManager(config=config)

        handler = manager.find_matching_handler("PreToolUse", "Bash", provider="copilot")
        assert handler is not None
        assert handler.name == "copilot-handler"

        handler2 = manager.find_matching_handler("PreToolUse", "Bash", provider="claude")
        assert handler2 is not None
        assert handler2.name == "default-handler"

    def test_find_matching_handler_returns_none_when_no_match(self):
        config = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    handlers=[HandlerConfig(name="bash", matcher="Bash")]
                )
            }
        )
        manager = ConfigurationManager(config=config)
        assert manager.find_matching_handler("PermissionRequest", "Write") is None
