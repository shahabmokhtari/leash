"""Adaptive threshold learning from user overrides."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

from leash.models.adaptive_threshold import (
    AdaptiveThresholdData,
    ThresholdOverride,
    ToolThresholdStats,
)

logger = logging.getLogger(__name__)

MAX_OVERRIDE_HISTORY = 500
MIN_SAMPLES_FOR_SUGGESTION = 5


class AdaptiveThresholdService:
    """Learns from user overrides to suggest optimal thresholds."""

    def __init__(self, storage_dir: str = "~/.leash") -> None:
        storage_path = Path(storage_dir).expanduser().resolve()
        self._data_file_path = storage_path / "adaptive-thresholds.json"
        self._data = AdaptiveThresholdData()
        self._file_lock = asyncio.Lock()

    async def load(self) -> None:
        """Load adaptive threshold data from disk."""
        if self._data_file_path.exists():
            try:
                async with aiofiles.open(self._data_file_path, "r") as f:
                    raw = await f.read()
                data = json.loads(raw)
                self._data = AdaptiveThresholdData.model_validate(data)
            except Exception as e:
                logger.warning("Failed to load adaptive threshold data, starting fresh: %s", e)
                self._data = AdaptiveThresholdData()

    async def record_override(
        self,
        tool_name: str,
        original_decision: str,
        user_action: str,
        safety_score: int,
        threshold: int,
        session_id: str,
    ) -> None:
        """Record a user override of an automatic decision."""
        override_record = ThresholdOverride(
            tool_name=tool_name,
            original_decision=original_decision,
            user_action=user_action,
            safety_score=safety_score,
            threshold=threshold,
            session_id=session_id,
        )

        self._data.overrides.append(override_record)

        # Trim old overrides
        while len(self._data.overrides) > MAX_OVERRIDE_HISTORY:
            self._data.overrides.pop(0)

        # Update tool stats
        self._update_tool_stats(tool_name, original_decision, user_action, safety_score)

        await self._save()

        logger.info(
            "Recorded override for %s: %s -> %s (score: %s)",
            tool_name, original_decision, user_action, safety_score,
        )

    async def record_decision(self, tool_name: str, safety_score: int, decision: str) -> None:
        """Record a decision and update per-tool stats."""
        if tool_name not in self._data.tool_stats:
            self._data.tool_stats[tool_name] = ToolThresholdStats(tool_name=tool_name)

        stats = self._data.tool_stats[tool_name]
        stats.total_decisions += 1

        # Running average
        stats.average_safety_score = (
            (stats.average_safety_score * (stats.total_decisions - 1)) + safety_score
        ) / stats.total_decisions

        # Recalculate suggestion periodically
        if stats.total_decisions % 10 == 0:
            self._recalculate_suggested_threshold(tool_name)

        # Save periodically to avoid excessive IO
        if stats.total_decisions % 5 == 0:
            await self._save()

    def get_suggested_threshold(self, tool_name: str) -> int | None:
        """Get the suggested threshold for a tool, if available."""
        stats = self._data.tool_stats.get(tool_name)
        if stats and stats.suggested_threshold is not None:
            return stats.suggested_threshold
        return None

    def get_data(self) -> AdaptiveThresholdData:
        """Return the full adaptive threshold data."""
        return self._data

    def get_tool_stats(self) -> dict[str, ToolThresholdStats]:
        """Return per-tool statistics."""
        return self._data.tool_stats

    def get_recent_overrides(self, count: int = 20) -> list[ThresholdOverride]:
        """Return the most recent overrides in reverse chronological order."""
        return list(reversed(self._data.overrides[-count:]))

    def _update_tool_stats(
        self,
        tool_name: str,
        original_decision: str,
        user_action: str,
        safety_score: int,
    ) -> None:
        """Update tool statistics from an override."""
        if tool_name not in self._data.tool_stats:
            self._data.tool_stats[tool_name] = ToolThresholdStats(tool_name=tool_name)

        stats = self._data.tool_stats[tool_name]
        stats.override_count += 1

        # False positive: system denied but user approved (threshold too high)
        if original_decision in ("denied", "script-denied") and user_action == "approved":
            stats.false_positives += 1
        # False negative: system approved but user denied (threshold too low)
        elif original_decision in ("auto-approved", "script-approved") and user_action == "denied":
            stats.false_negatives += 1

        self._recalculate_suggested_threshold(tool_name)

    def _recalculate_suggested_threshold(self, tool_name: str) -> None:
        """Recalculate the suggested threshold based on override patterns."""
        stats = self._data.tool_stats.get(tool_name)
        if not stats:
            return

        if stats.total_decisions < MIN_SAMPLES_FOR_SUGGESTION:
            stats.confidence_level = 0.0
            return

        # Get recent overrides for this tool
        tool_overrides = [o for o in self._data.overrides if o.tool_name == tool_name][-50:]

        if not tool_overrides:
            stats.confidence_level = min(1.0, stats.total_decisions / 100.0)
            return

        # Calculate suggested threshold based on override patterns
        fp_overrides = [
            o for o in tool_overrides
            if o.original_decision in ("denied", "script-denied") and o.user_action == "approved"
        ]
        fn_overrides = [
            o for o in tool_overrides
            if o.original_decision in ("auto-approved", "script-approved") and o.user_action == "denied"
        ]

        if fp_overrides or fn_overrides:
            lower_bound = (
                int(sum(o.safety_score for o in fp_overrides) / len(fp_overrides))
                if fp_overrides else None
            )
            upper_bound = (
                int(sum(o.safety_score for o in fn_overrides) / len(fn_overrides))
                if fn_overrides else None
            )

            if lower_bound is not None and upper_bound is not None:
                stats.suggested_threshold = (lower_bound + upper_bound) // 2
            elif lower_bound is not None:
                stats.suggested_threshold = max(50, lower_bound - 5)
            elif upper_bound is not None:
                stats.suggested_threshold = min(100, upper_bound + 5)

        # Confidence based on sample size and override ratio
        override_ratio = stats.override_count / max(1, stats.total_decisions)
        stats.confidence_level = min(
            1.0,
            (stats.total_decisions / 50.0) * (1.0 - min(0.5, override_ratio)),
        )

        self._data.last_calculated = datetime.now(timezone.utc)

    async def _save(self) -> None:
        """Persist data to disk with file locking."""
        async with self._file_lock:
            try:
                self._data_file_path.parent.mkdir(parents=True, exist_ok=True)
                data = self._data.model_dump(by_alias=True, mode="json")
                raw = json.dumps(data, indent=2)
                async with aiofiles.open(self._data_file_path, "w") as f:
                    await f.write(raw)
            except Exception as e:
                logger.warning("Failed to save adaptive threshold data: %s", e)
