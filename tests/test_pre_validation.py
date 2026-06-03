"""Tests for the pre-validation service and route helper."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from leash.services.pre_validation_service import PreValidationResult, PreValidationService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scripts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scripts"
    d.mkdir()
    return d


@pytest.fixture
def svc(scripts_dir: Path) -> PreValidationService:
    return PreValidationService(scripts_dir=str(scripts_dir))


def _write_script(scripts_dir: Path, name: str, code: str) -> Path:
    """Write a Python script into the scripts directory."""
    p = scripts_dir / name
    p.write_text(textwrap.dedent(code))
    return p


# ---------------------------------------------------------------------------
# PreValidationService.run() — basic decisions
# ---------------------------------------------------------------------------


class TestPreValidationServiceDecisions:
    async def test_approve(self, svc: PreValidationService, scripts_dir: Path):
        _write_script(scripts_dir, "approve.py", """\
            import json, sys
            ctx = json.load(sys.stdin)
            print(json.dumps({"decision": "approve", "reason": "all good"}))
        """)
        result = await svc.run("approve.py", {"toolName": "Read"})
        assert result.decision == "approve"
        assert result.reason == "all good"

    async def test_deny(self, svc: PreValidationService, scripts_dir: Path):
        _write_script(scripts_dir, "deny.py", """\
            import json, sys
            ctx = json.load(sys.stdin)
            print(json.dumps({"decision": "deny", "reason": "not allowed"}))
        """)
        result = await svc.run("deny.py", {"toolName": "Bash"})
        assert result.decision == "deny"
        assert result.reason == "not allowed"

    async def test_passthrough(self, svc: PreValidationService, scripts_dir: Path):
        _write_script(scripts_dir, "pass.py", """\
            import json, sys
            ctx = json.load(sys.stdin)
            print(json.dumps({"decision": "passthrough", "reason": "needs LLM"}))
        """)
        result = await svc.run("pass.py", {"toolName": "Write"})
        assert result.decision == "passthrough"

    async def test_script_receives_context(self, svc: PreValidationService, scripts_dir: Path):
        """Verify that the full context dict is available to the script."""
        _write_script(scripts_dir, "echo.py", """\
            import json, sys
            ctx = json.load(sys.stdin)
            # Echo back the toolName and cwd to prove we received them
            reason = f"tool={ctx.get('toolName')} cwd={ctx.get('cwd')}"
            print(json.dumps({"decision": "approve", "reason": reason}))
        """)
        result = await svc.run("echo.py", {"toolName": "Read", "cwd": "/home/user"})
        assert result.decision == "approve"
        assert "tool=Read" in result.reason
        assert "cwd=/home/user" in result.reason


# ---------------------------------------------------------------------------
# PreValidationService.run() — error handling (fail-safe → passthrough)
# ---------------------------------------------------------------------------


class TestPreValidationServiceErrors:
    async def test_missing_script(self, svc: PreValidationService):
        result = await svc.run("nonexistent.py", {})
        assert result.decision == "passthrough"

    async def test_script_crashes(self, svc: PreValidationService, scripts_dir: Path):
        _write_script(scripts_dir, "crash.py", """\
            raise RuntimeError("boom")
        """)
        result = await svc.run("crash.py", {"toolName": "Read"})
        assert result.decision == "passthrough"

    async def test_invalid_json_output(self, svc: PreValidationService, scripts_dir: Path):
        _write_script(scripts_dir, "bad_json.py", """\
            print("this is not json")
        """)
        result = await svc.run("bad_json.py", {"toolName": "Read"})
        assert result.decision == "passthrough"

    async def test_empty_output(self, svc: PreValidationService, scripts_dir: Path):
        _write_script(scripts_dir, "empty.py", """\
            pass
        """)
        result = await svc.run("empty.py", {})
        assert result.decision == "passthrough"

    async def test_unknown_decision(self, svc: PreValidationService, scripts_dir: Path):
        _write_script(scripts_dir, "unknown.py", """\
            import json
            print(json.dumps({"decision": "maybe", "reason": "unsure"}))
        """)
        result = await svc.run("unknown.py", {})
        assert result.decision == "passthrough"

    async def test_timeout(self, svc: PreValidationService, scripts_dir: Path):
        _write_script(scripts_dir, "slow.py", """\
            import time, json, sys
            ctx = json.load(sys.stdin)
            time.sleep(30)
            print(json.dumps({"decision": "approve", "reason": "late"}))
        """)
        # Patch timeout to 1 second for test speed
        import leash.services.pre_validation_service as mod
        original_timeout = mod.SCRIPT_TIMEOUT_SECONDS
        mod.SCRIPT_TIMEOUT_SECONDS = 1
        try:
            result = await svc.run("slow.py", {})
            assert result.decision == "passthrough"
        finally:
            mod.SCRIPT_TIMEOUT_SECONDS = original_timeout


# ---------------------------------------------------------------------------
# PreValidationService — bundled script copying
# ---------------------------------------------------------------------------


class TestPreValidationServiceBundledCopy:
    def test_copies_bundled_scripts(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "example.py").write_text("# example script")
        user_dir = tmp_path / "user_scripts"
        PreValidationService(scripts_dir=str(user_dir), bundled_scripts_dir=str(bundled))
        assert (user_dir / "example.py").exists()
        assert (user_dir / "example.py").read_text() == "# example script"

    def test_does_not_overwrite_existing(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "example.py").write_text("# bundled version")
        user_dir = tmp_path / "user_scripts"
        user_dir.mkdir()
        (user_dir / "example.py").write_text("# user customized version")
        PreValidationService(scripts_dir=str(user_dir), bundled_scripts_dir=str(bundled))
        assert (user_dir / "example.py").read_text() == "# user customized version"


# ---------------------------------------------------------------------------
# Default script: read-cwd-check.py
# ---------------------------------------------------------------------------


class TestReadCwdCheckScript:
    """Test the bundled read-cwd-check.py script directly."""

    @pytest.fixture
    def script_svc(self, tmp_path: Path) -> PreValidationService:
        """Service with the real read-cwd-check.py script."""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        # Copy the real script from the repo
        repo_root = Path(__file__).resolve().parent.parent
        real_script = repo_root / "scripts" / "read-cwd-check.py"
        if real_script.exists():
            import shutil
            shutil.copy2(real_script, scripts_dir / "read-cwd-check.py")
        else:
            pytest.skip("read-cwd-check.py not found in repo")
        return PreValidationService(scripts_dir=str(scripts_dir))

    async def test_path_inside_cwd_approved(self, script_svc: PreValidationService, tmp_path: Path):
        cwd = str(tmp_path)
        target = str(tmp_path / "src" / "main.py")
        result = await script_svc.run("read-cwd-check.py", {
            "toolName": "Read",
            "toolInput": {"file_path": target},
            "cwd": cwd,
        })
        assert result.decision == "approve"

    async def test_path_outside_cwd_passthrough(self, script_svc: PreValidationService, tmp_path: Path):
        cwd = str(tmp_path / "project")
        target = "/etc/passwd"
        result = await script_svc.run("read-cwd-check.py", {
            "toolName": "Read",
            "toolInput": {"file_path": target},
            "cwd": cwd,
        })
        assert result.decision == "passthrough"

    async def test_relative_path_inside_cwd(self, script_svc: PreValidationService, tmp_path: Path):
        cwd = str(tmp_path)
        result = await script_svc.run("read-cwd-check.py", {
            "toolName": "Read",
            "toolInput": {"file_path": "src/main.py"},
            "cwd": cwd,
        })
        assert result.decision == "approve"

    async def test_missing_path_passthrough(self, script_svc: PreValidationService):
        result = await script_svc.run("read-cwd-check.py", {
            "toolName": "Read",
            "toolInput": {},
            "cwd": "/some/dir",
        })
        assert result.decision == "passthrough"

    async def test_missing_cwd_passthrough(self, script_svc: PreValidationService):
        result = await script_svc.run("read-cwd-check.py", {
            "toolName": "Read",
            "toolInput": {"file_path": "/some/file.py"},
            "cwd": "",
        })
        assert result.decision == "passthrough"

    async def test_glob_pattern_inside_cwd(self, script_svc: PreValidationService, tmp_path: Path):
        cwd = str(tmp_path)
        result = await script_svc.run("read-cwd-check.py", {
            "toolName": "Glob",
            "toolInput": {"pattern": "**/*.py"},
            "cwd": cwd,
        })
        # Glob patterns are relative, resolved against cwd → inside
        assert result.decision == "approve"

    async def test_cwd_itself_approved(self, script_svc: PreValidationService, tmp_path: Path):
        cwd = str(tmp_path)
        result = await script_svc.run("read-cwd-check.py", {
            "toolName": "LS",
            "toolInput": {"path": cwd},
            "cwd": cwd,
        })
        assert result.decision == "approve"


# ---------------------------------------------------------------------------
# Route-level integration: _pre_validation helper
# ---------------------------------------------------------------------------


class TestPreValidationRouteHelper:
    """Test the shared run_pre_validation() helper with mocks."""

    async def test_no_script_returns_none(self):
        from leash.routes._pre_validation import run_pre_validation

        handler = MagicMock()
        handler.pre_validation_script = None
        result = await run_pre_validation(
            MagicMock(), MagicMock(), handler, None, None, None, None, None, "enforce", "PreToolUse",
        )
        assert result is None

    async def test_approve_returns_response(self):
        from leash.routes._pre_validation import run_pre_validation

        mock_svc = AsyncMock()
        mock_svc.run.return_value = PreValidationResult(decision="approve", reason="safe path")

        handler = MagicMock()
        handler.pre_validation_script = "test.py"
        handler.name = "file-read-analyzer"
        handler.prompt_template = "file-read-prompt.txt"
        handler.threshold = 93

        hook_input = MagicMock()
        hook_input.hook_event_name = "PreToolUse"
        hook_input.tool_name = "Read"
        hook_input.tool_input = {"file_path": "/test/file.py"}
        hook_input.cwd = "/test"
        hook_input.session_id = "session-1"
        hook_input.provider = "claude"

        harness_client = MagicMock()
        harness_client.format_response.return_value = {"result": "approve"}

        session_mgr = AsyncMock()

        result = await run_pre_validation(
            mock_svc, hook_input, handler, harness_client,
            session_mgr, None, None, None, "enforce", "PreToolUse",
        )
        assert result is not None
        # Verify the response contains the harness-formatted approval
        import json as _json
        body = _json.loads(result.body)
        assert body == {"result": "approve"}
        # Should have logged the event
        session_mgr.record_event.assert_called_once()

    async def test_deny_returns_response(self):
        from leash.routes._pre_validation import run_pre_validation

        mock_svc = AsyncMock()
        mock_svc.run.return_value = PreValidationResult(decision="deny", reason="blocked")

        handler = MagicMock()
        handler.pre_validation_script = "test.py"
        handler.name = "test-handler"
        handler.prompt_template = None
        handler.threshold = 85

        hook_input = MagicMock()
        hook_input.hook_event_name = "PreToolUse"
        hook_input.tool_name = "Bash"
        hook_input.tool_input = {"command": "rm -rf /"}
        hook_input.cwd = "/test"
        hook_input.session_id = "session-1"
        hook_input.provider = "claude"

        harness_client = MagicMock()
        harness_client.format_response.return_value = {"result": "deny"}

        result = await run_pre_validation(
            mock_svc, hook_input, handler, harness_client,
            AsyncMock(), None, None, None, "enforce", "PreToolUse",
        )
        assert result is not None
        import json as _json
        body = _json.loads(result.body)
        assert body == {"result": "deny"}

    async def test_passthrough_returns_none(self):
        from leash.routes._pre_validation import run_pre_validation

        mock_svc = AsyncMock()
        mock_svc.run.return_value = PreValidationResult(decision="passthrough", reason="needs LLM")

        handler = MagicMock()
        handler.pre_validation_script = "test.py"
        handler.name = "test-handler"
        handler.prompt_template = None
        handler.threshold = 85

        hook_input = MagicMock()
        hook_input.hook_event_name = "PreToolUse"
        hook_input.tool_name = "Write"
        hook_input.tool_input = {}
        hook_input.cwd = "/test"
        hook_input.session_id = "session-1"
        hook_input.provider = "claude"

        result = await run_pre_validation(
            mock_svc, hook_input, handler, None,
            None, None, None, None, "enforce", "PreToolUse",
        )
        assert result is None

    async def test_observe_mode_returns_no_opinion(self):
        """In observe mode, even an approve decision returns empty JSON."""
        from leash.routes._pre_validation import run_pre_validation

        mock_svc = AsyncMock()
        mock_svc.run.return_value = PreValidationResult(decision="approve", reason="safe")

        handler = MagicMock()
        handler.pre_validation_script = "test.py"
        handler.name = "test-handler"
        handler.prompt_template = None
        handler.threshold = 85

        hook_input = MagicMock()
        hook_input.hook_event_name = "PreToolUse"
        hook_input.tool_name = "Read"
        hook_input.tool_input = {}
        hook_input.cwd = "/test"
        hook_input.session_id = "session-1"
        hook_input.provider = "claude"

        result = await run_pre_validation(
            mock_svc, hook_input, handler, MagicMock(),
            AsyncMock(), None, None, None, "observe", "PreToolUse",
        )
        assert result is not None
        # Should be empty JSON (no opinion)
        assert result.body == b"{}"

    async def test_approve_only_deny_returns_no_opinion(self):
        """In approve-only mode, a deny decision returns empty JSON (never auto-deny)."""
        from leash.routes._pre_validation import run_pre_validation

        mock_svc = AsyncMock()
        mock_svc.run.return_value = PreValidationResult(decision="deny", reason="blocked")

        handler = MagicMock()
        handler.pre_validation_script = "test.py"
        handler.name = "test-handler"
        handler.prompt_template = None
        handler.threshold = 85

        hook_input = MagicMock()
        hook_input.hook_event_name = "PreToolUse"
        hook_input.tool_name = "Bash"
        hook_input.tool_input = {"command": "rm -rf /"}
        hook_input.cwd = "/test"
        hook_input.session_id = "session-1"
        hook_input.provider = "claude"

        result = await run_pre_validation(
            mock_svc, hook_input, handler, MagicMock(),
            AsyncMock(), None, None, None, "approve-only", "PreToolUse",
        )
        assert result is not None
        assert result.body == b"{}"

    async def test_service_error_returns_none(self):
        """If the service raises, we fall through to LLM."""
        from leash.routes._pre_validation import run_pre_validation

        mock_svc = AsyncMock()
        mock_svc.run.side_effect = RuntimeError("oops")

        handler = MagicMock()
        handler.pre_validation_script = "test.py"
        handler.name = "test-handler"

        hook_input = MagicMock()
        hook_input.hook_event_name = "PreToolUse"
        hook_input.tool_name = "Read"
        hook_input.tool_input = {}
        hook_input.cwd = "/test"
        hook_input.session_id = "s1"
        hook_input.provider = "claude"

        result = await run_pre_validation(
            mock_svc, hook_input, handler, None,
            None, None, None, None, "enforce", "PreToolUse",
        )
        assert result is None


# ---------------------------------------------------------------------------
# HandlerConfig model — field serialization
# ---------------------------------------------------------------------------


class TestHandlerConfigPreValidation:
    def test_field_exists_and_defaults_to_none(self):
        from leash.models.handler_config import HandlerConfig
        h = HandlerConfig(name="test")
        assert h.pre_validation_script is None

    def test_field_serializes_to_camel_case(self):
        from leash.models.handler_config import HandlerConfig
        h = HandlerConfig(name="test", pre_validation_script="my-script.py")
        data = h.model_dump(by_alias=True)
        assert data["preValidationScript"] == "my-script.py"

    def test_field_deserializes_from_camel_case(self):
        from leash.models.handler_config import HandlerConfig
        h = HandlerConfig.model_validate({"name": "test", "preValidationScript": "my-script.py"})
        assert h.pre_validation_script == "my-script.py"

    def test_default_config_has_script_on_file_read_analyzer(self):
        from leash.config import create_default_configuration
        config = create_default_configuration()
        handlers = config.hook_handlers["PreToolUse"].handlers
        file_read = next(h for h in handlers if h.name == "file-read-analyzer")
        assert file_read.pre_validation_script == "read-cwd-check.py"

    def test_other_handlers_have_no_script(self):
        from leash.config import create_default_configuration
        config = create_default_configuration()
        handlers = config.hook_handlers["PreToolUse"].handlers
        bash = next(h for h in handlers if h.name == "bash-analyzer")
        assert bash.pre_validation_script is None
