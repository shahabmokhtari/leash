"""JSON and HTML audit report generation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic.alias_generators import to_camel

if TYPE_CHECKING:
    from leash.services.adaptive_threshold_service import AdaptiveThresholdService
    from leash.services.profile_service import ProfileService
    from leash.services.session_manager import SessionManager

logger = logging.getLogger(__name__)


class FlaggedOperation(BaseModel):
    """An operation flagged as potentially risky."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    tool_name: str = ""
    safety_score: int = 0
    category: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decision: str = ""
    reasoning: str = ""


class ToolBreakdown(BaseModel):
    """Per-tool statistics breakdown."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    tool_name: str = ""
    total_requests: int = 0
    approved: int = 0
    denied: int = 0
    average_safety_score: float = 0.0


class AuditReport(BaseModel):
    """A complete audit report for a session."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    session_id: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_start: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    active_profile: str = ""
    total_decisions: int = 0
    approved: int = 0
    denied: int = 0
    no_handler: int = 0
    average_safety_score: float = 0.0
    risk_distribution: dict[str, int] = {}
    top_flagged_operations: list[FlaggedOperation] = []
    tool_breakdown: list[ToolBreakdown] = []


class AuditReportGenerator:
    """Generates JSON and HTML audit reports for sessions."""

    def __init__(
        self,
        session_manager: SessionManager,
        adaptive_service: AdaptiveThresholdService,
        profile_service: ProfileService,
    ) -> None:
        self._session_manager = session_manager
        self._adaptive_service = adaptive_service
        self._profile_service = profile_service

    async def generate_report(self, session_id: str) -> AuditReport:
        """Generate an audit report for the given session."""
        session = await self._session_manager.get_or_create_session(session_id)
        profile = self._profile_service.get_active_profile()

        events = session.conversation_history
        total_decisions = len(events)
        approved_count = sum(1 for e in events if e.decision in ("auto-approved", "script-approved", "tray-approved"))
        denied_count = sum(1 for e in events if e.decision in ("denied", "script-denied", "tray-denied"))
        no_handler_count = sum(1 for e in events if e.decision in ("logged", "no-handler"))

        # Risk distribution
        risk_distribution: dict[str, int] = {}
        for e in events:
            if e.category:
                risk_distribution[e.category] = risk_distribution.get(e.category, 0) + 1

        # Top flagged operations (safety_score < 80)
        flagged = [e for e in events if e.safety_score is not None and e.safety_score < 80]
        flagged.sort(key=lambda e: e.safety_score or 0)
        top_flagged = [
            FlaggedOperation(
                tool_name=e.tool_name or "unknown",
                safety_score=e.safety_score or 0,
                category=e.category or "unknown",
                timestamp=e.timestamp,
                decision=e.decision or "unknown",
                reasoning=e.reasoning or "",
            )
            for e in flagged[:10]
        ]

        # Tool breakdown
        tool_groups: dict[str, list[Any]] = {}
        for e in events:
            if e.tool_name:
                tool_groups.setdefault(e.tool_name, []).append(e)

        tool_breakdown = []
        for tool, tool_events in sorted(tool_groups.items(), key=lambda x: len(x[1]), reverse=True):
            scores = [e.safety_score for e in tool_events if e.safety_score is not None]
            avg_score = sum(scores) / len(scores) if scores else 0.0
            tool_breakdown.append(
                ToolBreakdown(
                    tool_name=tool,
                    total_requests=len(tool_events),
                    approved=sum(1 for e in tool_events if e.decision in ("auto-approved", "script-approved", "tray-approved")),
                    denied=sum(1 for e in tool_events if e.decision in ("denied", "script-denied", "tray-denied")),
                    average_safety_score=avg_score,
                )
            )

        # Average safety score
        all_scores = [e.safety_score for e in events if e.safety_score is not None]
        avg_safety = sum(all_scores) / len(all_scores) if all_scores else 0.0

        return AuditReport(
            session_id=session_id,
            generated_at=datetime.now(timezone.utc),
            session_start=session.start_time,
            session_last_activity=session.last_activity,
            active_profile=profile.name,
            total_decisions=total_decisions,
            approved=approved_count,
            denied=denied_count,
            no_handler=no_handler_count,
            average_safety_score=round(avg_safety, 1),
            risk_distribution=risk_distribution,
            top_flagged_operations=top_flagged,
            tool_breakdown=tool_breakdown,
        )

    def render_html(self, report: AuditReport) -> str:
        """Render an audit report as an HTML string with embedded CSS."""
        lines: list[str] = []

        lines.append("<!DOCTYPE html>")
        lines.append('<html lang="en"><head><meta charset="UTF-8">')
        lines.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
        lines.append(f"<title>Audit Report - {escape(report.session_id)}</title>")
        lines.append("<style>")
        lines.append(_get_report_styles())
        lines.append("</style></head><body>")
        lines.append('<div class="report">')

        # Header
        lines.append('<header class="report-header">')
        lines.append("<h1>Permission Audit Report</h1>")
        lines.append(f'<p class="subtitle">Session: {escape(report.session_id)}</p>')
        lines.append(
            f'<p class="meta">Generated: {report.generated_at.strftime("%Y-%m-%d %H:%M:%S")} UTC '
            f"| Profile: {escape(report.active_profile)}</p>"
        )
        lines.append("</header>")

        # Summary cards
        lines.append('<section class="summary-grid">')
        _render_summary_card(lines, "Total Decisions", str(report.total_decisions), "#1976D2")
        _render_summary_card(lines, "Auto-Approved", str(report.approved), "#4CAF50")
        _render_summary_card(lines, "Denied", str(report.denied), "#f44336")
        _render_summary_card(
            lines, "Avg Safety Score",
            f"{report.average_safety_score:.1f}",
            _get_score_color(report.average_safety_score),
        )
        lines.append("</section>")

        # Risk distribution
        if report.risk_distribution:
            lines.append('<section class="section">')
            lines.append("<h2>Risk Distribution</h2>")
            lines.append('<div class="risk-bars">')
            total = sum(report.risk_distribution.values())
            for category, count in sorted(report.risk_distribution.items(), key=lambda x: x[1], reverse=True):
                pct = (count * 100.0 / total) if total > 0 else 0
                color = _get_category_color(category)
                lines.append('<div class="risk-bar-row">')
                lines.append(f'<span class="risk-label">{escape(category)}</span>')
                lines.append(
                    f'<div class="risk-bar-track">'
                    f'<div class="risk-bar-fill" style="width:{pct:.1f}%;background:{color}"></div></div>'
                )
                lines.append(f'<span class="risk-count">{count} ({pct:.0f}%)</span>')
                lines.append("</div>")
            lines.append("</div></section>")

        # Tool breakdown table
        if report.tool_breakdown:
            lines.append('<section class="section">')
            lines.append("<h2>Tool Breakdown</h2>")
            lines.append("<table><thead><tr>")
            lines.append("<th>Tool</th><th>Requests</th><th>Approved</th><th>Denied</th><th>Avg Score</th>")
            lines.append("</tr></thead><tbody>")
            for tool in report.tool_breakdown:
                lines.append("<tr>")
                lines.append(f"<td>{escape(tool.tool_name)}</td>")
                lines.append(f"<td>{tool.total_requests}</td>")
                lines.append(f'<td class="approved">{tool.approved}</td>')
                lines.append(f'<td class="denied">{tool.denied}</td>')
                lines.append(
                    f'<td style="color:{_get_score_color(tool.average_safety_score)}">'
                    f"{tool.average_safety_score:.1f}</td>"
                )
                lines.append("</tr>")
            lines.append("</tbody></table></section>")

        # Top flagged operations
        if report.top_flagged_operations:
            lines.append('<section class="section">')
            lines.append("<h2>Top Flagged Operations</h2>")
            lines.append('<div class="flagged-list">')
            for op in report.top_flagged_operations:
                lines.append('<div class="flagged-item">')
                lines.append('<div class="flagged-header">')
                lines.append(f'<span class="tool-name">{escape(op.tool_name)}</span>')
                lines.append(
                    f'<span class="score" style="color:{_get_score_color(op.safety_score)}">'
                    f"{op.safety_score}</span>"
                )
                lines.append(
                    f'<span class="category category-{escape(op.category)}">'
                    f"{escape(op.category)}</span>"
                )
                lines.append(f'<span class="timestamp">{op.timestamp.strftime("%H:%M:%S")}</span>')
                lines.append("</div>")
                if op.reasoning:
                    lines.append(f'<p class="reasoning">{escape(op.reasoning)}</p>')
                lines.append("</div>")
            lines.append("</div></section>")

        # Recommendations
        lines.append('<section class="section">')
        lines.append("<h2>Recommendations</h2>")
        lines.append('<ul class="recommendations">')
        _generate_recommendations(lines, report)
        lines.append("</ul></section>")

        lines.append("<footer><p>Leash - Audit Report</p></footer>")
        lines.append("</div></body></html>")

        return "\n".join(lines)


