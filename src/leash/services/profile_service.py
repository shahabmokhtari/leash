"""Permission profile management."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from leash.models.permission_profile import BUILTIN_PROFILES, PermissionProfile

if TYPE_CHECKING:
    from leash.config import ConfigurationManager

logger = logging.getLogger(__name__)


class ProfileService:
    """Manages permission profiles with built-in and custom profiles."""

    def __init__(self, config_manager: ConfigurationManager) -> None:
        self._config_manager = config_manager
        self._active_profile_key = "moderate"
        self._active_profile = BUILTIN_PROFILES["moderate"]

    async def initialize(self) -> None:
        """Load the active profile from config."""
        config = await self._config_manager.load()
        key = config.profiles.active_profile
        profile = self._try_get_profile(key, config.profiles.custom_profiles)
        if profile is not None:
            self._active_profile_key = key
            self._active_profile = profile
            logger.debug("Loaded permission profile: %s", key)

    def get_active_profile(self) -> PermissionProfile:
        """Return the currently active permission profile."""
        return self._active_profile

    def get_active_profile_key(self) -> str:
        """Return the key of the currently active profile."""
        return self._active_profile_key

    def get_threshold_for_tool(self, tool_name: str | None) -> int:
        """Get the threshold for a specific tool, checking overrides then default."""
        if tool_name and tool_name in self._active_profile.threshold_overrides:
            return self._active_profile.threshold_overrides[tool_name]
        return self._active_profile.default_threshold

    def is_auto_approve_enabled(self) -> bool:
        """Check if auto-approve is enabled in the active profile."""
        return self._active_profile.auto_approve_enabled

    async def switch_profile(self, profile_key: str) -> bool:
        """Switch to a different profile, persisting to config.

        Returns True on success, False if profile not found.
        """
        config = await self._config_manager.load()
        profile = self._try_get_profile(profile_key, config.profiles.custom_profiles)
        if profile is None:
            logger.warning("Profile not found: %s", profile_key)
            return False

        self._active_profile_key = profile_key
        self._active_profile = profile
        config.profiles.active_profile = profile_key
        await self._config_manager.save()

        logger.debug("Switched to permission profile: %s", profile_key)
        return True

    def get_all_profiles(self) -> dict[str, PermissionProfile]:
        """Return all profiles (built-in + custom)."""
        all_profiles = dict(BUILTIN_PROFILES)
        # Custom profiles could be loaded from config here
        return all_profiles

    @staticmethod
    def _try_get_profile(
        key: str, custom_profiles: dict[str, object] | None = None
    ) -> PermissionProfile | None:
        """Try to find a profile by key in built-in then custom profiles."""
        if key in BUILTIN_PROFILES:
            return BUILTIN_PROFILES[key]

        if custom_profiles and key in custom_profiles:
            profile_data = custom_profiles[key]
            if isinstance(profile_data, PermissionProfile):
                return profile_data
            if isinstance(profile_data, dict):
                return PermissionProfile.model_validate(profile_data)

        return None
