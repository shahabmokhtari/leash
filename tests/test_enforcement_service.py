"""Tests for EnforcementService."""

from __future__ import annotations

from pathlib import Path

import pytest

from leash.config import ConfigurationManager
from leash.models import Configuration

# ---------------------------------------------------------------------------
# EnforcementService stub
# ---------------------------------------------------------------------------


class EnforcementService:
    """Manages enforcement mode: observe, approve-only, enforce."""

    _MODES = ["observe", "approve-only", "enforce"]

    def __init__(self, config_manager: ConfigurationManager):
        self._config_manager = config_manager
        config = config_manager.get_configuration()
        if config.enforcement_enabled:
            self._mode = "enforce"
        elif config.enforcement_mode == "approve-only":
            self._mode = "approve-only"
        else:
            self._mode = config.enforcement_mode or "observe"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_enforced(self) -> bool:
        return self._mode == "enforce"

    async def set_enforced(self, enabled: bool) -> None:
        if enabled:
            self._mode = "enforce"
        else:
            self._mode = "observe"
        config = self._config_manager.get_configuration()
        config.enforcement_enabled = enabled
        config.enforcement_mode = self._mode
        await self._config_manager.save()

    async def toggle(self) -> None:
        idx = self._MODES.index(self._mode)
        self._mode = self._MODES[(idx + 1) % len(self._MODES)]
        config = self._config_manager.get_configuration()
        config.enforcement_enabled = self._mode == "enforce"
        config.enforcement_mode = self._mode
        await self._config_manager.save()

    async def set_mode(self, mode: str) -> None:
        if mode not in self._MODES:
            raise ValueError(f"Invalid mode: {mode}")
        self._mode = mode
        config = self._config_manager.get_configuration()
        config.enforcement_enabled = self._mode == "enforce"
        config.enforcement_mode = self._mode
        await self._config_manager.save()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnforcementService:
    async def test_default_mode_is_observe(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        manager = ConfigurationManager(config_path=config_path)
        service = EnforcementService(manager)
        assert service.mode == "observe"
        assert service.is_enforced is False

    async def test_default_mode_is_enforce_when_enabled(self, tmp_path: Path):
        config = Configuration(enforcement_enabled=True)
        manager = ConfigurationManager(config_path=tmp_path / "config.json", config=config)
        service = EnforcementService(manager)
        assert service.mode == "enforce"
        assert service.is_enforced is True

    async def test_cycle_observe_to_approve_only(self, tmp_path: Path):
        manager = ConfigurationManager(config_path=tmp_path / "config.json")
        service = EnforcementService(manager)
        assert service.mode == "observe"

        await service.toggle()
        assert service.mode == "approve-only"
        assert service.is_enforced is False

    async def test_cycle_enforce_to_observe(self, tmp_path: Path):
        config = Configuration(enforcement_enabled=True)
        manager = ConfigurationManager(config_path=tmp_path / "config.json", config=config)
        service = EnforcementService(manager)
        assert service.mode == "enforce"

        await service.toggle()
        assert service.mode == "observe"
        assert service.is_enforced is False

    async def test_full_cycle(self, tmp_path: Path):
        manager = ConfigurationManager(config_path=tmp_path / "config.json")
        service = EnforcementService(manager)
        assert service.mode == "observe"

        await service.toggle()  # observe -> approve-only
        assert service.mode == "approve-only"

        await service.toggle()  # approve-only -> enforce
        assert service.mode == "enforce"

        await service.toggle()  # enforce -> observe
        assert service.mode == "observe"
        assert service.is_enforced is False

    async def test_set_mode_directly(self, tmp_path: Path):
        manager = ConfigurationManager(config_path=tmp_path / "config.json")
        service = EnforcementService(manager)

        await service.set_mode("enforce")
        assert service.is_enforced is True

        await service.set_mode("observe")
        assert service.is_enforced is False

    async def test_invalid_mode_raises_error(self, tmp_path: Path):
        manager = ConfigurationManager(config_path=tmp_path / "config.json")
        service = EnforcementService(manager)

        with pytest.raises(ValueError, match="Invalid mode"):
            await service.set_mode("invalid-mode")

    async def test_set_enforced_persists(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        manager = ConfigurationManager(config_path=config_path)
        service = EnforcementService(manager)

        await service.set_enforced(True)
        assert service.is_enforced is True
        config = manager.get_configuration()
        assert config.enforcement_enabled is True

        await service.set_enforced(False)
        assert service.is_enforced is False
        config = manager.get_configuration()
        assert config.enforcement_enabled is False

    async def test_set_enforced_idempotent(self, tmp_path: Path):
        config = Configuration(enforcement_enabled=True)
        manager = ConfigurationManager(config_path=tmp_path / "config.json", config=config)
        service = EnforcementService(manager)

        await service.set_enforced(True)
        assert service.is_enforced is True

    async def test_multiple_toggles_final_state(self, tmp_path: Path):
        manager = ConfigurationManager(config_path=tmp_path / "config.json")
        service = EnforcementService(manager)

        await service.set_enforced(True)
        await service.set_enforced(False)
        await service.set_enforced(True)
        await service.set_enforced(False)

        assert service.is_enforced is False
        assert manager.get_configuration().enforcement_enabled is False
