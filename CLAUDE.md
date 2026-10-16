# Leash — Project Guide

## Overview

Leash is a Python FastAPI service that intercepts Claude Code and GitHub Copilot CLI hook events, runs LLM-based safety analysis, and returns approve/deny/passthrough decisions. It provides a web dashboard, system tray notifications, transcript browsing, and multi-provider LLM support.

## Architecture

```
Claude Code / Copilot CLI
  └─ curl hook (Claude settings; Copilot hook files)
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
- `docs/configuration.md` — Configuration, provider, hook lifecycle, and privacy reference
- `docs/images/` — Synthetic-data screenshots for the README
- `docs/capture_screenshots.py` — Isolated Playwright screenshot capture (no live backend)

## Enforcement Modes

Three modes, switchable from dashboard or CLI:

- **Observe**: Log only. LLM analysis runs if `analyzeInObserveMode=true` but response is always `{}` (no opinion). Tray shows informational-only alerts (no buttons). Log decision = "logged".
- **Approve-only**: Auto-approve safe requests (score >= threshold). Unsafe requests: show interactive tray if enabled, otherwise return `{}`. Timeout = `{}` (never auto-deny). User can approve/deny/ignore via tray.
- **Enforce**: Auto-approve safe. Unsafe: default is DENY. An available, enabled tray can show an interactive dialog (user can override). Interactive timeout = deny. Headless/unsupported environments can fall back to no tray.

Profiles select each handler's `thresholdStrict`, `thresholdModerate`, `thresholdPermissive`, or `thresholdTrust`; the base `threshold` is only a fallback. Lockdown uses threshold 101 and disables automatic approval, including pre-validation script approvals. Adaptive suggestions do not automatically change these fields.

## LLM Providers

| Provider | Config key | Type |
|----------|-----------|------|
| Anthropic API | `anthropic-api` | Direct HTTP |
| Claude CLI | `claude-cli` | One-shot subprocess |
| Claude Persistent (ACP) | `claude-persistent` | Persistent process via Agent Client Protocol |
| Claude Stream | `claude-stream` | Persistent process via `--output-format stream-json` |
| Copilot CLI | `copilot-cli` | One-shot subprocess |
| Copilot Persistent (ACP) | `copilot-persistent` | Persistent process via ACP |
| Generic REST | `generic-rest` | Configurable JSON POST API with a response extraction path |

Persistent providers cache clients per monitored session. ACP reuses a conversation up to `maxQueriesPerSession`, then creates a fresh conversation in the same process; Stream rotates by restarting its process. Providers are recreated when effective configuration changes. New configurations default to `claude-stream` and `opus`; explicitly empty CLI model settings use the CLI default.

## Testing

```bash
uv run pytest tests/ -x --tb=short -q    # Run all tests in the project environment
uv run pytest tests/test_tray_helpers.py  # Enforcement mode tests
uv run pytest tests/test_persistent_claude_stream.py  # Stream provider tests
```

## Key Design Decisions

- **Error handling**: Caught hook-processing exceptions return `{}` (no opinion). Unsuccessful provider results become score-0, category-`error` outputs and follow mode policy; `PreToolUse` in Enforce defaults to deny. Ignore explicitly returns no opinion.
- **Hook markers**: Claude uses `# leash`; Copilot uses `# copilot-analyzer` in hook descriptions and scripts for targeted cleanup.
- **Session reuse**: ACP sessions are reused up to the configured query limit to amortize `session/new` overhead; latency depends on the provider/environment.
- **Incremental log updates**: Live logs page prepends new entries to DOM (no full re-render) to preserve expanded state.
- **Platform handling**: All subprocess commands use `_resolve_command_for_platform()` for Windows `.cmd` files. Path objects throughout.
- **Model empty = CLI default**: When model config is empty, `--model` flag is omitted so the CLI uses its built-in default.
- **Copilot hook location**: Dashboard/CLI startup installation is user-level and best-effort. Repository-local `.github/hooks/` is the supported Copilot location; the service's `install_repo()` API is not exposed by the web routes. Repository hooks need explicit cleanup.
- **Local access**: Keep the service on localhost. API-key middleware is not registered by `create_app()`, so setting a key does not activate authentication. Generated hooks and browser requests also do not automatically send `X-Api-Key`.
- **Documentation screenshots**: Use synthetic fixtures via `docs/capture_screenshots.py`, never a user's live transcripts or API keys. Keep documentation tooling outside `scripts/`, which is bundled and copied into the user's runtime pre-validation directory. See `docs/images/README.md`.

## Development Workflow

This is a personal project. Single-branch (`main`) workflow:

1. **Sync** — `git pull` before starting any task.
2. **Implement** — Follow existing patterns (Pydantic models with camelCase aliases, fail-safe error handling, platform-aware subprocess calls).
3. **Test** — Run `uv run pytest tests/ -x --tb=short -q` and verify all tests pass. Add tests for new logic.
4. **Commit & push** — `git add`, `git commit`, `git push`.

For larger refactors, optionally create a short-lived feature branch (`feature/description`) and merge back into `main` via fast-forward when done.

## Config

Config at `~/.leash/config.json`. Key fields:
- `llm.provider` / `llm.model` / `llm.timeout` — LLM settings
- `enforcementMode` — "observe" | "approve-only" | "enforce"
- `analyzeInObserveMode` — Run LLM analysis in observe mode (default: true)
- `tray.enabled` / `tray.showInObserve` / `tray.showInApproveOnly` — Tray notification settings
- `hookHandlers` — Per-event handler configs with matchers, thresholds, prompt templates
