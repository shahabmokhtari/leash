"""Capture the real frontend with synthetic API responses, without running Leash."""

import argparse
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://leash-docs.test"
STAMP = "2026-10-15T14:30:00Z"
SESSION = "demo-checkout"


def demo_data():
    sessions = [
        {
            "sessionId": SESSION,
            "title": "Add checkout validation",
            "provider": "claude",
            "branch": "feature/checkout",
            "lastModified": STAMP,
            "sizeBytes": 48200,
        },
        {
            "sessionId": "demo-tests",
            "parentSessionId": SESSION,
            "title": "Review validation tests",
            "slug": "test-review",
            "provider": "claude",
            "lastModified": STAMP,
            "sizeBytes": 12400,
        },
        {
            "sessionId": "demo-copilot",
            "title": "Document the checkout API",
            "provider": "copilot",
            "branch": "docs/checkout",
            "lastModified": STAMP,
            "sizeBytes": 24600,
        },
    ]
    projects = [
        {
            "name": "sample-store",
            "cwd": "C:\\demo\\sample-store",
            "path": "C:\\demo\\sample-store",
            "repository": "demo/sample-store",
            "sessions": sessions,
        }
    ]
    requests = [
        (
            "Bash",
            {"command": "python -m pytest tests/test_checkout.py -q"},
            97,
            "auto-approved",
            "safe",
            "Runs the project's checkout tests within the workspace.",
            "claude",
        ),
        (
            "Bash",
            {"command": "git push origin feature/checkout"},
            62,
            "denied",
            "cautious",
            "Updates a remote branch. Confirm the destination and intended changes first.",
            "claude",
        ),
        (
            "Edit",
            {"file_path": "C:\\demo\\sample-store\\checkout.py"},
            94,
            "auto-approved",
            "safe",
            "A scoped source edit within the current project.",
            "claude",
        ),
        (
            "Read",
            {"file_path": "C:\\demo\\sample-store\\README.md"},
            100,
            "script-approved",
            "safe",
            "Workspace read approved by the pre-validation script.",
            "copilot",
        ),
        (
            "Grep",
            {"pattern": "validate_checkout"},
            100,
            "auto-approved",
            "safe",
            "Read-only search for the checkout validator.",
            "copilot",
        ),
    ]
    logs = []
    for index, (tool, tool_input, score, decision, category, reasoning, provider) in enumerate(requests):
        response = (
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny" if decision == "denied" else "allow",
                    "permissionDecisionReason": reasoning,
                }
            }
            if provider == "claude"
            else {"permissionDecision": "allow"}
        )
        logs.append(
            {
                "timestamp": f"2026-10-15T14:{30 - index}:00Z",
                "type": "PreToolUse",
                "provider": provider,
                "toolName": tool,
                "toolInput": tool_input,
                "safetyScore": score,
                "threshold": 85,
                "decision": decision,
                "category": category,
                "reasoning": reasoning,
                "sessionId": SESSION if provider == "claude" else "demo-copilot",
                "handlerName": "Bash Safety" if tool == "Bash" else "Workspace Tools",
                "promptTemplate": "bash-prompt.txt" if tool == "Bash" else None,
                "responseJson": json.dumps(response),
                "elapsedMs": 820 if tool == "Bash" else 12,
            }
        )
    totals = {"inputTokens": 12400, "outputTokens": 3200, "cacheReadTokens": 8400, "cacheCreationTokens": 1600}
    transcript = [
        {
            "type": "user",
            "timestamp": STAMP,
            "message": {"role": "user", "content": "Add a clear validation error when the checkout quantity is zero."},
        },
        {
            "type": "assistant",
            "timestamp": STAMP,
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "I will validate the quantity before creating the order, then run the focused tests.",
                    },
                    {
                        "type": "tool_use",
                        "id": "demo-edit",
                        "name": "Edit",
                        "input": {
                            "file_path": "checkout.py",
                            "old_string": "def checkout(quantity):\n    return create_order(quantity)",
                            "new_string": "def checkout(quantity):\n    if quantity <= 0:\n"
                            '        raise ValueError("Quantity must be positive")\n'
                            "    return create_order(quantity)",
                        },
                    },
                ],
            },
        },
        {
            "type": "user",
            "timestamp": STAMP,
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "demo-edit", "content": "checkout.py updated successfully."},
                ],
            },
        },
        {
            "type": "assistant",
            "timestamp": STAMP,
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "**Validation added.** Positive quantities still create an order; "
                        "zero and negative quantities now raise a descriptive error.",
                    },
                ],
            },
        },
    ]
    handlers = [
        {"name": "Bash Safety", "matcher": "Bash", "mode": "llm-analysis", "promptTemplate": "bash-prompt.txt"},
        {
            "name": "File Edit Safety",
            "matcher": "Edit|Write",
            "mode": "llm-analysis",
            "promptTemplate": "file-write-prompt.txt",
        },
        {"name": "Workspace Read", "matcher": "Read", "mode": "llm-analysis", "promptTemplate": "file-read-prompt.txt"},
    ]
    for handler in handlers:
        handler.update(
            {
                "enabled": True,
                "autoApprove": True,
                "thresholdStrict": 95,
                "thresholdModerate": 85,
                "thresholdPermissive": 70,
                "thresholdTrust": 50,
            }
        )
    prompts = {p.name: p.read_text(encoding="utf-8") for p in sorted((ROOT / "prompts").glob("*.txt"))}
    return {
        "/api/dashboard/health": {"status": "healthy"},
        "/api/hooks/session-start/status": {"installed": False},
        "/api/hooks/status": {"installed": True, "enforcementMode": "enforce", "copilot": {"userInstalled": True}},
        "/api/profile": {"activeProfile": "moderate"},
        "/api/config/analyze-in-observe": {"analyzeInObserveMode": True},
        "/api/dashboard/stats": {"autoApprovedToday": 128, "deniedToday": 4, "activeSessions": 3, "avgSafetyScore": 94},
        "/api/dashboard/latency": {
            "overall": {"count": 132, "avg": 820, "median": 740, "p95": 1260, "min": 12, "max": 1820}
        },
        "/api/dashboard/trends": [
            {"date": f"2026-10-{day:02}", "approved": approved, "denied": denied}
            for day, approved, denied in [
                (9, 42, 3),
                (10, 65, 2),
                (11, 54, 1),
                (12, 92, 5),
                (13, 106, 3),
                (14, 117, 6),
                (15, 128, 4),
            ]
        ],
        "/api/dashboard/activity": logs,
        "/api/dashboard/sessions": sessions,
        "/api/logs": logs,
        "/api/transcripts/projects": projects,
        **{
            f"/api/transcripts/token-usage/{s['sessionId']}": {
                "total": totals,
                "models": {"demo-model": totals},
            }
            for s in sessions
        },
        f"/api/claude-logs/transcript/{SESSION}": transcript,
        "/api/terminal/buffer": [],
        "/api/insights": {
            "count": 1,
            "insights": [
                {
                    "id": "demo-insight",
                    "severity": "info",
                    "title": "Review your Bash threshold",
                    "description": "A few shell requests need closer inspection.",
                    "recommendation": "Open Live Logs to compare scores, reasoning, and final decisions.",
                }
            ],
        },
        "/api/adaptivethreshold/stats": {
            "toolStats": [
                {
                    "toolName": tool,
                    "totalDecisions": count,
                    "overrideCount": 0,
                    "averageSafetyScore": score,
                    "confidenceLevel": 0.9,
                }
                for tool, count, score in [("Bash", 64, 91), ("Edit", 36, 96), ("Read", 32, 100)]
            ]
        },
        "/api/config": {
            "server": {"host": "localhost", "port": 5050},
            "llm": {
                "provider": "claude-stream",
                "model": "",
                "timeout": 30000,
                "maxQueriesPerSession": 100,
                "sessionIdleTimeoutMinutes": 5,
                "maxConcurrentSessions": 20,
            },
            "hookHandlers": {"PreToolUse": {"enabled": True, "handlers": handlers}},
        },
        "/api/config/provider-models": {
            "models": {"claude-stream": [{"value": "", "label": "(Default \u2014 CLI selects)"}]},
            "effortLevels": {},
            "effortSupported": [],
        },
        "/api/prompts": prompts,
        **{f"/api/prompts/{name}": {"content": content} for name, content in prompts.items()},
    }


