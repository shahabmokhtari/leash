"""Smart suggestions engine based on usage patterns."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from leash.models.insight import Insight

if TYPE_CHECKING:
    from leash.services.adaptive_threshold_service import AdaptiveThresholdService
    from leash.services.session_manager import SessionManager

logger = logging.getLogger(__name__)

REGENERATE_INTERVAL = timedelta(minutes=30)


class InsightsEngine:
    """Generates smart suggestions based on tool usage patterns and override history."""

    def __init__(
        self,
        adaptive_service: AdaptiveThresholdService,
        session_manager: SessionManager,
    ) -> None:
        self._adaptive_service = adaptive_service
        self._session_manager = session_manager
        self._insights: list[Insight] = []
        self._dismissed_ids: set[str] = set()
        self._last_generated = datetime.min.replace(tzinfo=timezone.utc)

    def get_insights(self, include_all: bool = False) -> list[Insight]:
        """Return insights, regenerating if stale.

        By default returns only non-dismissed insights.
        """
        if datetime.now(timezone.utc) - self._last_generated > REGENERATE_INTERVAL:
            self.regenerate_insights()

        if include_all:
            return list(self._insights)
        return [i for i in self._insights if not i.dismissed and i.id not in self._dismissed_ids]

    def dismiss_insight(self, insight_id: str) -> None:
        """Mark an insight as dismissed."""
        self._dismissed_ids.add(insight_id)
        for insight in self._insights:
            if insight.id == insight_id:
                insight.dismissed = True

    def regenerate_insights(self) -> None:
        """Force regeneration of all insights."""
        self._insights.clear()
        stats = self._adaptive_service.get_tool_stats()
        overrides = self._adaptive_service.get_recent_overrides(100)

        self._generate_high_approval_rate_insights(stats)
        self._generate_high_override_rate_insights(stats)
        self._generate_threshold_suggestion_insights(stats)
        self._generate_unusual_pattern_insights(stats, overrides)
        self._generate_safe_list_candidate_insights(stats)

        self._last_generated = datetime.now(timezone.utc)
        logger.debug("Generated %d insights", len(self._insights))

    # ---- Insight generators ----

    def _generate_high_approval_rate_insights(
        self, stats: dict[str, object]
    ) -> None:
        """Detect tools with very high approval rates."""
        from leash.models.adaptive_threshold import ToolThresholdStats

        for tool, tool_stats in stats.items():
            if not isinstance(tool_stats, ToolThresholdStats):
                continue
            if tool_stats.total_decisions < 10:
                continue

            approval_rate = 1.0 - (tool_stats.false_negatives / tool_stats.total_decisions)
            if approval_rate >= 0.95 and tool_stats.total_decisions >= 20:
                self._insights.append(
                    Insight(
                        type="high-approval-rate",
                        severity="suggestion",
                        title=f"High approval rate for {tool}",
                        description=(
                            f"You approved {approval_rate:.0%} of {tool} operations "
                            f"({tool_stats.total_decisions} total). "
                            "This tool appears consistently safe in your workflow."
                        ),
                        recommendation=(
                            f"Consider adding {tool} to a safe list or lowering its "
                            "threshold to reduce prompts."
                        ),
                        tool_name=tool,
                        data_points={
                            "approvalRate": round(approval_rate * 100, 1),
                            "totalDecisions": tool_stats.total_decisions,
                            "avgScore": round(tool_stats.average_safety_score, 1),
                        },
                    )
                )

    def _generate_high_override_rate_insights(
        self, stats: dict[str, object]
    ) -> None:
        """Detect tools with frequent user overrides."""
        from leash.models.adaptive_threshold import ToolThresholdStats

        for tool, tool_stats in stats.items():
            if not isinstance(tool_stats, ToolThresholdStats):
                continue
            if tool_stats.total_decisions < 5 or tool_stats.override_count < 3:
                continue

            override_rate = tool_stats.override_count / tool_stats.total_decisions
            if override_rate >= 0.3:
                recommendation = (
                    "Threshold appears too strict. Consider lowering it."
                    if tool_stats.false_positives > tool_stats.false_negatives
                    else "Threshold appears too lenient. Consider raising it."
                )
                self._insights.append(
                    Insight(
                        type="high-override-rate",
                        severity="warning",
                        title=f"Frequent overrides for {tool}",
                        description=(
                            f"You've overridden {override_rate:.0%} of decisions for {tool} "
                            f"({tool_stats.override_count} of {tool_stats.total_decisions}). "
                            "The current threshold may not match your preferences."
                        ),
                        recommendation=recommendation,
                        tool_name=tool,
                        data_points={
                            "overrideRate": round(override_rate * 100, 1),
                            "falsePositives": tool_stats.false_positives,
                            "falseNegatives": tool_stats.false_negatives,
                        },
                    )
                )

    def _generate_threshold_suggestion_insights(
        self, stats: dict[str, object]
    ) -> None:
        """Surface optimized threshold suggestions."""
        from leash.models.adaptive_threshold import ToolThresholdStats

        for tool, tool_stats in stats.items():
            if not isinstance(tool_stats, ToolThresholdStats):
                continue
            if tool_stats.suggested_threshold is None or tool_stats.confidence_level < 0.5:
                continue

            self._insights.append(
                Insight(
                    type="threshold-suggestion",
                    severity="info",
                    title=f"Optimized threshold available for {tool}",
                    description=(
                        f"Based on {tool_stats.total_decisions} decisions and "
                        f"{tool_stats.override_count} overrides, "
                        f"an optimal threshold of {tool_stats.suggested_threshold} has been calculated "
                        f"(confidence: {tool_stats.confidence_level:.0%})."
                    ),
                    recommendation=(
                        f"Apply the suggested threshold of {tool_stats.suggested_threshold} for {tool} "
                        "to reduce manual overrides."
                    ),
                    tool_name=tool,
                    data_points={
                        "suggestedThreshold": tool_stats.suggested_threshold,
                        "confidence": round(tool_stats.confidence_level, 2),
                        "currentAvgScore": round(tool_stats.average_safety_score, 1),
                    },
                )
            )

    def _generate_unusual_pattern_insights(
        self,
        stats: dict[str, object],
        overrides: list[object],
    ) -> None:
        """Detect sudden spikes in overrides."""
        from leash.models.adaptive_threshold import ThresholdOverride

        typed_overrides = [o for o in overrides if isinstance(o, ThresholdOverride)]
        if len(typed_overrides) < 5:
            return

        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        recent = [o for o in typed_overrides if o.timestamp > one_hour_ago]

        # Group by tool
        tool_groups: dict[str, list[ThresholdOverride]] = {}
        for o in recent:
            tool_groups.setdefault(o.tool_name, []).append(o)

        for tool, group in tool_groups.items():
            if len(group) >= 3:
                self._insights.append(
                    Insight(
                        type="unusual-activity",
                        severity="warning",
                        title=f"Override spike for {tool}",
                        description=(
                            f"There have been {len(group)} overrides for {tool} in the last hour, "
                            "which is higher than normal."
                        ),
                        recommendation=(
                            "Review recent activity to ensure this pattern is expected. "
                            "Consider adjusting the threshold if the current setting doesn't match your needs."
                        ),
                        tool_name=tool,
                        data_points={
                            "recentOverrides": len(group),
                            "timeWindow": "1 hour",
                        },
                    )
                )

    def _generate_safe_list_candidate_insights(
        self, stats: dict[str, object]
    ) -> None:
        """Identify tools that are candidates for the safe list."""
        from leash.models.adaptive_threshold import ToolThresholdStats

        for tool, tool_stats in stats.items():
            if not isinstance(tool_stats, ToolThresholdStats):
                continue
            if tool_stats.total_decisions < 30:
                continue
            if tool_stats.average_safety_score < 90:
                continue
            if tool_stats.false_negatives > 0:
                continue

            self._insights.append(
                Insight(
                    type="safe-list-candidate",
                    severity="suggestion",
                    title=f"{tool} is a safe-list candidate",
                    description=(
                        f"{tool} has a perfect track record over {tool_stats.total_decisions} decisions "
                        f"with an average safety score of {tool_stats.average_safety_score:.1f}. "
                        "No operations were ever overridden to denied."
                    ),
                    recommendation=(
                        f"Consider adding {tool} to the auto-approve safe list "
                        "to eliminate prompts entirely."
                    ),
                    tool_name=tool,
                    data_points={
                        "totalDecisions": tool_stats.total_decisions,
                        "avgScore": round(tool_stats.average_safety_score, 1),
                        "falseNegatives": 0,
                    },
                )
            )
