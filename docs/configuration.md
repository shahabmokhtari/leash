# Configuration and operations

[Back to the feature tour](../README.md#feature-tour)

Use **Leash Settings** for routine changes. This guide covers the details behind the controls: providers, policy, hook installation, storage, and API access.

## Configuration basics

The default file is `~/.leash/config.json`. `leash --config PATH` selects another file, but does **not** relocate all logs, scripts, hooks, or client transcripts.

The UI auto-saves configuration and handler changes. Back up the file before larger edits; when editing JSON, merge the fields you want to change into the existing configuration rather than replacing your handlers. `PUT /api/config` accepts a configuration document, **not a JSON Patch**.

Example settings:

```json
{
  "llm": {
    "provider": "claude-stream",
    "model": "opus",
    "timeout": 30000,
    "maxQueriesPerSession": 100,
    "maxConcurrentSessions": 20,
    "sessionIdleTimeoutMinutes": 5
  },
  "server": { "host": "localhost", "port": 5050 },
  "profiles": { "activeProfile": "moderate" },
  "enforcementMode": "observe",
  "analyzeInObserveMode": true,
  "tray": {
    "enabled": true,
    "showInObserve": true,
    "showInApproveOnly": true,
    "interactiveTimeoutSeconds": 10,
    "sound": false,
    "useLargePopup": true
  }
}
```

These are representative default values, not a complete generated configuration. To change the listening address for a launch, use `--host` and `--port`; do not expect a live settings edit to rebind Uvicorn's socket.

## Policy and thresholds

An enabled event can have several handlers. Each handler specifies a **tool matcher**, optional **client** (`claude` or `copilot`; unset means both), **mode**, and relevant settings.

| Handler mode | Purpose |
|--------------|---------|
| `llm-analysis` | Build a prompt, request a safety score, and compare it with the active threshold |
| `log-only` | Record an event without asking an LLM for a decision |
| `context-injection` | Supply additional context for supported hook events |
| `custom-logic` | Use the built-in custom handler's tool-specific logic; not an arbitrary Python plugin loader |

For the normal profile-aware hook path, thresholds come from the **handler's** fields:

| Active profile | Handler field | Field default |
|----------------|---------------|---------------|
| Strict | `thresholdStrict` | 95 |
| Moderate | `thresholdModerate` | 85 |
| Permissive | `thresholdPermissive` | 70 |
| Trust | `thresholdTrust` | 50 |
| Lockdown | Special case | Threshold 101 and automatic approval disabled |

The base `threshold` is a fallback, not the value used for a recognized active profile. In particular, editing only `threshold` will not change an existing `thresholdModerate`. The profile service also has tool-default metadata; do not confuse that with the per-handler values used by these hook routes.

Example handler inside `hookHandlers.PreToolUse.handlers`:

```json
{
  "name": "project-bash-analysis",
  "enabled": true,
  "client": "claude",
  "matcher": "Bash",
  "mode": "llm-analysis",
  "promptTemplate": "bash-prompt.txt",
  "autoApprove": true,
  "thresholdStrict": 98,
  "thresholdModerate": 90,
  "thresholdPermissive": 80,
  "thresholdTrust": 55
}
```

Start changes in Observe, inspect the applied threshold in Live Logs, then enable decisions. Adaptive threshold recommendations are advisory; apply policy changes deliberately.

### Pre-validation scripts

A handler may set `"preValidationScript": "read-cwd-check.py"`. Scripts are resolved inside Leash's script directory, normally `~/.leash/scripts/`, and run **before** LLM analysis. The bundled [workspace-read check](../scripts/read-cwd-check.py) is an example.

The script receives JSON on stdin, including `hookEvent`, `toolName`, `toolInput`, `cwd`, `provider`, `sessionId`, `handlerName`, and `threshold`. It prints one JSON result:

```json
{"decision": "passthrough", "reason": "Let the configured analyzer evaluate this request."}
```

Valid decisions are `approve`, `deny`, and `passthrough`. Missing scripts, failures, invalid output, and the five-second execution timeout fall through to normal processing. Lockdown does not accept script approvals. Mode-specific handling still applies.

Treat scripts as trusted executable code. Removing sensitive environment variables from the subprocess is not a filesystem or process sandbox.

## LLM providers

The default is `claude-stream` with model `opus`. Authenticate the selected CLI before starting Leash. Selecting Copilot hooks does not change the analyzer provider.

| Configuration | Meaning |
|---------------|---------|
| `llm.provider` | One of the seven provider keys listed in the README |
| `llm.model` | Model identifier; explicitly empty for a CLI provider means its built-in default |
| `llm.providerModels` | Provider-specific model selections remembered by the UI |
| `llm.command` | Override the CLI executable instead of relying on detection |
| `llm.timeout` | Query timeout in **milliseconds**; 1,000 to 300,000 |
| `llm.effortLevel` | Optional reasoning effort; supported values depend on the provider |
| `llm.systemPrompt` | Analyzer instructions and expected response format |

### Persistent processes

`claude-persistent`, `copilot-persistent`, and `claude-stream` cache clients per monitored session so requests need not spawn a process each time.

- **ACP:** reuses a subprocess and an ACP conversation for up to `maxQueriesPerSession` queries, then creates a new conversation inside the same subprocess.
- **Stream:** reuses its stream conversation and restarts the process when rotating accumulated context.
- **Idle cleanup:** `sessionIdleTimeoutMinutes` defaults to 5; 0 disables idle expiry.
- **Capacity:** `maxConcurrentSessions` defaults to 20. Idle entries can be evicted; when all slots are busy, a one-shot fallback avoids sharing a busy client.

Model/provider settings changes recreate affected clients. Process reuse lowers startup overhead but is not a fixed latency guarantee. The dashboard's latency metrics let you evaluate your own environment.

### Anthropic API

Set `llm.provider` to `anthropic-api` and supply `llm.apiKey`. If no key is configured, the client can fall back to `primaryApiKey` in `~/.claude/config.json`. `llm.apiBaseUrl` overrides the Anthropic endpoint; the destination must understand the request and authentication format Leash sends.

Never commit a real API key into the repository or into a shared example.

### Generic REST

This provider supports configurable JSON POST APIs; it is not limited to one vendor. A local OpenAI-compatible endpoint can use:

```json
{
  "llm": {
    "provider": "generic-rest",
    "timeout": 30000,
    "genericRest": {
      "url": "http://127.0.0.1:11434/v1/chat/completions",
      "headers": {},
      "bodyTemplate": "{\"model\":\"YOUR_INSTALLED_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"{PROMPT}\"}],\"stream\":false}",
      "responsePath": "choices[0].message.content"
    }
  }
}
```

Start the endpoint separately and replace `YOUR_INSTALLED_MODEL` with a model it serves. For authenticated services, configure the required headers locally. `{PROMPT}` is substituted with JSON-escaped prompt text. `responsePath` uses dots for object keys and brackets for array indices, such as `choices[0].message.content`; `choices.0.message.content` is not supported.

The extracted text must contain the analyzer response, for example:

```json
{
  "safetyScore": 97,
  "reasoning": "Runs a focused test suite inside the workspace.",
  "category": "safe"
}
```

Categories are `safe`, `cautious`, `risky`, or `dangerous`; scores are clamped to 0-100.

### Failure behavior

Do not assume every error is a no-opinion response. A caught hook-processing exception returns `{}`, but an unsuccessful provider result becomes a handler output with score 0 and category `error`. For `PreToolUse`, that output follows the mode's unsafe-request path: no automatic approval in Approve-Only, and **default deny in Enforce**, including without a tray or after an interactive timeout. A desktop approval can override the deny; Ignore returns no opinion.

This distinction also applies to timeouts returned as unsuccessful provider results. Check the log's category, reasoning, and final response rather than interpreting score 0 alone as an assessment of the command.

## Claude hooks

The runtime installer writes enabled events to `~/.claude/settings.json`, using curl to POST the hook's JSON stdin to `/api/hooks/claude?event=EVENT`. Entries marked `# leash` belong to Leash; unrelated hook entries are preserved.

- Default startup installation targets Claude unless previously uninstalled by the user.
- `--no-hooks` or `--hooks none` skips startup installation.
- Normal shutdown removes runtime hooks.
- Opt-in **SessionStart auto-start** is managed separately and survives shutdown.

The auto-start helper and launch metadata live under `~/.leash/`. A hard kill cannot run normal cleanup. Do not remove every hook in a client settings file when cleaning up Leash.

## Copilot hooks

**Important limitation:** the dashboard and `--hooks copilot` / `both` install **user-level** hooks at `~/.copilot/hooks/hooks.json`. Copilot CLI's supported location is repository-local `.github/hooks/`; an Installed badge is not proof that events are being delivered.

The current web API exposes user-level install/uninstall only. For repository-local setup, the Python service API can generate the files. Run this in a Python environment containing Leash, substituting your repository and the running service URL:

```python
from leash.services.copilot_hook_installer import CopilotHookInstaller

installer = CopilotHookInstaller(service_url="http://localhost:5050")
installer.install_repo(r"C:\work\my-project")
```

From a source checkout, save the snippet above as a local `setup_hooks.py` file, then run `uv run python setup_hooks.py`. This is a user-created setup script, not a bundled command; remove it afterward. A `uv tool` installation alone does not make Leash importable by an unrelated system Python. On macOS/Linux, use your repository's native absolute path.

This writes `.github/hooks/hooks.json` and per-event shell/PowerShell scripts. Without a configuration manager, runtime events default to `preToolUse`, `postToolUse`, and `sessionEnd`. `sessionStart` is managed separately.

Review the generated files before using or committing them: they contain local paths and a service address. Keep Leash running while the repository hooks are in use. **Repository-local files are not removed by the normal user-level shutdown cleanup.** Remove them explicitly through the same installer:

```python
installer.uninstall_repo(r"C:\work\my-project")
```

Verify delivery with an actual Copilot request in Live Logs. Not every Claude hook event has a Copilot equivalent.

## Storage and retention

| Data | Default location / behavior |
|------|-----------------------------|
| Service configuration | `~/.leash/config.json` |
| Leash session event history | `~/.leash/sessions/`; bounded per-session history |
| Service logs | `~/.leash/logs/`; rotating files |
| User-editable prompt templates | Source checkout: `prompts/`; installed package: `share/leash/prompts` under the install prefix |
| Pre-validation scripts | `~/.leash/scripts/` |
| Adaptive threshold data | `~/.leash/adaptive-thresholds.json` |
| Claude source transcripts | `~/.claude/projects/` |
| Copilot source transcripts | `~/.copilot/session-state/` |
| Claude hook settings | `~/.claude/settings.json` |
| Copilot user-level hook settings | `~/.copilot/hooks/hooks.json` |

Transcripts are discovered from existing client files; Leash does not need to have monitored a session for that transcript to appear. Clearing Leash's logs is not the same as deleting the source client's conversation history.

The Prompt Editor writes into the discovered application data directory, not `~/.leash/prompts/`, even when handler configuration contains a path with that prefix. Back up customized templates before upgrading or reinstalling; package/cache replacement can remove them. From a source checkout, these edits modify the repository's `prompts/` files.

Exports can contain full tool inputs and conversation text. Transcript JSON/HTML exports include all loaded entries, even if type filters hide some in the viewer. Review exports before sharing.

## Privacy and API access

**Local UI does not mean local inference.** Depending on the chosen provider, safety prompts can send code, commands, paths, and session context to an external service. Observe mode also sends analysis requests when `analyzeInObserveMode` is enabled. Disable that toggle to log without LLM analysis.

Keep the default localhost binding. Leash is a local administrative tool, not a hardened multi-user service; LLM scoring and prompt-injection mitigations do not make it a security boundary.

The source contains API-key middleware, but **`create_app()` does not register it**. Setting `LEASH_API_KEY` or `security.apiKey` therefore does not enable API authentication in the bundled application. Do not treat these configuration fields as an active protection.

If integrating the middleware in a customized deployment, its implementation checks `LEASH_API_KEY` before an `api_key` constructor argument and expects `X-Api-Key` on `/api` requests. It does not load configuration itself: an integrator must explicitly pass a configured key when registering it. With neither source set, it allows requests. Static pages are outside the check. **Generated runtime hooks and the bundled frontend do not automatically add this header**, so registration also requires caller changes. That is additional implementation work, not a supported one-switch deployment mode.

Useful read-only endpoints:

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Basic service health |
| `GET /api/hooks/status` | Hook installation and enforcement state |
| `GET /api/config` | Current configuration; treat returned values as sensitive |
| `GET /api/config/provider-models` | Provider/model capabilities |
| `GET /api/transcripts/projects` | Discovered sessions grouped by project |
| `GET /api/auditreport/{session_id}` | Session audit report as JSON |
| `GET /api/auditreport/{session_id}/html` | Session audit report as HTML |

Audit reports are API features, not a separate dashboard page. Their coverage depends on the Leash session data available; they are not a complete forensic record of all client activity.

## Implementation references

For changes that affect these docs, check the implementation rather than copying UI labels:

- [Configuration defaults](../src/leash/config.py) and [configuration models](../src/leash/models/configuration.py)
- [Handler threshold selection](../src/leash/models/handler_config.py)
- [Claude hook route](../src/leash/routes/claude_hook.py) and [Copilot hook route](../src/leash/routes/copilot_hook.py)
- [Provider lifecycle](../src/leash/services/llm_client_provider.py)
- [ACP session reuse](../src/leash/services/acp_client_base.py)
- [Copilot installer](../src/leash/services/copilot_hook_installer.py)
- [API-key middleware](../src/leash/middleware/api_key_auth.py)
- [Frontend controls](../static/)
