"""Tests for AdaptiveThresholdService."""

from __future__ import annotations

import json
from pathlib import Path

from leash.models.adaptive_threshold import (
    AdaptiveThresholdData,
    ThresholdOverride,
    ToolThresholdStats,
)

# ---------------------------------------------------------------------------
# AdaptiveThresholdService stub
# ---------------------------------------------------------------------------


class AdaptiveThresholdService:
    """Learns from user overrides to suggest threshold adjustments."""

    def __init__(self, storage_dir: str | Path):
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._data_path = self._storage_dir / "adaptive_data.json"
        self._data = AdaptiveThresholdData()

    async def load(self) -> None:
        if self._data_path.exists():
            raw = self._data_path.read_text()
            self._data = AdaptiveThresholdData.model_validate(json.loads(raw))

    def _save(self) -> None:
        self._data_path.write_text(
            self._data.model_dump_json(by_alias=True, indent=2)
        )

    async def record_decision(self, tool_name: str, safety_score: int, decision: str) -> None:
        stats = self._data.tool_stats.setdefault(
            tool_name, ToolThresholdStats(tool_name=tool_name)
        )
        stats.total_decisions += 1
        # Running average
        n = stats.total_decisions
        stats.average_safety_score = (
            (stats.average_safety_score * (n - 1) + safety_score) / n
        )
        self._save()

    async def record_override(
        self,
        tool_name: str,
        original_decision: str,
        user_action: str,
        safety_score: int,
        threshold: int,
        session_id: str,
    ) -> None:
        override = ThresholdOverride(
            tool_name=tool_name,
            original_decision=original_decision,
            user_action=user_action,
            safety_score=safety_score,
            threshold=threshold,
            session_id=session_id,
        )
        self._data.overrides.append(override)

        stats = self._data.tool_stats.setdefault(
            tool_name, ToolThresholdStats(tool_name=tool_name)
        )
        stats.override_count += 1

        # False positive: system denied but user approved
        if original_decision in ("denied", "deny") and user_action in ("approved", "approve"):
            stats.false_positives += 1
        # False negative: system approved but user denied
        elif original_decision in ("auto-approved", "approved", "approve") and user_action in (
            "denied",
            "deny",
        ):
            stats.false_negatives += 1

        self._save()

    def get_tool_stats(self) -> dict[str, ToolThresholdStats]:
        return dict(self._data.tool_stats)

    def get_recent_overrides(self, limit: int = 50) -> list[ThresholdOverride]:
        return list(reversed(self._data.overrides[-limit:]))

    def get_suggested_threshold(self, tool_name: str) -> int | None:
        stats = self._data.tool_stats.get(tool_name)
        if not stats or stats.override_count < 3:
            return None
        # Simple suggestion: average of override safety scores
        override_scores = [
            o.safety_score for o in self._data.overrides if o.tool_name == tool_name
        ]
        if not override_scores:
            return None
        return int(sum(override_scores) / len(override_scores))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAdaptiveThresholdService:
    async def test_record_decision_tracks_tool_stats(self, tmp_path: Path):
        service = AdaptiveThresholdService(tmp_path / "adaptive")

        await service.record_decision("Bash", 90, "auto-approved")
        await service.record_decision("Bash", 85, "auto-approved")
        await service.record_decision("Bash", 70, "denied")

        stats = service.get_tool_stats()
        assert "Bash" in stats
        assert stats["Bash"].total_decisions == 3
        assert stats["Bash"].average_safety_score > 0

    async def test_record_override_tracks_false_positives(self, tmp_path: Path):
        service = AdaptiveThresholdService(tmp_path / "adaptive")

        # System denied but user approved (false positive)
        await service.record_override("Bash", "denied", "approved", 80, 85, "session-1")

        stats = service.get_tool_stats()
        assert stats["Bash"].false_positives == 1
        assert stats["Bash"].false_negatives == 0
        assert stats["Bash"].override_count == 1

    async def test_record_override_tracks_false_negatives(self, tmp_path: Path):
        service = AdaptiveThresholdService(tmp_path / "adaptive")

        # System approved but user denied (false negative)
        await service.record_override("Write", "auto-approved", "denied", 92, 85, "session-1")

        stats = service.get_tool_stats()
        assert stats["Write"].false_positives == 0
        assert stats["Write"].false_negatives == 1

    async def test_get_recent_overrides_in_reverse_order(self, tmp_path: Path):
        service = AdaptiveThresholdService(tmp_path / "adaptive")

        await service.record_override("Bash", "denied", "approved", 80, 85, "session-1")
        await service.record_override("Write", "denied", "approved", 75, 85, "session-2")

        overrides = service.get_recent_overrides()
        assert len(overrides) == 2
        assert overrides[0].tool_name == "Write"
        assert overrides[1].tool_name == "Bash"

    async def test_persistence_across_instances(self, tmp_path: Path):
        storage = tmp_path / "adaptive"

        service1 = AdaptiveThresholdService(storage)
        await service1.record_override("Bash", "denied", "approved", 80, 85, "session-1")

        service2 = AdaptiveThresholdService(storage)
        await service2.load()

        overrides = service2.get_recent_overrides()
        assert len(overrides) == 1
        assert overrides[0].tool_name == "Bash"

    async def test_suggested_threshold_returns_none_without_data(self, tmp_path: Path):
        service = AdaptiveThresholdService(tmp_path / "adaptive")
        assert service.get_suggested_threshold("Bash") is None

    async def test_suggested_threshold_with_enough_overrides(self, tmp_path: Path):
        service = AdaptiveThresholdService(tmp_path / "adaptive")

        for i in range(5):
            await service.record_override("Bash", "denied", "approved", 75 + i, 85, f"s{i}")

        suggestion = service.get_suggested_threshold("Bash")
        assert suggestion is not None
        assert 70 <= suggestion <= 90
