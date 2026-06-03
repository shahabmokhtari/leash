"""Integration tests for the Claude hook endpoint using FastAPI TestClient.

Since the actual route module may not exist yet, we define a minimal router
inline and mount it on a test app.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from leash.models import Configuration, HandlerConfig, HookEventConfig, HookOutput

# ---------------------------------------------------------------------------
# Passthrough tools set (matches C# ClaudeHookController.PassthroughTools)
# ---------------------------------------------------------------------------

PASSTHROUGH_TOOLS = {"AskUserQuestion", "AskFollowupQuestion"}


# ---------------------------------------------------------------------------
# Minimal Claude hook route
# ---------------------------------------------------------------------------


def create_test_app() -> FastAPI:
    """Create a minimal test app with the hook route."""

    app = FastAPI()

    @app.post("/api/hooks/claude")
    async def handle_claude_hook(request: Request, event: str = Query(None)):
        # Validate event param
        if not event or not event.strip():
            return JSONResponse(
                status_code=400,
                content={"error": "Missing 'event' query parameter"},
            )

        try:
            body = await request.json()
        except Exception:
            body = {}

        session_id = body.get("sessionId") or body.get("session_id") or ""
        tool_name = body.get("toolName") or body.get("tool_name") or ""

        # Check enforcement mode
        enforcement = getattr(request.app.state, "enforcement_mode", "observe")
        if enforcement == "observe":
            return JSONResponse(content={})

        # Missing session_id => {}
        if not session_id:
            return JSONResponse(content={})

        # Passthrough tools
        if tool_name in PASSTHROUGH_TOOLS:
            return JSONResponse(content={})

        # Try to find a handler
        config: Configuration = getattr(request.app.state, "configuration", Configuration())
        hook_config = config.hook_handlers.get(event)
        if not hook_config or not hook_config.enabled:
            return JSONResponse(content={})

        matching = None
        for h in hook_config.handlers:
            if h.matches(tool_name):
                matching = h
                break

        if not matching:
            return JSONResponse(content={})

        # Call mock handler if available
        handler_fn = getattr(request.app.state, "mock_handler", None)
        if handler_fn:
            try:
                output: HookOutput = await handler_fn(body, matching)
            except Exception:
                return JSONResponse(content={})

            if matching.mode == "log-only":
                return JSONResponse(content={})

            if output.auto_approve:
                return JSONResponse(
                    content={
                        "hookSpecificOutput": {
                            "decision": {
                                "behavior": "allow",
                            }
                        }
                    }
                )
            else:
                msg = (
                    f"Safety score {output.safety_score}/{output.threshold}. "
                    f"{output.reasoning}"
                )
                return JSONResponse(
                    content={
                        "hookSpecificOutput": {
                            "decision": {
                                "behavior": "deny",
                                "message": msg,
                            }
                        }
                    }
                )

        return JSONResponse(content={})

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClaudeHookRoute:
    @pytest.fixture()
    def app(self) -> FastAPI:
        return create_test_app()

    @pytest.fixture()
    def test_client(self, app: FastAPI) -> TestClient:
        return TestClient(app)

    def test_empty_response_when_enforcement_off(self, app: FastAPI, test_client: TestClient):
        """When enforcement is off (observe mode), should return {}."""
        app.state.enforcement_mode = "observe"
        response = test_client.post(
            "/api/hooks/claude?event=PermissionRequest",
            json={
                "sessionId": "test-session-abc123",
                "toolName": "Bash",
                "toolInput": {"command": "ls -la"},
            },
        )
        assert response.status_code == 200
        assert response.json() == {}

    def test_missing_event_returns_400(self, test_client: TestClient):
        """Missing event query parameter should return 400."""
        response = test_client.post(
            "/api/hooks/claude",
            json={"sessionId": "test-session-abc123"},
        )
        assert response.status_code == 400

    def test_empty_event_returns_400(self, test_client: TestClient):
        """Empty event query parameter should return 400."""
        response = test_client.post(
            "/api/hooks/claude?event=",
            json={"sessionId": "test-session-abc123"},
        )
        assert response.status_code == 400

    def test_missing_session_id_returns_empty(self, app: FastAPI, test_client: TestClient):
        """Missing sessionId should return {} even with enforcement on."""
        app.state.enforcement_mode = "enforce"
        response = test_client.post(
            "/api/hooks/claude?event=PermissionRequest",
            json={"toolName": "Bash"},
        )
        assert response.status_code == 200
        assert response.json() == {}

    def test_passthrough_tool_returns_empty(self, app: FastAPI, test_client: TestClient):
        """Passthrough tools should always return {}."""
        app.state.enforcement_mode = "enforce"
        app.state.configuration = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    enabled=True,
                    handlers=[HandlerConfig(name="all", matcher="*", mode="llm-analysis")],
                )
            }
        )
        response = test_client.post(
            "/api/hooks/claude?event=PermissionRequest",
            json={
                "sessionId": "test-session-abc123",
                "toolName": "AskUserQuestion",
            },
        )
        assert response.status_code == 200
        assert response.json() == {}

    def test_no_matching_handler_returns_empty(self, app: FastAPI, test_client: TestClient):
        """When no handler matches, should return {}."""
        app.state.enforcement_mode = "enforce"
        app.state.configuration = Configuration(hook_handlers={})
        response = test_client.post(
            "/api/hooks/claude?event=PermissionRequest",
            json={
                "sessionId": "test-session-abc123",
                "toolName": "Bash",
            },
        )
        assert response.status_code == 200
        assert response.json() == {}

    def test_allow_response(self, app: FastAPI, test_client: TestClient):
        """Handler that approves should return allow decision."""
        app.state.enforcement_mode = "enforce"
        app.state.configuration = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    enabled=True,
                    handlers=[
                        HandlerConfig(
                            name="test",
                            matcher="Bash",
                            mode="llm-analysis",
                            threshold=90,
                            auto_approve=True,
                        )
                    ],
                )
            }
        )

        async def mock_handler(body, config):
            return HookOutput(auto_approve=True, safety_score=96, reasoning="Safe", category="safe", threshold=90)

        app.state.mock_handler = mock_handler

        response = test_client.post(
            "/api/hooks/claude?event=PermissionRequest",
            json={
                "sessionId": "test-session-abc123",
                "toolName": "Bash",
                "toolInput": {"command": "git status"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["hookSpecificOutput"]["decision"]["behavior"] == "allow"

    def test_deny_response(self, app: FastAPI, test_client: TestClient):
        """Handler that denies should return deny decision."""
        app.state.enforcement_mode = "enforce"
        app.state.configuration = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    enabled=True,
                    handlers=[
                        HandlerConfig(
                            name="test",
                            matcher="Bash",
                            mode="llm-analysis",
                            threshold=95,
                            auto_approve=True,
                        )
                    ],
                )
            }
        )

        async def mock_handler(body, config):
            return HookOutput(
                auto_approve=False,
                safety_score=40,
                reasoning="Potentially destructive command",
                category="dangerous",
                threshold=95,
            )

        app.state.mock_handler = mock_handler

        response = test_client.post(
            "/api/hooks/claude?event=PermissionRequest",
            json={
                "sessionId": "test-session-abc123",
                "toolName": "Bash",
                "toolInput": {"command": "rm -rf /"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        decision = data["hookSpecificOutput"]["decision"]
        assert decision["behavior"] == "deny"
        assert "40" in decision["message"]
        assert "95" in decision["message"]
        assert "Potentially destructive command" in decision["message"]

    def test_handler_exception_returns_empty(self, app: FastAPI, test_client: TestClient):
        """Handler exception should return {} (error safety)."""
        app.state.enforcement_mode = "enforce"
        app.state.configuration = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    enabled=True,
                    handlers=[
                        HandlerConfig(
                            name="test",
                            matcher="Bash",
                            mode="llm-analysis",
                            threshold=90,
                            auto_approve=True,
                        )
                    ],
                )
            }
        )

        async def mock_handler(body, config):
            raise RuntimeError("LLM service unavailable")

        app.state.mock_handler = mock_handler

        response = test_client.post(
            "/api/hooks/claude?event=PermissionRequest",
            json={
                "sessionId": "test-session-abc123",
                "toolName": "Bash",
                "toolInput": {"command": "ls"},
            },
        )
        assert response.status_code == 200
        assert response.json() == {}

    def test_log_only_handler_returns_empty(self, app: FastAPI, test_client: TestClient):
        """Log-only handlers return {} (no opinion)."""
        app.state.enforcement_mode = "enforce"
        app.state.configuration = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    enabled=True,
                    handlers=[
                        HandlerConfig(name="log", matcher="Bash", mode="log-only")
                    ],
                )
            }
        )

        async def mock_handler(body, config):
            return HookOutput(category="logged")

        app.state.mock_handler = mock_handler

        response = test_client.post(
            "/api/hooks/claude?event=PermissionRequest",
            json={
                "sessionId": "test-session-abc123",
                "toolName": "Bash",
            },
        )
        assert response.status_code == 200
        assert response.json() == {}