def capture(channel):
    output = ROOT / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    data = demo_data()
    failures = []
    static = (ROOT / "static").resolve()

    def route_request(route):
        url = urlparse(route.request.url)
        path = unquote(url.path)
        if f"{url.scheme}://{url.netloc}" != ORIGIN or route.request.method != "GET":
            failures.append(f"Unexpected request: {route.request.method} {route.request.url}")
            route.abort()
        elif path.endswith("/stream"):
            route.fulfill(status=200, content_type="text/event-stream", body=": demo capture\n\n")
        elif path in data:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data[path]))
        else:
            file = (static / (path.lstrip("/") or "index.html")).resolve()
            if file.is_relative_to(static) and file.is_file():
                route.fulfill(path=str(file), content_type=mimetypes.guess_type(file)[0] or "application/octet-stream")
            elif path == "/favicon.ico":
                route.fulfill(status=204)
            else:
                failures.append(f"Missing demo response: {path}")
                route.fulfill(status=404, body="Missing documentation fixture")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=True)
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 1050},
                device_scale_factor=1,
                locale="en-US",
                timezone_id="UTC",
                color_scheme="light",
                service_workers="block",
            )
            context.route("**/*", route_request)
            context.add_init_script("""
                localStorage.setItem('cpa-theme', 'light');
                localStorage.setItem('cpa-transcript-tokens', 'true');
                localStorage.setItem('cpa-transcript-markdown', 'true');
                localStorage.setItem('cpa-transcript-live', 'false');
                localStorage.setItem('cpa-transcript-autoscroll', 'false');
            """)
            page = context.new_page()
            page.on("pageerror", lambda error: failures.append(str(error)))

            def screenshot(name):
                page.screenshot(path=str(output / f"{name}.png"), animations="disabled")
                print(f"Captured {name}.png")

            page.goto(ORIGIN)
            expect(page.locator("#autoApprovedCount")).to_have_text("128")
            expect(page.locator("#btnToggleHooks")).to_have_text("Uninstall Hooks")
            page.set_viewport_size({"width": 1440, "height": 1140})
            page.locator("#themeToggle").click()
            screenshot("dashboard")
            page.locator("#themeToggle").click()
            page.set_viewport_size({"width": 1440, "height": 1050})
            expect(page.locator(".adaptive-card")).to_have_count(3)
            page.locator(".charts-section").scroll_into_view_if_needed()
            screenshot("insights")

            page.goto(f"{ORIGIN}/logs.html")
            expect(page.locator(".log-entry-detailed")).to_have_count(5)
            page.locator(".log-header").first.click()
            expect(page.locator("#log-detail-0")).to_be_visible()
            screenshot("live-logs")

            page.goto(f"{ORIGIN}/transcripts.html")
            expect(page.locator(".project-card")).to_have_count(1)
            page.locator(".project-card").click()
            expect(page.locator(f"#tokens-{SESSION}")).to_contain_text("22.4K")
            page.locator(f'[data-toggle-session="{SESSION}"]').click()
            page.set_viewport_size({"width": 1440, "height": 600})
            screenshot("sessions")
            page.set_viewport_size({"width": 1440, "height": 1050})
            page.locator(f'[data-session-id="{SESSION}"] .session-title').click()
            expect(page.locator(".transcript-entry")).to_have_count(4)
            screenshot("transcript")

            page.goto(f"{ORIGIN}/config.html")
            expect(page.locator("#cfg-provider")).to_have_value("claude-stream")
            expect(page.locator("#cfg-model option").first).to_have_text("(Default \u2014 CLI selects)")
            screenshot("settings")
            page.locator("#hookHandlersContent").scroll_into_view_if_needed()
            expect(page.locator(".handler-row")).to_have_count(3)
            screenshot("handlers")

            page.goto(f"{ORIGIN}/prompts.html")
            page.get_by_text("bash-prompt.txt", exact=True).click()
            expect(page.locator("#templateEditor")).to_be_enabled()
            page.locator("#testToggleBtn").click()
            page.locator("#testCwd").fill("C:\\demo\\sample-store")
            page.locator("#testToolInput").fill('{"command": "python -m pytest tests/test_checkout.py -q"}')
            screenshot("prompts")
            context.close()
        finally:
            browser.close()
    if failures:
        raise RuntimeError("\n".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="msedge", help="Installed Chromium channel (default: msedge).")
    capture(parser.parse_args().channel)
