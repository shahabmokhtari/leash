"""Persistent Copilot CLI client using ACP (Agent Client Protocol).

Uses ``copilot --acp`` as the ACP server process.  Falls back to a one-shot
``CopilotCliClient`` on failure.

Cross-platform: ACP uses standard stdin/stdout pipes which work on all
platforms (Mac, Linux, Windows).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from leash.services.acp_client_base import AcpClientBase
from leash.services.copilot_cli_client import (
    CopilotCliClient,
    parse_text_response,
    resolve_copilot_command,
)
from leash.services.llm_client_base import resolve_model_for_provider

if TYPE_CHECKING:
    from leash.config import ConfigurationManager
    from leash.models.configuration import LlmConfig
    from leash.models.llm_response import LLMResponse
    from leash.services.terminal_output_service import TerminalOutputService


class PersistentCopilotClient(AcpClientBase):
    """Persistent Copilot LLM client over ACP.

    Spawns ``copilot --acp`` (or ``gh copilot --acp``) as a persistent
    subprocess and communicates using the Agent Client Protocol (JSON-RPC
    over stdio).  Falls back to one-shot ``CopilotCliClient`` on failure.
    """

    @property
    def _label(self) -> str:
        return "copilot"

    def _get_command_and_args(self) -> tuple[str, list[str]]:
        cmd = None
        effort = ""
        if self._config_manager is not None:
            try:
                cfg = self._config_manager.get_configuration()
                cmd = cfg.llm.command
                effort = cfg.llm.effort_level or ""
            except Exception:
                pass
        if not cmd:
            cmd = self._config.command
        resolved_cmd, add_copilot_arg = resolve_copilot_command(cmd)

        # Resolve per-provider model — fall back to config only if no config_manager
        model = ""
        if self._config_manager is not None:
            try:
                model = resolve_model_for_provider(
                    self._config_manager.get_configuration(), "copilot-persistent"
                )
            except Exception:
                pass
        elif self._config.model:
            # Only use startup config when config_manager is absent entirely
            model = self._config.model

        # Fall back to config for effort only if no config_manager
        if not effort and self._config_manager is None:
            if hasattr(self._config, "effort_level") and self._config.effort_level:
                effort = self._config.effort_level

        acp_flags = [
            "--acp",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--no-auto-update",
            "--no-ask-user",
            "--log-level", "none",
        ]
        if model:
            acp_flags.extend(["--model", model])
        if effort and effort in ("low", "medium", "high", "xhigh"):
            acp_flags.extend(["--reasoning-effort", effort])
        if add_copilot_arg:
            return (resolved_cmd, ["copilot", *acp_flags])
        return (resolved_cmd, acp_flags)

    def _parse_assistant_text(self, text: str) -> LLMResponse:
        return parse_text_response(text)

    def _create_fallback_client(self):
        return CopilotCliClient(
            self._config,
            config_manager=self._config_manager,
            terminal_output=self._terminal_output,
        )
