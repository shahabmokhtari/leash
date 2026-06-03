"""Tests for ProfileService."""

from __future__ import annotations

from pathlib import Path

from leash.config import ConfigurationManager
from leash.models import Configuration
from leash.models.permission_profile import BUILTIN_PROFILES, PermissionProfile

# ---------------------------------------------------------------------------
# ProfileService stub
# ---------------------------------------------------------------------------


class ProfileService:
    """Manages permission profiles and tool-specific threshold overrides."""

    def __init__(self, config_manager: ConfigurationManager):
        self._config_manager = config_manager

    def get_active_profile_key(self) -> str:
        return self._config_manager.get_configuration().profiles.active_profile

    def get_active_profile(self) -> PermissionProfile:
        key = self.get_active_profile_key()
        return BUILTIN_PROFILES.get(key, BUILTIN_PROFILES["moderate"])

    def get_threshold_for_tool(self, tool_name: str | None) -> int:
        profile = self.get_active_profile()
        if tool_name and tool_name in profile.threshold_overrides:
            return profile.threshold_overrides[tool_name]
        return profile.default_threshold

    def get_all_profiles(self) -> dict[str, PermissionProfile]:
        return dict(BUILTIN_PROFILES)

    def is_auto_approve_enabled(self) -> bool:
        return self.get_active_profile().auto_approve_enabled

    async def switch_profile(self, profile_key: str) -> bool:
        if profile_key not in BUILTIN_PROFILES:
            return False
        config = self._config_manager.get_configuration()
        config.profiles.active_profile = profile_key
        await self._config_manager.save()
        return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProfileService:
    def test_default_profile_is_moderate(self):
        config = Configuration()
        manager = ConfigurationManager(config=config)
        service = ProfileService(manager)

        profile = service.get_active_profile()
        assert profile.name == "Moderate"
        assert profile.default_threshold == 85

    def test_tool_specific_threshold_override(self):
        config = Configuration()
        manager = ConfigurationManager(config=config)
        service = ProfileService(manager)

        # Moderate profile has Bash=90
        bash_threshold = service.get_threshold_for_tool("Bash")
        unknown_threshold = service.get_threshold_for_tool("UnknownTool")

        assert bash_threshold == 90
        assert unknown_threshold == 85

    async def test_switch_profile(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        manager = ConfigurationManager(config_path=config_path)
        service = ProfileService(manager)

        success = await service.switch_profile("strict")
        assert success is True
        assert service.get_active_profile_key() == "strict"
        assert service.get_active_profile().name == "Strict"
        assert service.get_active_profile().default_threshold == 95

    async def test_switch_profile_invalid(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        manager = ConfigurationManager(config_path=config_path)
        service = ProfileService(manager)

        success = await service.switch_profile("nonexistent")
        assert success is False
        assert service.get_active_profile_key() == "moderate"

    def test_all_builtin_profiles_exist(self):
        config = Configuration()
        manager = ConfigurationManager(config=config)
        service = ProfileService(manager)

        profiles = service.get_all_profiles()
        assert "strict" in profiles
        assert "moderate" in profiles
        assert "permissive" in profiles
        assert "lockdown" in profiles

    def test_auto_approve_reflects_profile(self):
        config = Configuration()
        manager = ConfigurationManager(config=config)
        service = ProfileService(manager)

        # Moderate profile has auto_approve_enabled=True
        assert service.is_auto_approve_enabled() is True

    def test_lockdown_profile_disables_auto_approve(self):
        config = Configuration(profiles={"active_profile": "lockdown"})
        manager = ConfigurationManager(config=config)
        service = ProfileService(manager)

        assert service.is_auto_approve_enabled() is False
        assert service.get_active_profile().default_threshold == 100
