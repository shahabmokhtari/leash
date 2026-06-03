"""Configuration management for Leash."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiofiles
from pydantic import ValidationError

from leash.exceptions import ConfigurationException
from leash.models.configuration import Configuration, HookEventConfig
from leash.models.handler_config import HandlerConfig

logger = logging.getLogger(__name__)


def _default_config_path() -> Path:
    return Path.home() / ".leash" / "config.json"


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve a user-provided config path or return the default path."""
    return Path(config_path).expanduser() if config_path else _default_config_path()


def _prompts_dir() -> str:
    return str(Path.home() / ".leash" / "prompts")


def create_default_configuration() -> Configuration:
    """Create the default configuration with all built-in handlers."""
    pd = _prompts_dir()

    return Configuration(
        hook_handlers={
            "PreToolUse": HookEventConfig(
                enabled=True,
                handlers=[
                    HandlerConfig(
                        name="bash-analyzer",
                        matcher="^(Bash|Execute|PowerShell)$",
                        mode="llm-analysis",
                        prompt_template=f"{pd}/bash-prompt.txt",
                        threshold=95,
                        auto_approve=True,
                        config={
                            "sendCode": False,
                            "knownSafeCommands": [
                                "git status", "git log", "git diff", "git branch",
                                "npm install", "npm test", "npm run",
                                "dotnet build", "dotnet test", "dotnet restore",
                                "ls", "pwd", "cat", "head", "tail", "wc", "echo",
                                "date", "find", "which", "whoami",
                            ],
                        },
                    ),
                    HandlerConfig(
                        name="file-read-analyzer",
                        matcher="^(Read|Grep|Glob|NotebookRead|LS)$",
                        mode="llm-analysis",
                        prompt_template=f"{pd}/file-read-prompt.txt",
                        threshold=93,
                        auto_approve=True,
                        pre_validation_script="read-cwd-check.py",
                        config={"sendCode": False, "allowSendCodeIfConfigured": True},
                    ),
                    HandlerConfig(
                        name="file-write-analyzer",
                        matcher="^(Write|Edit|NotebookEdit|MultiEdit)$",
                        mode="llm-analysis",
                        prompt_template=f"{pd}/file-write-prompt.txt",
                        threshold=97,
                        auto_approve=True,
                        config={"sendCode": False},
                    ),
                    HandlerConfig(
                        name="web-analyzer",
                        matcher="WebFetch|WebSearch",
                        mode="llm-analysis",
                        prompt_template=f"{pd}/web-prompt.txt",
                        threshold=90,
                        auto_approve=True,
                        config={
                            "knownSafeDomains": [
                                "github.com", "*.microsoft.com", "npmjs.com", "pypi.org",
                                "stackoverflow.com", "docs.microsoft.com", "developer.mozilla.org",
                                "docs.python.org", "crates.io", "pkg.go.dev", "learn.microsoft.com",
                            ],
                        },
                    ),
                    HandlerConfig(
                        name="mcp-analyzer",
                        matcher="mcp__.*",
                        mode="llm-analysis",
                        prompt_template=f"{pd}/mcp-prompt.txt",
                        threshold=92,
                        auto_approve=True,
                        config={"autoApproveRegistered": True},
                    ),
                    HandlerConfig(
                        name="pre-tool-logger",
                        matcher="*",
                        mode="log-only",
                        config={"logLevel": "info"},
                    ),
                    HandlerConfig(
                        name="copilot-pre-tool",
                        matcher="*",
                        mode="llm-analysis",
                        client="copilot",
                        prompt_template=f"{pd}/pre-tool-use-prompt.txt",
                        threshold=85,
                        auto_approve=True,
                    ),
                ],
            ),
            "PostToolUse": HookEventConfig(
                enabled=True,
                handlers=[
                    HandlerConfig(
                        name="post-tool-validator",
                        enabled=False,
                        matcher="Write|Edit",
                        mode="llm-analysis",
                        prompt_template=f"{pd}/post-tool-validation-prompt.txt",
                        config={"checkForErrors": True},
                    ),
                    HandlerConfig(name="post-tool-logger", matcher="*", mode="log-only"),
                ],
            ),
            "PostToolUseFailure": HookEventConfig(
                enabled=True,
                handlers=[
                    HandlerConfig(
                        name="failure-analyzer",
                        enabled=False,
                        matcher="*",
                        mode="llm-analysis",
                        prompt_template=f"{pd}/failure-analysis-prompt.txt",
                        config={"suggestFixes": True},
                    ),
                    HandlerConfig(name="failure-logger", matcher="*", mode="log-only"),
                ],
            ),
            "UserPromptSubmit": HookEventConfig(
                enabled=True,
                handlers=[
                    HandlerConfig(name="prompt-logger", mode="log-only"),
                    HandlerConfig(
                        name="context-injector",
                        mode="context-injection",
                        prompt_template=f"{pd}/context-injection-prompt.txt",
                        config={"injectGitBranch": True, "injectRecentErrors": True},
                    ),
                ],
            ),
            "Stop": HookEventConfig(
                enabled=True,
                handlers=[HandlerConfig(name="stop-logger", mode="log-only")],
            ),
            "SessionStart": HookEventConfig(
                enabled=True,
                handlers=[
                    HandlerConfig(
                        name="session-start",
                        matcher="*",
                        mode="custom-logic",
                        config={
                            "showProtectionMessage": True,
                            "loadProjectContext": True,
                            "checkGitStatus": True,
                        },
                    )
                ],
            ),
        },
    )


