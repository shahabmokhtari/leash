"""Base class for persistent ACP (Agent Client Protocol) LLM clients.

Manages a persistent subprocess that speaks ACP over stdin/stdout (JSON-RPC,
newline-delimited).  Handles the initialize → session/new → session/prompt
lifecycle, process management, stderr consumption, and failure tracking.

Subclasses only need to implement ``_get_command_and_args`` and
``_parse_assistant_text`` to provide agent-specific CLI arguments and response
parsing.
"""

from __future__ import annotations

import asyncio
import glob as globmod
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from leash import __version__
from leash.models.llm_response import LLMResponse
from leash.services.llm_client_base import LLMClientBase

if TYPE_CHECKING:
    from leash.config import ConfigurationManager
    from leash.models.configuration import LlmConfig
    from leash.services.terminal_output_service import TerminalOutputService

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_FAILURES = 3

# Safety-net cap on queries per ACP session.  In practice sessions are reused
# indefinitely because context growth is negligible (~1K tokens/turn) compared
# to the expensive session/new cost (~3-5s).  This cap only exists to prevent
# truly extreme context accumulation (100+ turns → ~100K tokens).
_MAX_QUERIES_PER_SESSION = 100

# Environment variables to strip from subprocess to prevent nesting detection
_NESTING_ENV_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")

# Thread-safe JSON-RPC id counter (module-level, shared across instances)
_rpc_id_lock = threading.Lock()
_next_rpc_id = 0


def _rpc_id() -> int:
    global _next_rpc_id
    with _rpc_id_lock:
        _next_rpc_id += 1
        return _next_rpc_id


def _build_acp_subprocess_env(effort_level: str | None = None) -> dict[str, str]:
    """Build environment for an ACP subprocess."""
    env = dict(os.environ)
    for key in _NESTING_ENV_VARS:
        env.pop(key, None)
    # Disable extended thinking by default to reduce latency for safety
    # analysis.  When the user explicitly sets a high effort level, allow
    # thinking tokens so the effort setting is honored.
    if effort_level and effort_level in ("high", "xhigh"):
        env.pop("MAX_THINKING_TOKENS", None)
    else:
        env["MAX_THINKING_TOKENS"] = "0"
    return env


def _resolve_command_for_platform(cmd: str, args: list[str]) -> tuple[str, list[str]]:
    """Resolve a command for cross-platform subprocess execution.

    On Windows, .cmd/.bat files (e.g. npx.cmd, copilot.cmd) cannot be
    executed directly by CreateProcessW.  This function detects them and
    wraps the invocation with ``cmd.exe /c``.
    """
    if sys.platform != "win32":
        return cmd, args

    resolved = shutil.which(cmd)
    if resolved and resolved.lower().endswith((".cmd", ".bat")):
        return "cmd", ["/c", resolved, *args]

    return cmd, args


# Module-level cache for _resolve_npx_package results (avoids repeated
# synchronous subprocess.run("npm config get cache") calls on every process start).
# Only successful (non-None) resolutions are cached — failed lookups are retried
# so that a transient miss (e.g. package not yet downloaded) doesn't stick forever.
# Protected by a lock since _get_command_and_args runs in a thread pool via
# asyncio.to_thread, and multiple ACP clients may start concurrently.
_npx_cache: dict[str, tuple[str, str]] = {}
_npx_cache_lock = threading.Lock()


def _resolve_npx_package(package_name: str) -> tuple[str, str] | None:
    """Resolve an npx package to (node_exe, script_path) by inspecting the npm cache.

    On Windows, ``cmd /c npx.CMD`` doesn't properly forward stdin/stdout for
    persistent/interactive processes.  This function finds the cached entry
    point so we can invoke ``node <script>`` directly, bypassing cmd.exe.

    Only successful resolutions are cached.  Failed lookups (``None``) are
    retried on subsequent calls so a transient miss doesn't persist.

    Thread-safe: guarded by ``_npx_cache_lock`` since this runs in a thread
    pool via ``asyncio.to_thread``.

    Returns ``None`` if the package is not cached or node is not found.
    """
    with _npx_cache_lock:
        cached = _npx_cache.get(package_name)
        if cached is not None:
            return cached

    result = _resolve_npx_package_uncached(package_name)
    if result is not None:
        with _npx_cache_lock:
            _npx_cache[package_name] = result
    return result