# ---- Helper functions ----


def _render_summary_card(lines: list[str], label: str, value: str, color: str) -> None:
    lines.append('<div class="summary-card">')
    lines.append(f'<div class="card-label">{escape(label)}</div>')
    lines.append(f'<div class="card-value" style="color:{color}">{escape(value)}</div>')
    lines.append("</div>")


def _generate_recommendations(lines: list[str], report: AuditReport) -> None:
    if report.total_decisions == 0:
        lines.append("<li>No permission decisions recorded. Ensure the hook integration is working.</li>")
        return

    approval_rate = report.approved / report.total_decisions if report.total_decisions > 0 else 0

    if approval_rate > 0.95:
        lines.append(
            f"<li>Very high approval rate ({approval_rate:.0%}). "
            "Consider switching to 'Permissive' profile to reduce friction.</li>"
        )
    elif approval_rate < 0.5:
        lines.append(
            f"<li>Low approval rate ({approval_rate:.0%}). "
            "Consider reviewing threshold settings or switching to 'Moderate' profile.</li>"
        )

    if report.denied > 10:
        lines.append(f"<li>{report.denied} operations were denied. Review denied operations for false positives.</li>")

    for tool in report.tool_breakdown:
        if tool.denied > 0 and tool.average_safety_score > 80:
            lines.append(
                f"<li>{escape(tool.tool_name)} has denials despite high average score "
                f"({tool.average_safety_score:.0f}). Consider lowering its threshold.</li>"
            )

    if any(op.category == "dangerous" for op in report.top_flagged_operations):
        lines.append(
            "<li>Dangerous operations were detected. "
            "Review these carefully and consider stricter controls.</li>"
        )


