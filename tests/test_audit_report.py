"""Tests for AuditReportGenerator."""

from __future__ import annotations

from pathlib import Path

from leash.models import SessionEvent

# Reuse SessionManager stub
from tests.test_session_manager import SessionManager

# ---------------------------------------------------------------------------
# AuditReport model
# ---------------------------------------------------------------------------


class ToolBreakdown:
    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        self.total_requests = 0
        self.approved = 0
        self.denied = 0


class AuditReport:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.total_decisions = 0
        self.approved = 0
        self.denied = 0
        self.average_safety_score = 0.0
        self.risk_distribution: dict[str, int] = {}
        self.top_flagged_operations: list[SessionEvent] = []
        self.tool_breakdown: list[ToolBreakdown] = []


# ---------------------------------------------------------------------------
# AuditReportGenerator stub
# ---------------------------------------------------------------------------


class AuditReportGenerator:
    """Generates audit reports from session data."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    async def generate_report(self, session_id: str) -> AuditReport:
        session = await self._session_manager.get_or_create_session(session_id)
        report = AuditReport(session_id=session_id)

        scores = []
        tool_map: dict[str, ToolBreakdown] = {}

        for evt in session.conversation_history:
            if evt.type != "permission-request":
                continue
            report.total_decisions += 1

            if evt.decision and "approved" in evt.decision:
                report.approved += 1
            elif evt.decision and "denied" in evt.decision:
                report.denied += 1

            if evt.safety_score is not None:
                scores.append(evt.safety_score)

            if evt.category:
                report.risk_distribution[evt.category] = (
                    report.risk_distribution.get(evt.category, 0) + 1
                )

            # Flagged = denied or score < 50
            if (evt.decision and "denied" in evt.decision) or (
                evt.safety_score is not None and evt.safety_score < 50
            ):
                report.top_flagged_operations.append(evt)

            # Tool breakdown
            tool_name = evt.tool_name or "unknown"
            if tool_name not in tool_map:
                tool_map[tool_name] = ToolBreakdown(tool_name)
            tb = tool_map[tool_name]
            tb.total_requests += 1
            if evt.decision and "approved" in evt.decision:
                tb.approved += 1
            elif evt.decision and "denied" in evt.decision:
                tb.denied += 1

        if scores:
            report.average_safety_score = sum(scores) / len(scores)

        report.tool_breakdown = list(tool_map.values())
        return report

    def render_html(self, report: AuditReport) -> str:
        events_html = ""
        for evt in report.top_flagged_operations:
            events_html += f"<tr><td>{evt.tool_name}</td><td>{evt.safety_score}</td><td>{evt.decision}</td></tr>\n"

        return f"""<!DOCTYPE html>
<html>
<head><title>Permission Audit Report</title></head>
<body>
<h1>Permission Audit Report</h1>
<h2>Session: {report.session_id}</h2>
<p>Total: {report.total_decisions}, Approved: {report.approved}, Denied: {report.denied}</p>
<table>{events_html}</table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuditReportGenerator:
    async def test_empty_session_report(self, tmp_path: Path):
        session_mgr = SessionManager(tmp_path / "sessions")
        generator = AuditReportGenerator(session_mgr)

        report = await generator.generate_report("new-session")

        assert report.session_id == "new-session"
        assert report.total_decisions == 0
        assert report.approved == 0
        assert report.denied == 0

    async def test_report_counts_decisions_correctly(self, tmp_path: Path):
        session_mgr = SessionManager(tmp_path / "sessions")
        generator = AuditReportGenerator(session_mgr)

        await session_mgr.record_event(
            "test-session",
            SessionEvent(
                type="permission-request",
                tool_name="Bash",
                decision="auto-approved",
                safety_score=95,
                category="safe",
            ),
        )
        await session_mgr.record_event(
            "test-session",
            SessionEvent(
                type="permission-request",
                tool_name="Write",
                decision="denied",
                safety_score=60,
                category="risky",
            ),
        )
        await session_mgr.record_event(
            "test-session",
            SessionEvent(
                type="permission-request",
                tool_name="Read",
                decision="auto-approved",
                safety_score=98,
                category="safe",
            ),
        )

        report = await generator.generate_report("test-session")

        assert report.total_decisions == 3
        assert report.approved == 2
        assert report.denied == 1
        assert report.average_safety_score > 0
        assert report.risk_distribution["safe"] == 2
        assert report.risk_distribution["risky"] == 1

    async def test_identifies_flagged_operations(self, tmp_path: Path):
        session_mgr = SessionManager(tmp_path / "sessions")
        generator = AuditReportGenerator(session_mgr)

        await session_mgr.record_event(
            "test-session",
            SessionEvent(
                type="permission-request",
                tool_name="Bash",
                decision="denied",
                safety_score=40,
                category="dangerous",
                reasoning="Potentially dangerous command",
            ),
        )

        report = await generator.generate_report("test-session")

        assert len(report.top_flagged_operations) == 1
        assert report.top_flagged_operations[0].tool_name == "Bash"
        assert report.top_flagged_operations[0].safety_score == 40

    async def test_html_rendering(self, tmp_path: Path):
        session_mgr = SessionManager(tmp_path / "sessions")
        generator = AuditReportGenerator(session_mgr)

        await session_mgr.record_event(
            "test-session",
            SessionEvent(
                type="permission-request",
                tool_name="Bash",
                decision="auto-approved",
                safety_score=95,
                category="safe",
            ),
        )

        report = await generator.generate_report("test-session")
        html = generator.render_html(report)

        assert "<!DOCTYPE html>" in html
        assert "Permission Audit Report" in html
        assert "test-session" in html
        assert "</html>" in html

    async def test_tool_breakdown(self, tmp_path: Path):
        session_mgr = SessionManager(tmp_path / "sessions")
        generator = AuditReportGenerator(session_mgr)

        for i in range(5):
            await session_mgr.record_event(
                "test-session",
                SessionEvent(
                    type="permission-request",
                    tool_name="Bash",
                    decision="auto-approved",
                    safety_score=90 + i,
                    category="safe",
                ),
            )
        for i in range(3):
            await session_mgr.record_event(
                "test-session",
                SessionEvent(
                    type="permission-request",
                    tool_name="Write",
                    decision="denied",
                    safety_score=60 + i,
                    category="risky",
                ),
            )

        report = await generator.generate_report("test-session")

        assert len(report.tool_breakdown) == 2
        bash_breakdown = next(t for t in report.tool_breakdown if t.tool_name == "Bash")
        assert bash_breakdown.total_requests == 5
        assert bash_breakdown.approved == 5
        assert bash_breakdown.denied == 0
