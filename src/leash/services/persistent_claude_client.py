"""Persistent Claude Code client using ACP (Agent Client Protocol).

Uses ``claude-agent-acp`` (npx @zed-industries/claude-agent-acp) as the ACP
server process.  Falls back to a one-shot ``ClaudeCliClient`` on failure.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from leash.services.acp_client_base import AcpClientBase, _resolve_npx_package
from leash.services.claude_cli_client import ClaudeCliClient, parse_response
from leash.services.copilot_cli_client import _parse_text_heuristic
from leash.services.llm_client_base import resolve_model_for_provider, resolve_model_name

if TYPE_CHECKING:
    from leash.config import ConfigurationManager
    from leash.models.configuration import LlmConfig
    from leash.models.llm_response import LLMResponse
    from leash.services.terminal_output_service import TerminalOutputService

logger = logging.getLogger(__name__)

_ACP_PACKAGE = "@zed-industries/claude-agent-acp"


class PersistentClaudeClient(AcpClientBase):
    """Persistent Claude Code LLM client over ACP.

    Spawns ``npx @zed-industries/claude-agent-acp`` as a persistent subprocess
    and communicates using the Agent Client Protocol (JSON-RPC over stdio).
    Falls back to one-shot ``ClaudeCliClient`` on failure.

    On Windows, ``cmd /c npx.CMD`` hangs for persistent processes because it
    doesn't properly forward stdin/stdout pipes. When the package is already
    cached, we resolve the entry-point script and invoke ``node`` directly.
    """

    @property
    def _label(self) -> str:
        return "claude"

    def _get_command_and_args(self) -> tuple[str, list[str]]:
        if sys.platform == "win32":
            resolved = _resolve_npx_package(_ACP_PACKAGE)
            if resolved is not None:
                node_exe, script_path = resolved
                logger.info("Using direct node invocation: %s %s", node_exe, script_path)
                return (node_exe, [script_path])
            logger.debug("npx package %s not cached, falling back to npx", _ACP_PACKAGE)
        return ("npx", [_ACP_PACKAGE])

    def _build_session_meta(self) -> dict:
        """Build ``_meta`` for session/new to minimise latency.

        The ``claude-agent-acp`` package defaults to loading the full Claude
        Code system prompt (~10K tokens) and all 30+ built-in tools.  For
        safety analysis we only need a minimal system prompt and zero tools,
        which reduces prompt processing from ~15K to ~3K tokens.
        """
        # Resolve the configured model so the ACP session uses it
        model = self._config.model or ""
        effort = ""
        if self._config_manager is not None:
            try:
                cfg = self._config_manager.get_configuration()
                resolved = resolve_model_for_provider(cfg, "claude-persistent")
                model = resolved if resolved is not None else model
                effort = cfg.llm.effort_level or ""
            except Exception:
                pass
        resolved_model = resolve_model_name(model) if model else ""

        meta: dict = {
            # Replace the full Claude Code system prompt with our minimal one
            "systemPrompt": self._config.system_prompt or "",
            # Disable all built-in tools (Read, Write, Bash, etc.)
            "disableBuiltInTools": True,
            "claudeCode": {
                "options": {
                    # Single-turn safety analysis — no multi-turn agent loop
                    "maxTurns": 1,
                },
            },
        }

        # Set model if configured
        if resolved_model:
            meta["claudeCode"]["options"]["model"] = resolved_model

        # Set effort level if configured (maps to Claude --effort flag)
        if effort and effort in ("low", "medium", "high"):
            meta["claudeCode"]["options"]["effort"] = effort

        return meta

    def _parse_assistant_text(self, text: str) -> LLMResponse:
        # Try structured JSON first; fall back to keyword heuristics for
        # conversational responses (the ACP agent doesn't always output JSON)
        result = parse_response(text)
        if result.success:
            return result
        if text and text.strip():
            logger.debug("JSON parsing failed for ACP response, falling back to heuristics")
            return _parse_text_heuristic(text)
        return result

    def _create_fallback_client(self):
        return ClaudeCliClient(
            self._config,
            config_manager=self._config_manager,
            terminal_output=self._terminal_output,
        )