def _get_score_color(score: float) -> str:
    if score >= 90:
        return "#4CAF50"
    if score >= 70:
        return "#FF9800"
    if score >= 50:
        return "#f44336"
    return "#d32f2f"


def _get_category_color(category: str) -> str:
    return {
        "safe": "#4CAF50",
        "cautious": "#FF9800",
        "risky": "#f44336",
        "dangerous": "#d32f2f",
    }.get(category, "#9E9E9E")


def _get_report_styles() -> str:
    return """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f0f2f5; color: #333; }
.report { max-width: 900px; margin: 20px auto; background: white;
  border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); overflow: hidden; }
.report-header { background: linear-gradient(135deg, #1976D2, #1565C0); color: white; padding: 32px; }
.report-header h1 { font-size: 28px; margin-bottom: 8px; }
.subtitle { font-size: 16px; opacity: 0.9; }
.meta { font-size: 12px; opacity: 0.7; margin-top: 8px; }
.summary-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: #e0e0e0; }
.summary-card { background: white; padding: 24px; text-align: center; }
.card-label { font-size: 12px; color: #666; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
.card-value { font-size: 32px; font-weight: bold; }
.section { padding: 24px 32px; border-top: 1px solid #e0e0e0; }
.section h2 { font-size: 18px; margin-bottom: 16px; color: #1a1a1a; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; padding: 10px 12px; background: #f5f5f5;
  border-bottom: 2px solid #e0e0e0; font-size: 13px; color: #666; }
td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
td.approved { color: #4CAF50; font-weight: 600; }
td.denied { color: #f44336; font-weight: 600; }
.risk-bars { display: flex; flex-direction: column; gap: 8px; }
.risk-bar-row { display: flex; align-items: center; gap: 12px; }
.risk-label { width: 80px; font-size: 13px; font-weight: 600; text-transform: capitalize; }
.risk-bar-track { flex: 1; height: 24px; background: #f0f0f0; border-radius: 12px; overflow: hidden; }
.risk-bar-fill { height: 100%; border-radius: 12px; transition: width 0.3s; }
.risk-count { width: 80px; font-size: 13px; color: #666; text-align: right; }
.flagged-list { display: flex; flex-direction: column; gap: 8px; }
.flagged-item { padding: 12px; background: #fafafa; border-radius: 8px; border-left: 3px solid #f44336; }
.flagged-header { display: flex; align-items: center; gap: 12px; }
.tool-name { font-weight: 600; }
.score { font-weight: bold; font-size: 18px; }
.category { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #f0f0f0; }
.category-safe { background: #e8f5e9; color: #2e7d32; }
.category-cautious { background: #fff3e0; color: #e65100; }
.category-risky { background: #fce4ec; color: #c62828; }
.category-dangerous { background: #f44336; color: white; }
.timestamp { font-size: 12px; color: #999; margin-left: auto; }
.reasoning { font-size: 13px; color: #666; margin-top: 6px; }
.recommendations { padding-left: 20px; }
.recommendations li { padding: 8px 0; line-height: 1.5; color: #555; }
footer { padding: 16px 32px; text-align: center; color: #999; font-size: 12px; border-top: 1px solid #e0e0e0; }
@media (max-width: 700px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } }
""".strip()