def _resolve_npx_package_uncached(package_name: str) -> tuple[str, str] | None:
    """Uncached implementation of npx package resolution."""
    node = shutil.which("node")
    if node is None:
        return None

    # Determine npm cache directory
    cache_dir: str | None = None
    try:
        result = subprocess.run(
            ["npm", "config", "get", "cache"],
            capture_output=True, text=True, timeout=10,
            # npm on Windows is a .cmd; need shell to resolve it
            shell=(sys.platform == "win32"),
        )
        if result.returncode == 0 and result.stdout.strip():
            cache_dir = result.stdout.strip()
    except Exception:
        pass

    if not cache_dir and sys.platform == "win32":
        cache_dir = str(Path.home() / "AppData" / "Local" / "npm-cache")

    if not cache_dir or not Path(cache_dir).is_dir():
        return None

    # Search _npx cache directories for the package
    pattern = str(Path(cache_dir) / "_npx" / "*" / "node_modules" / package_name / "package.json")
    matches = globmod.glob(pattern)
    if not matches:
        return None

    # Use the most recently modified match
    matches.sort(key=os.path.getmtime, reverse=True)
    pkg_json_path = matches[0]

    try:
        with open(pkg_json_path, encoding="utf-8") as f:
            pkg = json.load(f)
    except Exception:
        return None

    bin_field = pkg.get("bin")
    if bin_field is None:
        return None

    # bin can be a string or a dict
    if isinstance(bin_field, str):
        entry = bin_field
    elif isinstance(bin_field, dict):
        # Prefer an entry matching the package's short name, else take first
        short_name = package_name.rsplit("/", 1)[-1]
        entry = bin_field.get(short_name) or next(iter(bin_field.values()), None)
        if entry is None:
            return None
    else:
        return None

    pkg_dir = str(Path(pkg_json_path).parent)
    script_path = str(Path(pkg_dir) / entry)
    if not Path(script_path).is_file():
        return None

    logger.debug("Resolved npx package %s -> node %s", package_name, script_path)
    return (node, script_path)


