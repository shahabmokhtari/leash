# Leash — Project Guide

## Overview

Leash is a Python FastAPI service that intercepts Claude Code and GitHub Copilot CLI hook events, runs LLM-based safety analysis, and returns approve/deny/passthrough decisions. It provides a web dashboard, system tray notifications, transcript browsing, and multi-provider LLM support.

## Architecture

```
Claude Code / Copilot CLI
  └─ curl hook (auto-installed in ~/.claude/settings.json)
      └─ POST /api/hooks/{claude|copilot}?event=PreToolUse
          └─ Handler matching (by event type + tool name regex)
              └─ LLM safety analysis (prompt template + provider)
                  └─ Enforcement decision (observe / approve-only / enforce)
                      └─ Tray notification (optional interactive approve/deny)
                          └─ JSON response to CLI (approve / deny / {} no-opinion)
```

## Key Directories

- `src/leash/` — Python backend (FastAPI app, services, models, routes)
- `src/leash/routes/` — API endpoints (auto-discovered, prefixed with `_` are private)
- `src/leash/services/` — Business logic (LLM clients, session manager, tray, etc.)
- `src/leash/models/` — Pydantic models (camelCase aliases for JSON API)
- `src/leash/handlers/` — Hook handler implementations (llm-analysis, log-only, etc.)
- `src/leash/services/tray/` — Platform-specific tray/notification (windows, mac, linux)
- `src/leash/services/harness/` — Client adapters (claude, copilot) for input/output mapping
- `static/` — Frontend (vanilla HTML/CSS/JS, no build step)
- `prompts/` — LLM prompt templates (bash-prompt.txt, etc.)
- `tests/` — pytest test suite

## Enforcement Modes

Three modes, switchable from dashboard or CLI:

- **Observe**: Log only. LLM analysis runs if `analyzeInObserveMode=true` but response is always `{}` (no opinion). Tray shows informational-only alerts (no buttons). Log decision = "logged".
- **Approve-only**: Auto-approve safe requests (score >= threshold). Unsafe requests: show interactive tray if enabled, otherwise return `{}`. Timeout = `{}` (never auto-deny). User can approve/deny/ignore via tray.
- **Enforce**: Auto-approve safe. Unsafe: default is DENY. Tray shows interactive dialog (user can override). Timeout = deny. Tray is always available in enforce mode.

## LLM Providers

| Provider | Config key | Type |
|----------|-----------|------|
| Anthropic API | `anthropic-api` | Direct HTTP (fastest) |
| Claude CLI | `claude-cli` | One-shot subprocess |
| Claude Persistent (ACP) | `claude-persistent` | Persistent process via Agent Client Protocol |
| Claude Stream | `claude-stream` | Persistent process via `--output-format stream-json` |
| Copilot CLI | `copilot-cli` | One-shot subprocess |
| Copilot Persistent (ACP) | `copilot-persistent` | Persistent process via ACP |
| Generic REST | `generic-rest` | Any OpenAI-compatible API |

Persistent providers reuse sessions across queries (no per-query session overhead). The provider is recreated when the configured model changes.

## Testing

```bash
python -m pytest tests/ -x --tb=short -q    # Run all tests (470+)
python -m pytest tests/test_tray_helpers.py  # Enforcement mode tests
python -m pytest tests/test_persistent_claude_stream.py  # Stream provider tests
```

## Key Design Decisions

- **Fail-safe**: Any error returns `{}` (no opinion). The CLI asks the user as normal.
- **Hook marker**: `# leash` comment identifies our hooks for clean uninstall.
- **Session reuse**: ACP sessions are reused across queries to avoid ~6s `session/new` overhead.
- **Incremental log updates**: Live logs page prepends new entries to DOM (no full re-render) to preserve expanded state.
- **Platform handling**: All subprocess commands use `_resolve_command_for_platform()` for Windows `.cmd` files. Path objects throughout.
- **Model empty = CLI default**: When model config is empty, `--model` flag is omitted so the CLI uses its built-in default.

## Development Workflow

This is a personal project. Single-branch (`main`) workflow:

1. **Sync** — `git pull` before starting any task.
2. **Implement** — Follow existing patterns (Pydantic models with camelCase aliases, fail-safe error handling, platform-aware subprocess calls).
3. **Test** — Run `python -m pytest tests/ -x --tb=short -q` and verify all tests pass. Add tests for new logic.
4. **Commit & push** — `git add`, `git commit`, `git push`.

For larger refactors, optionally create a short-lived feature branch (`feature/description`) and merge back into `main` via fast-forward when done.

## Config

Config at `~/.leash/config.json`. Key fields:
- `llm.provider` / `llm.model` / `llm.timeout` — LLM settings
- `enforcementMode` — "observe" | "approve-only" | "enforce"
- `analyzeInObserveMode` — Run LLM analysis in observe mode (default: true)
- `tray.enabled` / `tray.showInObserve` / `tray.showInApproveOnly` — Tray notification settings
- `hookHandlers` — Per-event handler configs with matchers, thresholds, prompt templates
