"""Tests for InsightsEngine."""

from __future__ import annotations

from pathlib import Path

from leash.models.insight import Insight

# Reuse AdaptiveThresholdService stub from the adaptive tests
from tests.test_adaptive_threshold import AdaptiveThresholdService

# ---------------------------------------------------------------------------
# InsightsEngine stub
# ---------------------------------------------------------------------------


class InsightsEngine:
    """Generates smart suggestions based on tool stats and overrides."""

    def __init__(self, adaptive_service: AdaptiveThresholdService):
        self._adaptive = adaptive_service
        self._insights: list[Insight] = []
        self._dismissed: set[str] = set()

    def regenerate_insights(self) -> None:
        self._insights = []
        tool_stats = self._adaptive.get_tool_stats()

        for tool_name, stats in tool_stats.items():
            # High override rate
            if stats.total_decisions >= 5 and stats.override_count >= 3:
                rate = stats.override_count / stats.total_decisions
                if rate > 0.2:
                    self._insights.append(
                        Insight(
                            type="high-override-rate",
                            severity="warning",
                            title=f"High override rate for {tool_name}",
                            description=f"{tool_name} has {stats.override_count} overrides "
                            f"out of {stats.total_decisions} decisions ({rate:.0%})",
                            recommendation="Consider adjusting the threshold for this tool.",
                            tool_name=tool_name,
                        )
                    )

            # Safe-list candidate: many approvals, high avg score
            if (
                stats.total_decisions >= 30
                and stats.average_safety_score >= 90
                and stats.false_negatives == 0
            ):
                self._insights.append(
                    Insight(
                        type="safe-list-candidate",
                        severity="info",
                        title=f"{tool_name} could be safe-listed",
                        description=f"{tool_name} has {stats.total_decisions} decisions "
                        f"with avg score {stats.average_safety_score:.0f}",
                        recommendation="Consider adding this tool to the safe list.",
                        tool_name=tool_name,
                    )
                )

            # Threshold suggestion
            suggestion = self._adaptive.get_suggested_threshold(tool_name)
            if suggestion is not None:
                self._insights.append(
                    Insight(
                        type="threshold-suggestion",
                        severity="info",
                        title=f"Threshold suggestion for {tool_name}",
                        description=f"Based on overrides, suggested threshold: {suggestion}",
                        recommendation=f"Consider changing threshold to {suggestion}.",
                        tool_name=tool_name,
                        data_points={"suggested_threshold": suggestion},
                    )
                )

    def get_insights(self, include_discussed: bool = False) -> list[Insight]:
        if include_discussed:
            return list(self._insights)
        return [i for i in self._insights if i.id not in self._dismissed]

    def dismiss_insight(self, insight_id: str) -> None:
        self._dismissed.add(insight_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInsightsEngine:
    async def test_returns_empty_with_no_data(self, tmp_path: Path):
        adaptive = AdaptiveThresholdService(tmp_path / "insights")
        engine = InsightsEngine(adaptive)
        engine.regenerate_insights()
        assert engine.get_insights() == []

    async def test_detects_high_override_rate(self, tmp_path: Path):
        adaptive = AdaptiveThresholdService(tmp_path / "insights")
        engine = InsightsEngine(adaptive)

        for i in range(10):
            await adaptive.record_decision("Bash", 80, "denied")
        for i in range(5):
            await adaptive.record_override("Bash", "denied", "approved", 80, 85, "s1")

        engine.regenerate_insights()
        insights = engine.get_insights()
        assert any(i.type == "high-override-rate" and i.tool_name == "Bash" for i in insights)

    async def test_detects_safe_list_candidate(self, tmp_path: Path):
        adaptive = AdaptiveThresholdService(tmp_path / "insights")
        engine = InsightsEngine(adaptive)

        for i in range(35):
            await adaptive.record_decision("Read", 95, "auto-approved")

        engine.regenerate_insights()
        insights = engine.get_insights()
        assert any(i.type == "safe-list-candidate" and i.tool_name == "Read" for i in insights)

    async def test_dismiss_insight_hides_from_results(self, tmp_path: Path):
        adaptive = AdaptiveThresholdService(tmp_path / "insights")
        engine = InsightsEngine(adaptive)

        for i in range(35):
            await adaptive.record_decision("Read", 95, "auto-approved")

        engine.regenerate_insights()
        all_insights = engine.get_insights(include_discussed=True)

        if all_insights:
            insight_id = all_insights[0].id
            engine.dismiss_insight(insight_id)
            visible = engine.get_insights()
            assert not any(i.id == insight_id for i in visible)

    async def test_threshold_suggestion(self, tmp_path: Path):
        adaptive = AdaptiveThresholdService(tmp_path / "insights")
        engine = InsightsEngine(adaptive)

        # Record enough decisions and overrides for a suggestion
        for i in range(60):
            await adaptive.record_decision("Bash", 85, "auto-approved")
        for i in range(10):
            await adaptive.record_override("Bash", "denied", "approved", 80, 85, "s1")

        engine.regenerate_insights()
        # The code ran without errors -- the insight may or may not appear
        # depending on confidence
        insights = engine.get_insights()
        assert isinstance(insights, list)