class AcpClientBase(LLMClientBase):
    """Base class for ACP-based persistent LLM clients.

    Manages the persistent process, ACP handshake, session creation, and
    prompt/response cycle.  Subclasses must implement:

    - ``_get_command_and_args()`` → (cmd, args) for subprocess_exec
    - ``_parse_assistant_text(text)`` → LLMResponse
    - ``_create_fallback_client()`` → one-shot LLMClient for fallback
    - ``_label`` property → short name for logging (e.g. "claude", "copilot")
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
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._disposed = False
        self._fallback_client = self._create_fallback_client()
        self._stderr_task: asyncio.Task[None] | None = None
        self._kill_task: asyncio.Task[None] | None = None
        self._initialized: bool = False
        self._session_id: str | None = None
        self._session_query_count: int = 0

    # -- abstract interface (subclasses must implement) -----------------------

    @property
    def _label(self) -> str:
        raise NotImplementedError

    def _get_command_and_args(self) -> tuple[str, list[str]]:
        """Return (executable, args) for the ACP subprocess."""
        raise NotImplementedError

    def _parse_assistant_text(self, text: str) -> LLMResponse:
        """Parse assistant text into an LLMResponse."""
        raise NotImplementedError

    def _create_fallback_client(self):
        """Create a one-shot fallback client."""
        raise NotImplementedError

    # -- public API ----------------------------------------------------------

    async def query(self, prompt: str) -> LLMResponse:
        """Send a prompt via ACP, falling back to one-shot on failure."""
        if self._disposed:
            raise RuntimeError(f"Persistent{self._label.title()}Client has been disposed")

        async with self._lock:
            result = await self._try_acp_query(prompt)
            if result is not None:
                self._consecutive_failures = 0
                return result

        self._push_terminal(f"persistent-{self._label}", "stderr", f"ACP query failed — falling back to one-shot {self._label}")
        logger.warning("Persistent %s ACP query failed, falling back to one-shot", self._label)
        return await self._fallback_client.query(prompt)

    # -- ACP protocol --------------------------------------------------------

    async def _send_rpc(self, proc: asyncio.subprocess.Process, method: str, params: dict | None = None, rpc_id: int | None = None) -> None:
        """Send a JSON-RPC request or notification."""
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if rpc_id is not None:
            msg["id"] = rpc_id
        line = json.dumps(msg) + "\n"
        proc.stdin.write(line.encode("utf-8"))
        await proc.stdin.drain()

    async def _read_rpc_response(self, proc: asyncio.subprocess.Process, expected_id: int, deadline: float) -> dict | None:
        """Read JSON-RPC messages until we get a response matching expected_id.

        Notifications (no "id") are processed and discarded.
        Returns the result dict or None on timeout/EOF.
        """
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if not line_bytes:
                return None

            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Is this a response to our request?
            if "id" in data and data["id"] == expected_id:
                if "error" in data:
                    logger.error("[%s] RPC error: %s", self._label, data["error"])
                    return None
                return data.get("result", {})

            # Otherwise it's a notification — log and continue
            method = data.get("method", "")
            if method == "session/update":
                self._handle_session_update(data.get("params", {}))

    def _handle_session_update(self, params: dict) -> None:
        """Process a session/update notification, extracting agent text chunks."""
        update = params.get("update", {})
        update_type = update.get("sessionUpdate", "")
        if update_type == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                text = content.get("text", "")
                if text:
                    self._push_terminal(f"persistent-{self._label}", "stdout", text[:200])

    async def _acp_initialize(self, proc: asyncio.subprocess.Process, deadline: float) -> bool:
        """Send ACP initialize handshake."""
        rid = _rpc_id()
        await self._send_rpc(proc, "initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "leash", "title": "Leash", "version": __version__},
        }, rpc_id=rid)
        result = await self._read_rpc_response(proc, rid, deadline)
        if result is None:
            logger.error("[%s] ACP initialize failed", self._label)
            return False
        logger.info("[%s] ACP initialized (protocol v%s)", self._label, result.get("protocolVersion", "?"))
        return True

    def _build_session_meta(self) -> dict | None:
        """Build the ``_meta`` dict for ``session/new``.

        Subclasses can override to customise session-level parameters.
        Returns ``None`` to omit ``_meta`` entirely (default for providers
        that don't support it, like Copilot ACP).
        """
        return None

    async def _acp_new_session(self, proc: asyncio.subprocess.Process, deadline: float) -> str | None:
        """Create a new ACP session. Returns sessionId or None."""
        rid = _rpc_id()
        params: dict = {
            "cwd": os.getcwd(),
            "mcpServers": [],
        }
        meta = self._build_session_meta()
        if meta is not None:
            params["_meta"] = meta
        await self._send_rpc(proc, "session/new", params, rpc_id=rid)
        result = await self._read_rpc_response(proc, rid, deadline)
        if result is None:
            logger.error("[%s] ACP session/new failed", self._label)
            return None
        session_id = result.get("sessionId")
        logger.info("[%s] ACP session created: %s", self._label, session_id)
        return session_id

    async def _acp_prompt(self, proc: asyncio.subprocess.Process, session_id: str, text: str, deadline: float) -> tuple[str | None, bool]:
        """Send a session/prompt and collect the response.

        Returns (collected_assistant_text, success).
        """
        rid = _rpc_id()
        await self._send_rpc(proc, "session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        }, rpc_id=rid)

        # Read until we get the response for this prompt
        collected_text: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "".join(collected_text) if collected_text else None, False
            try:
                line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                return "".join(collected_text) if collected_text else None, False
            if not line_bytes:
                logger.error("[%s] stdout closed during prompt", self._label)
                await self._kill_process()
                return None, False

            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Response to our prompt request = turn complete
            if "id" in data and data["id"] == rid:
                if "error" in data:
                    logger.error("[%s] prompt error: %s", self._label, data["error"])
                    return None, False
                return "".join(collected_text) if collected_text else None, True

            # Notification — collect text chunks
            method = data.get("method", "")
            if method == "session/update":
                params = data.get("params", {})
                update = params.get("update", {})
                update_type = update.get("sessionUpdate", "")
                if update_type == "agent_message_chunk":
                    content = update.get("content", {})
                    if content.get("type") == "text":
                        collected_text.append(content.get("text", ""))

    # -- query implementation ------------------------------------------------

    def _get_prompt_sequences(self) -> tuple[list[str], list[str]]:
        """Read prompt prefix/suffix sequences from live config."""
        prefixes: list[str] = []
        suffixes: list[str] = []
        if self._config_manager is not None:
            try:
                llm_cfg = self._config_manager.get_configuration().llm
                prefixes = llm_cfg.prompt_prefixes
                suffixes = llm_cfg.prompt_suffixes
            except Exception:
                logger.warning("[%s] Failed to read prompt sequences from config", self._label, exc_info=True)
        return prefixes, suffixes


    async def _try_acp_query(self, prompt: str) -> LLMResponse | None:
        """Attempt a query via ACP. Returns None on failure."""
        try:
            if not await self._ensure_process_running():
                return None

            proc = self._process
            if proc is None or proc.stdin is None or proc.stdout is None:
                return None

            timeout = self.current_timeout
            pid = proc.pid
            self._push_terminal(f"persistent-{self._label}", "info", f"[PID {pid}] Sending prompt ({len(prompt)} chars, timeout: {timeout}ms)")
            self._push_terminal(f"persistent-{self._label}", "stdout", f"[PID {pid}] Prompt: {self.preview_prompt(prompt)}")
            logger.info(
                "[%s] Sending prompt (%d chars, timeout: %dms): %s",
                self._label, len(prompt), timeout, self.preview_prompt(prompt),
            )

            start = time.monotonic()
            deadline = start + (timeout / 1000.0)

            # Reuse the ACP session for up to max_queries_per_session queries
            # to amortize the expensive session/new cost (~3-5s) across multiple
            # queries.  After N queries, create a fresh session to bound context
            # growth from accumulated conversation turns.
            max_queries = _MAX_QUERIES_PER_SESSION
            if self._config_manager is not None:
                try:
                    cfg_val = self._config_manager.get_configuration().llm.max_queries_per_session
                    if cfg_val is not None and cfg_val > 0:
                        max_queries = cfg_val
                except Exception:
                    pass
            session_ms = 0
            needs_new_session = (
                self._session_id is None
                or self._session_query_count >= max_queries
            )
            if needs_new_session:
                session_id = await self._acp_new_session(proc, deadline)
                if session_id is None:
                    # session/new failures are transient — don't kill the process
                    logger.warning("[%s] session/new failed, skipping (process kept alive)", self._label)
                    return None
                self._session_id = session_id
                self._session_query_count = 0
                session_ms = int((time.monotonic() - start) * 1000)
            else:
                session_id = self._session_id

            # Send prefix messages
            prefixes, suffixes = self._get_prompt_sequences()
            for prefix in prefixes:
                if not prefix:
                    continue
                logger.debug("[%s] Sending prefix: %s", self._label, prefix)
                _, ok = await self._acp_prompt(proc, session_id, prefix, deadline)
                if not ok:
                    logger.warning("[%s] Prefix did not complete: %s", self._label, prefix)
                    self._session_id = None
                    self._increment_failures_and_maybe_kill()
                    return None

            # Send actual prompt
            prompt_start = time.monotonic()
            assistant_text, got_result = await self._acp_prompt(proc, session_id, prompt, deadline)

            if not got_result:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                self._push_terminal(f"persistent-{self._label}", "stderr", f"[PID {pid}] Timeout — no result after {elapsed_ms}ms (session: {session_ms}ms)")
                logger.warning("[%s] No result after %dms (session: %dms)", self._label, elapsed_ms, session_ms)
                # Force fresh session on next attempt
                self._session_id = None
                self._increment_failures_and_maybe_kill()
                return None

            self._session_query_count += 1
            prompt_ms = int((time.monotonic() - prompt_start) * 1000)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info("[%s] Result in %dms (session: %dms, prompt: %dms, query %d/%d)", self._label, elapsed_ms, session_ms, prompt_ms, self._session_query_count, max_queries)

            # Send suffix messages
            suffix_failed = False
            for suffix in suffixes:
                if not suffix:
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    suffix_failed = True
                    break
                logger.debug("[%s] Sending suffix: %s", self._label, suffix)
                _text, ok = await self._acp_prompt(proc, session_id, suffix, deadline)
                if not ok:
                    suffix_failed = True
            # If any suffix timed out, stale data may remain in stdout;
            # force a fresh ACP session on the next query to avoid corruption.
            if suffix_failed:
                self._session_id = None

            parsed = self._parse_assistant_text(assistant_text or "")
            parsed.elapsed_ms = elapsed_ms
            self._push_terminal(f"persistent-{self._label}", "info", f"[PID {pid}] Result in {elapsed_ms}ms (session: {session_ms}ms, prompt: {prompt_ms}ms, query {self._session_query_count}/{max_queries}) — score={parsed.safety_score}, category={parsed.category}")
            return parsed

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            pid_str = f"PID {proc.pid}" if proc is not None else "no process"
            self._push_terminal(f"persistent-{self._label}", "stderr", f"[{pid_str}] Query failed: {exc}")
            logger.error("[%s] Query failed: %s", self._label, exc)
            self._increment_failures_and_maybe_kill()
            return None

    # -- process management --------------------------------------------------

    async def _ensure_process_running(self) -> bool:
        """Start the ACP process and complete handshake if not running."""
        if self._process is not None and self._process.returncode is None and self._initialized:
            return True

        await self._kill_process()

        try:
            cmd, args = await asyncio.to_thread(self._get_command_and_args)
            cmd, args = _resolve_command_for_platform(cmd, args)
            effort = None
            if self._config_manager is not None:
                try:
                    effort = self._config_manager.get_configuration().llm.effort_level
                except Exception:
                    pass
            env = _build_acp_subprocess_env(effort_level=effort)

            self._process = await asyncio.create_subprocess_exec(
                cmd, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            pid = self._process.pid
            logger.info("[%s] Process started (PID: %s)", self._label, pid)
            self._push_terminal(f"persistent-{self._label}", "info", f"Process started (PID: {pid}, cmd: {cmd} {' '.join(args)})")

            # Consume stderr in background
            proc = self._process
            self._stderr_task = asyncio.create_task(self._consume_stderr(proc))

            # Quick check that the process didn't die immediately.
            # We avoid a fixed 300ms sleep and instead poll a few times with
            # short sleeps to detect synchronous startup failures faster.
            for _ in range(3):
                if self._process.returncode is not None:
                    break
                await asyncio.sleep(0.05)
            if self._process.returncode is not None:
                self._push_terminal(f"persistent-{self._label}", "stderr", f"[PID {pid}] Process exited immediately (code {self._process.returncode})")
                logger.error("[%s] Process exited immediately with code %s", self._label, self._process.returncode)
                await self._kill_process()
                return False

            # ACP handshake (initialize only — session created per-query)
            deadline = time.monotonic() + 15.0
            if not await self._acp_initialize(self._process, deadline):
                self._push_terminal(f"persistent-{self._label}", "stderr", f"[PID {pid}] ACP handshake failed")
                await self._kill_process()
                return False

            self._push_terminal(f"persistent-{self._label}", "info", f"[PID {pid}] ACP handshake complete")

            # Mark as ready — _try_acp_query creates a fresh session per query
            self._initialized = True
            return True

        except FileNotFoundError:
            cmd, _ = await asyncio.to_thread(self._get_command_and_args)
            self._push_terminal(f"persistent-{self._label}", "stderr", f"CLI '{cmd}' not found in PATH")
            logger.error("[%s] CLI '%s' not found in PATH", self._label, cmd)
            await self._kill_process()
            return False
        except Exception as exc:
            self._push_terminal(f"persistent-{self._label}", "stderr", f"Failed to start: {exc}")
            logger.error("[%s] Failed to start process: %s", self._label, exc)
            await self._kill_process()
            return False

    async def _consume_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """Read stderr, forwarding to terminal output and debug log."""
        if proc.stderr is None:
            return
        try:
            while True:
                line_bytes = await proc.stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if line:
                    self._push_terminal(f"persistent-{self._label}", "stderr", line)
                    logger.debug("[persistent-%s stderr] %s", self._label, line)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("[%s] Stderr consumer ended unexpectedly", self._label, exc_info=True)

    def _increment_failures_and_maybe_kill(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            task = asyncio.create_task(self._kill_process())
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            self._kill_task = task

    async def _kill_process(self) -> None:
        proc = self._process
        self._process = None
        self._initialized = False
        self._session_id = None
        self._session_query_count = 0
        self._consecutive_failures = 0

        if proc is not None:
            try:
                if proc.returncode is None:
                    self._push_terminal(f"persistent-{self._label}", "info", f"[PID {proc.pid}] Terminating process")
                    logger.info("[%s] Terminating process (PID: %s)", self._label, proc.pid)
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        logger.warning("[%s] Process did not exit, killing (PID: %s)", self._label, proc.pid)
                        proc.kill()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            logger.error(
                                "[%s] Process PID %s did not exit after kill — zombie process may remain",
                                self._label, proc.pid,
                            )
            except ProcessLookupError:
                pass
            except Exception as exc:
                logger.debug("[%s] Exception killing process: %s", self._label, exc)

        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None

    async def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        await self._kill_process()