class ConfigurationManager:
    """Loads, saves, and provides access to the Leash configuration."""

    def __init__(self, config_path: str | Path | None = None, config: Configuration | None = None):
        self._config_path = resolve_config_path(config_path)
        self._configuration = config or create_default_configuration()

    async def load(self) -> Configuration:
        """Load configuration from disk, or create defaults if missing."""
        if self._config_path.exists():
            try:
                async with aiofiles.open(self._config_path, "r") as f:
                    raw = await f.read()
                data = json.loads(raw)
                self._configuration = Configuration.model_validate(data)
            except json.JSONDecodeError as e:
                raise ConfigurationException(
                    f"Cannot load configuration: invalid JSON at {self._config_path}"
                ) from e
            except ValidationError as e:
                raise ConfigurationException(
                    f"Cannot load configuration: schema validation error at {self._config_path}: {e}"
                ) from e
            except OSError as e:
                raise ConfigurationException(
                    f"Cannot load configuration from {self._config_path}"
                ) from e
        else:
            self._configuration = create_default_configuration()
            await self.save()
        return self._configuration

    async def save(self) -> None:
        """Save the current configuration to disk atomically."""
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            data = self._configuration.model_dump(by_alias=True)
            raw = json.dumps(data, indent=2)
            tmp_path = self._config_path.with_suffix(".tmp")
            async with aiofiles.open(tmp_path, "w") as f:
                await f.write(raw)
            tmp_path.replace(self._config_path)
        except PermissionError as e:
            raise ConfigurationException(
                f"Cannot save configuration to {self._config_path}: Permission denied"
            ) from e
        except OSError as e:
            raise ConfigurationException(
                f"Cannot save configuration to {self._config_path}: {e}"
            ) from e

    def get_configuration(self) -> Configuration:
        return self._configuration

    async def update(self, config: Configuration) -> None:
        self._configuration = config
        await self.save()

    def get_handlers_for_hook(self, hook_event_name: str) -> list[HandlerConfig]:
        hook_config = self._configuration.hook_handlers.get(hook_event_name)
        if hook_config and hook_config.enabled:
            return [h for h in hook_config.handlers if h.enabled]
        return []

    def find_matching_handler(
        self, hook_event_name: str, tool_name: str | None, provider: str = "claude"
    ) -> HandlerConfig | None:
        all_handlers = self.get_handlers_for_hook(hook_event_name)

        # 1. Check handlers with explicit client matching this provider
        for h in all_handlers:
            if h.client and h.client.lower() == provider.lower():
                if h.matches(tool_name or ""):
                    return h

        # 2. Check deprecated copilot section (backwards compat)
        if provider.lower() == "copilot":
            copilot_handlers = self._get_copilot_handlers(hook_event_name)
            for h in copilot_handlers:
                if h.matches(tool_name or ""):
                    return h

        # 3. Fall back to handlers with client=None (applies to all)
        for h in all_handlers:
            if h.client is None and h.matches(tool_name or ""):
                return h

        return None

    def _get_copilot_handlers(self, hook_event_name: str) -> list[HandlerConfig]:
        copilot = self._configuration.copilot
        if copilot.enabled:
            hook_config = copilot.hook_handlers.get(hook_event_name)
            if hook_config and hook_config.enabled:
                return hook_config.handlers
        return []
