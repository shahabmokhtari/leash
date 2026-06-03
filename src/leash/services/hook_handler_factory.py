"""Factory for creating hook handlers by mode string."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leash.handlers.base import HookHandler
    from leash.services.llm_client_provider import LLMClientProvider
    from leash.services.prompt_template_service import PromptTemplateService
    from leash.services.session_manager import SessionManager

logger = logging.getLogger(__name__)


class HookHandlerFactory:
    """Creates hook handler instances based on mode strings."""

    def __init__(
        self,
        llm_client_provider: LLMClientProvider | None = None,
        prompt_template_service: PromptTemplateService | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        self._llm_client_provider = llm_client_provider
        self._prompt_template_service = prompt_template_service
        self._session_manager = session_manager

    async def create(
        self,
        mode: str,
        prompt_template_name: str | None = None,
        session_id: str | None = None,
    ) -> tuple[HookHandler, object | None]:
        """Create a handler for the given mode.

        Args:
            mode: The handler mode string (e.g. "llm-analysis", "log-only").
            prompt_template_name: Optional name of a prompt template to load.
            session_id: Optional session ID for LLM client selection.

        Returns:
            A tuple of (HookHandler, session_entry) where session_entry must be
            passed to ``LLMClientProvider.release_session()`` when done.  For
            non-persistent providers, session_entry is ``None``.

        Raises:
            ValueError: If the mode is not supported.
        """
        from leash.handlers.context_injection import ContextInjectionHandler
        from leash.handlers.custom_logic import CustomLogicHandler
        from leash.handlers.llm_analysis import LLMAnalysisHandler
        from leash.handlers.log_only import LogOnlyHandler

        # Load prompt template content if name is provided
        prompt_template: str | None = None
        if prompt_template_name and self._prompt_template_service:
            prompt_template = self._prompt_template_service.get_template(prompt_template_name)

        # Get LLM client for this session (only for modes that need it)
        llm_client = None
        session_entry = None
        if self._llm_client_provider and mode in ("llm-analysis", "llm-validation"):
            llm_client, session_entry = await self._llm_client_provider.get_client_for_session(session_id)

        try:
            if mode in ("llm-analysis", "llm-validation"):
                return LLMAnalysisHandler(llm_client=llm_client, prompt_template=prompt_template), session_entry
            elif mode == "log-only":
                return LogOnlyHandler(), session_entry
            elif mode == "context-injection":
                return ContextInjectionHandler(), session_entry
            elif mode == "custom-logic":
                return CustomLogicHandler(session_manager=self._session_manager), session_entry
            else:
                raise ValueError(f"Handler mode '{mode}' is not supported")
        except Exception:
            # Release the session entry if handler creation fails to prevent
            # a permanent in_use leak on the session client
            if self._llm_client_provider and session_entry is not None:
                await self._llm_client_provider.release_session(session_entry)
            raise
