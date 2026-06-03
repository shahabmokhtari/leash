"""Tests for hook install/uninstall decision persistence."""

from __future__ import annotations

from leash.models.configuration import Configuration


class TestHookDecisionPersistence:
    def test_default_hooks_not_uninstalled(self):
        config = Configuration()
        assert config.hooks_user_uninstalled is False

    def test_hooks_user_uninstalled_serializes(self):
        config = Configuration(hooks_user_uninstalled=True)
        data = config.model_dump(by_alias=True)
        assert data["hooksUserUninstalled"] is True

    def test_hooks_user_uninstalled_deserializes(self):
        data = {"hooksUserUninstalled": True}
        config = Configuration.model_validate(data)
        assert config.hooks_user_uninstalled is True

    def test_hooks_user_uninstalled_round_trip(self):
        config = Configuration(hooks_user_uninstalled=True)
        data = config.model_dump(by_alias=True)
        restored = Configuration.model_validate(data)
        assert restored.hooks_user_uninstalled is True

    def test_missing_field_defaults_false(self):
        data = {}
        config = Configuration.model_validate(data)
        assert config.hooks_user_uninstalled is False
