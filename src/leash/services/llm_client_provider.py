"""LLM client provider: factory, registry, and caching."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

import httpx

from leash.models.llm_response import LLMResponse
from leash.services.anthropic_api_client import AnthropicApiClient
from leash.services.claude_cli_client import ClaudeCliClient
from leash.services.copilot_cli_client import CopilotCliClient
from leash.services.generic_rest_client import GenericRestClient
from leash.services.persistent_claude_client import PersistentClaudeClient
from leash.services.persistent_claude_stream_client import PersistentClaudeStreamClient
from leash.services.persistent_copilot_client import PersistentCopilotClient

if TYPE_CHECKING:
    from leash.config import ConfigurationManager
    from leash.models.configuration import LlmConfig
    from leash.services.llm_client import LLMClient
    from leash.services.terminal_output_service import TerminalOutputService

logger = logging.getLogger(__name__)

_IDLE_TIMEOUT_MINUTES = 5
_CLEANUP_INTERVAL_SECONDS = 60  # 1 minute
_MAX_SESSION_CLIENTS = 20  # Cap on concurrent per-session subprocess clients


class _SessionClientEntry:
    """Tracks a per-session LLM client and its last-used timestamp."""

    __slots__ = ("client", "last_used", "in_use", "stale", "settings_sig")

    def __init__(self, client: LLMClient, settings_sig: str = "") -> None:
        self.client = client
        self.last_used: float = time.monotonic()
        self.in_use: int = 0
        self.stale: bool = False
        self.settings_sig: str = settings_sig

    def touch(self) -> None:
        self.last_used = time.monotonic()


class LLMClientProvider:
    """Runtime LLM provider registry and switcher.

    Maps provider names to factory functions. Reads config.llm.provider on each
    call and delegates to the matching client. Caches the active client and
    recreates it if the provider changes.

    For the "claude-persistent" provider, maintains per-session client instances
    so multiple Claude Code sessions can query the LLM in parallel.

    Implements the LLMClient protocol itself by delegating to the active client.
    """

    def __init__(
        self,
        config_manager: ConfigurationManager,
        http_client: httpx.AsyncClient | None = None,
        terminal_output: TerminalOutputService | None = None,
    ) -> None:
        if config_manager is None:
            raise ValueError("config_manager is required")
        self._config_manager = config_manager
        self._http_client = http_client
        self._terminal_output = terminal_output

        self._lock = asyncio.Lock()
        self._cached_client: LLMClient | None = None
        self._cached_provider: str | None = None
        self._cached_settings_sig: str | None = None

        self._session_lock = asyncio.Lock()
        self._session_clients: dict[str, _SessionClientEntry] = {}
        self._pending_disposal: list[_SessionClientEntry] = []

        self._cleanup_task: asyncio.Task[None] | None = None

        self._factories: dict[str, Callable[[LlmConfig], LLMClient]] = {
            "anthropic-api": self._create_anthropic_api_client,
            "claude-cli": self._create_claude_cli_client,
            "claude-persistent": self._create_persistent_claude_client,
            "claude-stream": self._create_persistent_claude_stream_client,
            "copilot-cli": self._create_copilot_cli_client,
            "copilot-persistent": self._create_persistent_copilot_client,
            "generic-rest": self._create_generic_rest_client,
        }

    @staticmethod
    def _settings_signature(config: Any) -> str:
        """Compute a hashed cache signature from all settings that affect client behavior.

        Includes every field that is baked into client instances at construction
        time so that changing any of them invalidates cached clients.  The
        result is a SHA-256 hex digest so secrets (api_key, system_prompt)
        are never exposed in logs.
        """
        llm = config.llm
        rest = llm.generic_rest
        parts = [
            llm.provider or "",
            llm.model or "",
            llm.effort_level or "",
            llm.system_prompt or "",
            llm.api_key or "",
            llm.api_base_url or "",
            llm.command or "",
            str(llm.timeout or ""),
            str(sorted((llm.provider_models or {}).items())),
            str(sorted((rest.headers if rest else {}).items())),
            str(rest.url if rest else ""),
            str(rest.body_template if rest else ""),
            str(rest.response_path if rest else ""),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def _get_or_create_http_client(self) -> httpx.AsyncClient:
        """Get or create an httpx.AsyncClient."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
        return self._http_client

    def _create_anthropic_api_client(self, _config: LlmConfig) -> LLMClient:
        return AnthropicApiClient(  # type: ignore[return-value]
            http_client=self._get_or_create_http_client(),
            config_manager=self._config_manager,
            terminal_output=self._terminal_output,
        )

    def _create_claude_cli_client(self, config: LlmConfig) -> LLMClient:
        return ClaudeCliClient(  # type: ignore[return-value]
            config=config,
            config_manager=self._config_manager,
            terminal_output=self._terminal_output,
        )

    def _create_persistent_claude_client(self, config: LlmConfig) -> LLMClient:
        return PersistentClaudeClient(  # type: ignore[return-value]
            config=config,
            config_manager=self._config_manager,
            terminal_output=self._terminal_output,
        )

    def _create_persistent_claude_stream_client(self, config: LlmConfig) -> LLMClient:
        return PersistentClaudeStreamClient(  # type: ignore[return-value]
            config=config,
            config_manager=self._config_manager,
            terminal_output=self._terminal_output,
        )

    def _create_copilot_cli_client(self, config: LlmConfig) -> LLMClient:
        return CopilotCliClient(  # type: ignore[return-value]
            config=config,
            config_manager=self._config_manager,
            terminal_output=self._terminal_output,
        )

    def _create_persistent_copilot_client(self, config: LlmConfig) -> LLMClient:
        return PersistentCopilotClient(  # type: ignore[return-value]
            config=config,
            config_manager=self._config_manager,
            terminal_output=self._terminal_output,
        )

    def _create_generic_rest_client(self, _config: LlmConfig) -> LLMClient:
        return GenericRestClient(  # type: ignore[return-value]
            http_client=self._get_or_create_http_client(),
            config_manager=self._config_manager,
            terminal_output=self._terminal_output,
        )

    async def query(self, prompt: str) -> LLMResponse:
        """Send a prompt to the currently configured LLM provider."""
        try:
            client = await self.get_client()
        except Exception as exc:
            logger.error("Failed to initialize LLM provider: %s", exc)
            return LLMResponse(
                success=False,
                safety_score=0,
                error=f"Failed to initialize LLM provider: {exc}",
                reasoning="LLM provider initialization failed",
            )
        return await client.query(prompt)

    async def get_client(self) -> LLMClient:
        """Return the LLM client for the currently configured provider.

        Lazily creates clients on first use and caches them.
        If the provider changes, the old client is disposed and a new one is created.
        """
        config = self._config_manager.get_configuration()
        provider = config.llm.provider or "anthropic-api"
        sig = self._settings_signature(config)

        settings_changed = False
        new_client: LLMClient | None = None

        async with self._lock:
            # Return cached client if settings haven't changed
            if (
                self._cached_client is not None
                and self._cached_provider == provider
                and self._cached_settings_sig == sig
            ):
                return self._cached_client

            # Check if settings changed — dispose all stale clients.
            # Also treat the first-time initialization (cached_provider is None)
            # as a settings change if session clients already exist — this handles
            # the case where the user switches from a persistent provider (which
            # bypasses get_client()) to a non-persistent one.
            settings_changed = (
                self._cached_provider is not None
                and (self._cached_provider != provider or self._cached_settings_sig != sig)
            )
            if not settings_changed and self._cached_provider is None and self._session_clients:
                settings_changed = True

            # Dispose old shared client if switching providers or settings
            if self._cached_client is not None and settings_changed:
                logger.info(
                    "Recreating LLM client (provider: %s→%s, sig: %s→%s)",
                    self._cached_provider, provider, self._cached_settings_sig, sig,
                )
                await self._dispose_client(self._cached_client)
                self._cached_client = None
                self._cached_provider = None
                self._cached_settings_sig = None

            factory = self._factories.get(provider)
            if factory is None:
                logger.warning("Unknown LLM provider '%s', falling back to anthropic-api", provider)
                factory = self._factories["anthropic-api"]

            new_client = factory(config.llm)
            self._cached_client = new_client
            self._cached_provider = provider
            self._cached_settings_sig = sig
            logger.info("Initialized LLM provider: %s (model: %s)", provider, config.llm.model or "default")

            # Start cleanup task if not running
            if self._cleanup_task is None or self._cleanup_task.done():
                self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        # Invalidate per-session clients OUTSIDE _lock to avoid ABBA deadlock.
        # _lock is released before acquiring _session_lock.
        if settings_changed and self._session_clients:
            stale_dispose: list[_SessionClientEntry] = []
            async with self._session_lock:
                keep: dict[str, _SessionClientEntry] = {}
                for sid, entry in self._session_clients.items():
                    # A concurrent get_client_for_session() may have already
                    # created a client with the NEW settings — keep it.
                    if entry.settings_sig == sig:
                        keep[sid] = entry
                        continue
                    if entry.in_use <= 0:
                        stale_dispose.append(entry)
                    else:
                        entry.stale = True
                        self._pending_disposal.append(entry)
                self._session_clients.clear()
                self._session_clients.update(keep)
                logger.info("Cleared stale per-session clients (settings changed, %d idle, %d busy-pending, %d kept)",
                            len(stale_dispose), len(self._pending_disposal), len(keep))
            # Dispose idle entries outside the lock
            for entry in stale_dispose:
                await self._dispose_client(entry.client)

        return new_client  # type: ignore[return-value]

    # Providers that get a dedicated client per session.  ACP providers use
    # lightweight Node.js processes where the one-time cold start (~2-4s on
    # first request) is worth the trade-off: subsequent requests run on a
    # warm process with no lock contention across sessions.
    # claude-stream uses a persistent subprocess with accumulated conversation
    # history — it MUST be per-session to prevent context bleed between sessions.
    _PERSISTENT_PROVIDERS = frozenset({"claude-persistent", "claude-stream", "copilot-persistent"})

    async def get_client_for_session(self, session_id: str | None) -> tuple[LLMClient, _SessionClientEntry | None]:
        """Return an LLM client and its tracking entry for the given session.

        Persistent providers get a dedicated client per session so that each
        Claude Code / Copilot session has its own subprocess.  Non-persistent
        providers share a single client since they are stateless.

        Returns ``(client, entry)`` where ``entry`` is the tracking object
        that callers MUST pass to ``release_session()`` when done.  For
        non-persistent providers, ``entry`` is ``None``.
        """
        if not session_id:
            return await self.get_client(), None

        config = self._config_manager.get_configuration()
        provider = config.llm.provider or "anthropic-api"

        # Non-persistent providers share a single client
        if provider not in self._PERSISTENT_PROVIDERS:
            return await self.get_client(), None

        sig = self._settings_signature(config)
        to_dispose: list = []  # Clients to dispose AFTER releasing the lock
        result_client = None
        result_entry = None
        need_fallback = False

        try:
            async with self._session_lock:
                entry = self._session_clients.get(session_id)
                if entry is not None:
                    if entry.settings_sig == sig:
                        entry.touch()
                        entry.in_use += 1
                        result_client = entry.client
                        result_entry = entry
                    else:
                        # Settings changed — displace old entry
                        if entry.in_use <= 0:
                            to_dispose.append(entry.client)
                        else:
                            entry.stale = True
                            self._pending_disposal.append(entry)
                        del self._session_clients[session_id]

                if result_client is None:
                    # Need to create a new client — check capacity first.
                    # Count both active and pending-disposal clients to prevent
                    # unbounded subprocess growth during config churn.
                    # NOTE: _pending_disposal entries are conservatively counted
                    # toward capacity.  Under rapid config churn with long queries,
                    # this may force temporary one-shot fallbacks until the cleanup
                    # cycle runs (every 60s).  This is intentional — subprocess
                    # exhaustion is worse than temporary fallback degradation.
                    max_sessions = config.llm.max_concurrent_sessions or _MAX_SESSION_CLIENTS
                    total_clients = len(self._session_clients) + len(self._pending_disposal)
                    oldest_sid: str | None = None
                    if total_clients >= max_sessions:
                        idle_entries = [
                            (sid, e) for sid, e in self._session_clients.items() if e.in_use <= 0
                        ]
                        if idle_entries:
                            # Defer eviction until after factory succeeds to
                            # avoid wasting a warm client on factory failure.
                            oldest_sid = min(idle_entries, key=lambda x: x[1].last_used)[0]
                        else:
                            # All slots busy — use one-shot fallback (see below)
                            need_fallback = True
                            logger.warning("All %d session clients are busy — using one-shot fallback for session %s", max_sessions, session_id)

                    if not need_fallback:
                        factory = self._factories.get(provider, self._factories["anthropic-api"])
                        result_client = factory(config.llm)
                        # Factory succeeded — now safe to evict the idle entry
                        if total_clients >= max_sessions and oldest_sid:
                            evicted = self._session_clients.pop(oldest_sid)
                            to_dispose.append(evicted.client)
                            logger.info("Evicted oldest idle session client (session %s) — at capacity (%d)", oldest_sid, max_sessions)
                        result_entry = _SessionClientEntry(result_client, settings_sig=sig)
                        result_entry.in_use = 1
                        self._session_clients[session_id] = result_entry
                        logger.info("Created per-session %s client for session %s", provider, session_id)

                        if self._cleanup_task is None or self._cleanup_task.done():
                            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        finally:
            # Always dispose queued clients, even if factory() or other code threw
            for client in to_dispose:
                await self._dispose_client(client)

        if need_fallback:
            # Use the one-shot (non-persistent) variant of this provider to
            # avoid context bleed from sharing a stateful persistent client.
            fallback_map = {
                "claude-persistent": "claude-cli",
                "claude-stream": "claude-cli",
                "copilot-persistent": "copilot-cli",
            }
            fallback_provider = fallback_map.get(provider, provider)
            fallback_factory = self._factories.get(fallback_provider, self._factories["anthropic-api"])
            result_client = fallback_factory(config.llm)
            logger.info("Created one-shot %s fallback client for busy session %s", fallback_provider, session_id)
            return result_client, None

        return result_client, result_entry

    async def release_session(self, entry: _SessionClientEntry | None) -> None:
        """Decrement the in-use counter for a session client after a query completes.

        If the entry was displaced (marked stale) and is no longer in use,
        disposes its subprocess to prevent leaks.
        """
        if entry is None:
            return
        dispose_client = None
        async with self._session_lock:
            if entry.in_use > 0:
                entry.in_use -= 1
            # If this was a displaced entry that's now idle, dispose it
            if entry.stale and entry.in_use <= 0:
                try:
                    self._pending_disposal.remove(entry)
                except ValueError:
                    pass
                dispose_client = entry.client
        if dispose_client is not None:
            logger.info("Disposing stale displaced session client")
            await self._dispose_client(dispose_client)

    async def _periodic_cleanup(self) -> None:
        """Periodically clean up idle session clients."""
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            await self._cleanup_idle_sessions()

    async def _cleanup_idle_sessions(self) -> None:
        """Dispose session clients that have been idle for more than the timeout.

        Collects expired entries under the lock, then disposes them outside
        the lock to avoid blocking ``get_client_for_session`` during slow
        subprocess termination.
        """
        timeout_minutes = _IDLE_TIMEOUT_MINUTES
        try:
            timeout_minutes = self._config_manager.get_configuration().llm.session_idle_timeout_minutes
        except Exception:
            pass
        if timeout_minutes <= 0:
            return  # disabled
        cutoff = time.monotonic() - (timeout_minutes * 60)
        expired: list[tuple[str, _SessionClientEntry]] = []

        async with self._session_lock:
            for sid, entry in self._session_clients.items():
                if entry.last_used < cutoff and entry.in_use <= 0:
                    expired.append((sid, entry))
            for sid, _ in expired:
                del self._session_clients[sid]

            # Also collect pending-disposal entries that are no longer in use
            still_pending: list[_SessionClientEntry] = []
            for entry in self._pending_disposal:
                if entry.in_use <= 0:
                    expired.append(("(displaced)", entry))
                else:
                    still_pending.append(entry)
            self._pending_disposal = still_pending

        # Dispose outside the lock so other sessions aren't blocked
        for sid, entry in expired:
            logger.info("Disposing idle per-session client for session %s", sid)
            await self._dispose_client(entry.client)

    @staticmethod
    async def _dispose_client(client: LLMClient) -> None:
        """Dispose a client if it has a dispose method."""
        if hasattr(client, "dispose"):
            try:
                await client.dispose()  # type: ignore[attr-defined]
            except Exception as exc:
                logger.debug("Exception while disposing client: %s", exc)

    async def dispose(self) -> None:
        """Clean up all clients and the cleanup task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Dispose all session clients — snapshot under lock, dispose outside
        # to avoid blocking release_session during slow subprocess teardown.
        session_dispose: list = []
        pending_dispose: list = []
        async with self._session_lock:
            session_dispose = [e.client for e in self._session_clients.values()]
            self._session_clients.clear()
            pending_dispose = [e.client for e in self._pending_disposal]
            self._pending_disposal.clear()
        for client in session_dispose:
            await self._dispose_client(client)
        for client in pending_dispose:
            await self._dispose_client(client)

        # Dispose cached client
        async with self._lock:
            if self._cached_client is not None:
                await self._dispose_client(self._cached_client)
                self._cached_client = None
                self._cached_provider = None
                self._cached_settings_sig = None

        # Close http client if we created it
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
