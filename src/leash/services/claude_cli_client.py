"""One-shot Claude CLI subprocess LLM client."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from leash.models.llm_response import LLMResponse
from leash.services.cli_process_runner import run as run_cli
from leash.services.llm_client_base import MAX_OUTPUT_SIZE, LLMClientBase, resolve_model_for_provider, resolve_model_name

if TYPE_CHECKING:
    from leash.config import ConfigurationManager
    from leash.models.configuration import LlmConfig
    from leash.services.terminal_output_service import TerminalOutputService

logger = logging.getLogger(__name__)

_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9\-._]+$")
_MAX_RETRIES = 3
_RETRY_DELAY_S = 0.5

_VALID_CATEGORIES = frozenset({"safe", "cautious", "risky", "dangerous", "unknown", "error"})

# Environment variables to strip from subprocess to prevent nesting detection
_NESTING_ENV_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")


class ClaudeCliClient(LLMClientBase):
    """LLM client that runs `claude` as a one-shot subprocess.

    Validates model names, builds CLI arguments, sets up an isolated config
    directory for the subprocess, and parses JSON safety analysis from the output.
    Retries up to 3 times with 500ms delay on transient failures.
    """

    def __init__(
        self,
        config: LlmConfig,
        config_manager: ConfigurationManager | None = None,
        terminal_output: TerminalOutputService | None = None,
    ) -> None:
        super().__init__(config_manager=config_manager, initial_config=config, terminal_output=terminal_output)
        if config is None:
            raise ValueError("config is required")
        self._config = config
        if config.model and not _is_valid_model_name(config.model):
            raise ValueError("Model name contains invalid characters")

    async def query(self, prompt: str) -> LLMResponse:
        """Send a prompt to the Claude CLI and return a structured response."""
        timeout = self.current_timeout
        self._push_terminal("claude-cli", "info", f"Querying Claude CLI ({len(prompt)} chars, timeout: {timeout}ms)")
        self._push_terminal("claude-cli", "stdout", f"Prompt: {self.preview_prompt(prompt)}")
        logger.info(
            "Querying Claude CLI (%d chars, timeout: %dms): %s",
            len(prompt),
            timeout,
            self.preview_prompt(prompt),
        )
        total_start = time.monotonic()

        for attempt in range(1, _MAX_RETRIES + 1):
            start = time.monotonic()
            try:
                cmd = "claude"
                if self._config_manager is not None:
                    try:
                        configured_cmd = self._config_manager.get_configuration().llm.command
                        if configured_cmd:
                            cmd = configured_cmd
                    except Exception:
                        pass

                args = self._build_command_args(prompt)
                env = _build_subprocess_env()

                result = await run_cli(cmd, args, timeout, "claude-cli", env=env, terminal_output=self._terminal_output)
                elapsed_ms = int((time.monotonic() - start) * 1000)

                response = parse_response(result.output)
                response.elapsed_ms = elapsed_ms
                self._push_terminal("claude-cli", "info", f"Completed in {elapsed_ms}ms (score={response.safety_score})")
                logger.info("Claude CLI query completed in %dms", elapsed_ms)
                return response

            except TimeoutError:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "Claude CLI attempt %d/%d timed out after %dms, retrying...",
                        attempt,
                        _MAX_RETRIES,
                        elapsed_ms,
                    )
                    await asyncio.sleep(_RETRY_DELAY_S)
                else:
                    total_elapsed = int((time.monotonic() - total_start) * 1000)
                    logger.warning("All %d Claude CLI attempts timed out (%dms total)", _MAX_RETRIES, total_elapsed)
                    return self.create_timeout_response("LLM query", _MAX_RETRIES, timeout, total_elapsed)

            except FileNotFoundError:
                self._push_terminal("claude-cli", "stderr", "Claude CLI not found in PATH")
                return self.create_failure_response(
                    "Claude CLI not found - ensure 'claude' command is installed and in PATH",
                    "Claude CLI is not installed or not in PATH",
                )

            except RuntimeError:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if attempt < _MAX_RETRIES:
                    logger.warning("Claude CLI attempt %d/%d failed, retrying...", attempt, _MAX_RETRIES)
                    await asyncio.sleep(_RETRY_DELAY_S)
                else:
                    logger.error("Claude CLI query failed after %d attempts", _MAX_RETRIES)
                    return self.create_failure_response("LLM query failed", "LLM query failed due to runtime error")

        return self.create_retries_exhausted_response("LLM")

    def _build_command_args(self, prompt: str) -> list[str]:
        """Build CLI arguments for the claude command."""
        model = self._config.model
        effort = self._config.effort_level
        if self._config_manager is not None:
            try:
                cfg = self._config_manager.get_configuration()
                resolved = resolve_model_for_provider(cfg, "claude-cli")
                # Empty string = "use provider default" — don't fall back to global model
                model = resolved if resolved is not None else model
                effort = cfg.llm.effort_level or effort
            except Exception:
                pass

        args = [
            "-p",
            "--output-format",
            "text",
            "--no-session-persistence",
            "--dangerously-skip-permissions",
            "--settings",
            '{"disableAllHooks":true,"enableAllProjectMcpServers":false,"enableMcpServerCreation":false,"customSlashCommands":{}}',
        ]
        if model:
            args.extend(["--model", resolve_model_name(model)])
        if effort and effort in ("low", "medium", "high"):
            args.extend(["--effort", effort])
        if self._config.system_prompt:
            args.extend(["--system-prompt", self._config.system_prompt])
        args.append(prompt)
        return args


def parse_response(output: str) -> LLMResponse:
    """Parse LLM text output into a structured LLMResponse.

    Extracts JSON by finding the first '{', uses brace counting to isolate the
    JSON object, then parses safetyScore, reasoning, and category fields.
    """
    if len(output) > MAX_OUTPUT_SIZE:
        return _create_error_response("LLM output exceeded maximum size limit")

    json_string = _extract_json_from_output(output)
    if json_string is None:
        return _create_error_response("No JSON object found")

    return _parse_json_to_response(json_string)


def _extract_json_from_output(output: str) -> str | None:
    """Extract the safety-analysis JSON object from mixed text output.

    Scans all top-level ``{...}`` blocks and returns the first one that
    contains a ``safetyScore`` key.  Falls back to the first parseable JSON
    object if none contain the key, preserving backward compatibility.
    """
    candidates: list[str] = []
    idx = 0
    while idx < len(output):
        start = output.find("{", idx)
        if start < 0:
            break

        # Try parsing from this { to end of string
        candidate = None
        try:
            json.loads(output[start:])
            candidate = output[start:]
        except json.JSONDecodeError:
            # Fall back to brace counting from this position
            candidate = _extract_json_by_brace_counting(output, start)

        if candidate is not None:
            candidates.append(candidate)
            # Skip past this candidate to look for more
            idx = start + len(candidate)
        else:
            idx = start + 1

    if not candidates:
        return None

    # Prefer the candidate that contains "safetyScore"
    for c in candidates:
        if "safetyScore" in c:
            try:
                json.loads(c)
                return c
            except json.JSONDecodeError:
                pass

    # Fall back to first parseable candidate
    for c in candidates:
        try:
            json.loads(c)
            return c
        except json.JSONDecodeError:
            pass

    return None


def _extract_json_by_brace_counting(output: str, start_idx: int) -> str | None:
    """Extract JSON by counting matched braces."""
    brace_count = 0
    end_idx = start_idx

    for i in range(start_idx, len(output)):
        if output[i] == "{":
            brace_count += 1
        elif output[i] == "}":
            brace_count -= 1
            if brace_count == 0:
                end_idx = i + 1
                break

    if brace_count == 0:
        return output[start_idx:end_idx]
    return None


def _parse_json_to_response(json_string: str) -> LLMResponse:
    """Parse a JSON string into an LLMResponse.

    Detects nested JSON: if the ``reasoning`` field itself contains a JSON
    object with a ``safetyScore`` key, the inner values are used instead.
    This handles LLMs that wrap the real response inside the reasoning field.
    """
    try:
        data = json.loads(json_string)
    except json.JSONDecodeError as exc:
        return _create_error_response(f"Invalid JSON format: {exc}")

    try:
        safety_score = int(data["safetyScore"])
    except (KeyError, ValueError, TypeError) as exc:
        return _create_error_response(f"Missing or invalid safetyScore: {exc}")

    reasoning = str(data.get("reasoning", "No reasoning provided"))
    category = str(data.get("category", "unknown"))

    # Detect nested JSON in reasoning — the LLM sometimes wraps the real
    # response inside the reasoning field as a JSON string.
    if reasoning.lstrip().startswith("{") and "safetyScore" in reasoning:
        try:
            inner = json.loads(reasoning)
            if isinstance(inner, dict) and "safetyScore" in inner:
                try:
                    safety_score = int(inner["safetyScore"])
                except (ValueError, TypeError):
                    pass
                else:
                    if "category" in inner:
                        category = str(inner["category"])
                    if "reasoning" in inner:
                        reasoning = str(inner["reasoning"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Clamp to valid range
    safety_score = max(0, min(safety_score, 100))

    if category not in _VALID_CATEGORIES:
        category = "unknown"

    return LLMResponse(
        success=True,
        safety_score=safety_score,
        reasoning=reasoning,
        category=category,
    )


def _create_error_response(reasoning: str) -> LLMResponse:
    """Create a failed LLMResponse for parse errors."""
    return LLMResponse(
        success=False,
        safety_score=0,
        reasoning=reasoning,
    )


def _is_valid_model_name(model: str) -> bool:
    """Validate that a model name contains only safe characters."""
    if not model or len(model) > 64:
        return False
    return _MODEL_NAME_RE.match(model) is not None



def read_anthropic_api_key() -> str | None:
    """Read the Anthropic API key from the main Claude config (~/.claude/config.json).

    Returns None if not found.
    """
    try:
        config_path = Path.home() / ".claude" / "config.json"
        if not config_path.exists():
            return None
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data.get("primaryApiKey")
    except Exception:
        return None


def _build_subprocess_env() -> dict[str, str]:
    """Build environment variables for a Claude subprocess.

    Removes nesting-detection variables so the subprocess doesn't
    think it's running inside another Claude Code session.
    Auth credentials are inherited from the user's normal config.
    """
    env = dict(os.environ)

    # Remove Claude Code nesting-detection env vars
    for key in _NESTING_ENV_VARS:
        env.pop(key, None)

    return env
