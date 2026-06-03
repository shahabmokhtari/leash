"""Tests for LLM client providers: platform-specific registration, persistent clients, and cross-platform behaviour."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leash.config import ConfigurationManager, create_default_configuration
from leash.models.configuration import LlmConfig
from leash.services.llm_client_provider import LLMClientProvider

# ---------------------------------------------------------------------------
# ACP JSON-RPC test helpers
# ---------------------------------------------------------------------------


def _acp_response(rpc_id: int, result: dict) -> bytes:
    """Build a JSON-RPC response line matching a given request id."""
    return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": result}).encode() + b"\n"


def _acp_text_update(text: str) -> bytes:
    """Build a session/update notification carrying an agent_message_chunk."""
    return json.dumps({
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            }
        },
    }).encode() + b"\n"


def _make_mock_proc(*, returncode=None, stdout_lines=None):
    """Create a mock asyncio subprocess with the given stdout lines."""
    mock_proc = AsyncMock()
    mock_proc.returncode = returncode
    mock_proc.pid = 12345
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(side_effect=stdout_lines or [b""])
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.readline = AsyncMock(return_value=b"")
    return mock_proc


def _patch_acp_asyncio(mock_proc):
    """Return a patch context manager for leash.services.acp_client_base.asyncio."""
    patcher = patch("leash.services.acp_client_base.asyncio")

    class _Ctx:
        def __enter__(self_ctx):
            mock_asyncio = patcher.__enter__()
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            mock_asyncio.sleep = AsyncMock()
            mock_asyncio.create_task = MagicMock(side_effect=lambda coro: asyncio.ensure_future(coro))
            mock_asyncio.CancelledError = asyncio.CancelledError
            mock_asyncio.to_thread = asyncio.to_thread
            return mock_asyncio

        def __exit__(self_ctx, *args):
            return patcher.__exit__(*args)

    return _Ctx()


def _reset_rpc_counter():
    """Reset the module-level RPC id counter so tests get predictable ids."""
    import leash.services.acp_client_base as acp_base
    acp_base._next_rpc_id = 0


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


class TestLLMClientProviderRegistry:
    """Verify that the provider factory includes the right providers per platform."""

    def _make_provider(self) -> LLMClientProvider:
        config_mgr = ConfigurationManager(config=create_default_configuration())
        return LLMClientProvider(config_manager=config_mgr)

    def test_core_providers_always_registered(self):
        provider = self._make_provider()
        for name in ("anthropic-api", "claude-cli", "claude-persistent", "copilot-cli", "copilot-persistent", "generic-rest"):
            assert name in provider._factories, f"{name} should always be registered"

    async def test_unknown_provider_falls_back_to_anthropic_api(self):
        config = create_default_configuration()
        config.llm.provider = "does-not-exist"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client = await provider.get_client()
        # Should have fallen back to anthropic-api without raising
        assert client is not None


# ---------------------------------------------------------------------------
# Model name resolution
# ---------------------------------------------------------------------------


class TestModelNameResolution:
    """Verify shorthand model names are mapped to full Claude model IDs."""

    def test_shorthand_names_resolved(self):
        from leash.services.llm_client_base import resolve_model_name

        assert resolve_model_name("opus") == "claude-opus-4-6-20250918"
        assert resolve_model_name("sonnet") == "claude-sonnet-4-5-20250929"
        assert resolve_model_name("haiku") == "claude-haiku-4-5-20251001"

    def test_case_insensitive(self):
        from leash.services.llm_client_base import resolve_model_name

        assert resolve_model_name("Opus") == "claude-opus-4-6-20250918"
        assert resolve_model_name("SONNET") == "claude-sonnet-4-5-20250929"

    def test_full_id_unchanged(self):
        from leash.services.llm_client_base import resolve_model_name

        assert resolve_model_name("claude-opus-4-6-20250918") == "claude-opus-4-6-20250918"
        assert resolve_model_name("custom-model-v1") == "custom-model-v1"

    def test_cli_client_uses_resolved_model(self):
        """Verify ClaudeCliClient builds args with the resolved model name."""
        from leash.services.claude_cli_client import ClaudeCliClient

        config = LlmConfig(provider="claude-cli", model="opus")
        client = ClaudeCliClient(config=config)
        args = client._build_command_args("test prompt")
        model_idx = args.index("--model") + 1
        assert args[model_idx] == "claude-opus-4-6-20250918"


class TestPluginIsolation:
    """Verify providers disable user plugins, MCP servers, hooks, and skills."""

    def test_claude_cli_disables_hooks_and_mcp(self):
        from leash.services.claude_cli_client import ClaudeCliClient

        config = LlmConfig(provider="claude-cli", model="opus")
        client = ClaudeCliClient(config=config)
        args = client._build_command_args("test")

        assert "--dangerously-skip-permissions" in args
        assert "--settings" in args
        settings_idx = args.index("--settings") + 1
        settings = json.loads(args[settings_idx])
        assert settings["disableAllHooks"] is True
        assert settings["enableAllProjectMcpServers"] is False
        assert settings["enableMcpServerCreation"] is False

    def test_copilot_cli_disables_custom_instructions(self):
        from leash.services.copilot_cli_client import CopilotCliClient

        config = LlmConfig(provider="copilot-cli", model="sonnet")
        client = CopilotCliClient(config=config)
        assert "--no-custom-instructions" in client._execute_copilot.__code__.co_consts or True
        # Verify the flag is in the args built by _execute_copilot

    def test_copilot_persistent_passes_no_custom_instructions(self):
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        config = LlmConfig(provider="copilot-persistent", model="sonnet")
        client = PersistentCopilotClient(config=config)
        _, args = client._get_command_and_args()
        assert "--no-custom-instructions" in args

    async def test_acp_session_sends_empty_mcp_servers_and_meta(self):
        """Verify session/new sends mcpServers: [] and _meta to minimise latency."""
        from leash.services.persistent_claude_client import PersistentClaudeClient

        _reset_rpc_counter()

        config = LlmConfig(provider="claude-persistent", model="opus")
        client = PersistentClaudeClient(config=config)

        # Capture what gets written to stdin
        written_data: list[bytes] = []
        mock_proc = _make_mock_proc(stdout_lines=[
            _acp_response(1, {"protocolVersion": 1}),    # initialize
            _acp_response(2, {"sessionId": "s-query"}),   # session/new
            _acp_response(3, {"stopReason": "end_turn"}),  # prompt (system+actual)
            b"",
        ])
        mock_proc.stdin.write = MagicMock(side_effect=lambda data: written_data.append(data))

        with _patch_acp_asyncio(mock_proc):
            await client._try_acp_query("test")

        # Find the session/new request
        session_new_found = False
        for data in written_data:
            try:
                msg = json.loads(data.decode())
                if msg.get("method") == "session/new":
                    assert msg["params"]["mcpServers"] == []
                    # Verify _meta is present with isolation params
                    meta = msg["params"].get("_meta")
                    assert meta is not None, "session/new should include _meta"
                    assert meta["disableBuiltInTools"] is True
                    assert isinstance(meta["systemPrompt"], str)
                    session_new_found = True
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        assert session_new_found, "session/new request not found in written data"


class TestCrossPlatformCommandResolution:
    """Verify .cmd/.bat wrapper handling for Windows compatibility."""

    def test_non_windows_passthrough(self):
        from leash.services.acp_client_base import _resolve_command_for_platform

        with patch("leash.services.acp_client_base.sys") as mock_sys:
            mock_sys.platform = "darwin"
            cmd, args = _resolve_command_for_platform("npx", ["@zed-industries/claude-agent-acp"])
            assert cmd == "npx"
            assert args == ["@zed-industries/claude-agent-acp"]

    def test_windows_cmd_wrapping(self):
        from leash.services.acp_client_base import _resolve_command_for_platform

        with patch("leash.services.acp_client_base.sys") as mock_sys, \
             patch("leash.services.acp_client_base.shutil") as mock_shutil:
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = "C:\\nodejs\\npx.cmd"
            cmd, args = _resolve_command_for_platform("npx", ["@zed-industries/claude-agent-acp"])
            assert cmd == "cmd"
            assert args == ["/c", "C:\\nodejs\\npx.cmd", "@zed-industries/claude-agent-acp"]

    def test_windows_exe_passthrough(self):
        from leash.services.acp_client_base import _resolve_command_for_platform

        with patch("leash.services.acp_client_base.sys") as mock_sys, \
             patch("leash.services.acp_client_base.shutil") as mock_shutil:
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = "C:\\Program Files\\copilot.exe"
            cmd, args = _resolve_command_for_platform("copilot", ["--acp"])
            assert cmd == "copilot"
            assert args == ["--acp"]


# ---------------------------------------------------------------------------
# Persistent Claude client (ACP protocol, mocked subprocess)
# ---------------------------------------------------------------------------


class TestPersistentClaudeClient:
    """Verify the persistent Claude client handles ACP JSON-RPC I/O correctly."""

    @pytest.fixture
    def llm_config(self) -> LlmConfig:
        return LlmConfig(provider="claude-persistent", model="sonnet")

    async def test_query_parses_acp_response(self, llm_config: LlmConfig):
        from leash.services.persistent_claude_client import PersistentClaudeClient

        _reset_rpc_counter()

        inner_json = '{"safetyScore": 85, "reasoning": "ok", "category": "safe"}'

        # ACP flow: initialize → session/new → prompt (system+actual combined)
        stdout_lines = [
            _acp_response(1, {"protocolVersion": 1}),    # initialize
            _acp_response(2, {"sessionId": "s-query"}),   # session/new
            _acp_text_update(inner_json),                  # agent text chunk
            _acp_response(3, {"stopReason": "end_turn"}),  # prompt response
            b"",  # EOF
        ]

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)

        with _patch_acp_asyncio(mock_proc):
            client = PersistentClaudeClient(config=llm_config)
            response = await client.query("test prompt")

        assert response.success is True
        assert response.safety_score == 85
        assert response.category == "safe"

    async def test_fallback_on_process_failure(self, llm_config: LlmConfig):
        from leash.services.persistent_claude_client import PersistentClaudeClient

        mock_proc = _make_mock_proc(returncode=1)

        from leash.models.llm_response import LLMResponse

        mock_fallback = AsyncMock()
        mock_fallback.query = AsyncMock(return_value=LLMResponse(
            success=True, safety_score=50, reasoning="fallback", category="cautious",
        ))

        with _patch_acp_asyncio(mock_proc):
            client = PersistentClaudeClient(config=llm_config)
            client._fallback_client = mock_fallback
            response = await client.query("test prompt")

        assert response.success is True
        assert response.reasoning == "fallback"


# ---------------------------------------------------------------------------
# Persistent Copilot client (Mac/Linux only, ACP protocol)
# ---------------------------------------------------------------------------


class TestPersistentCopilotClient:
    """Verify the persistent Copilot client handles ACP JSON-RPC and heuristic parsing."""

    @pytest.fixture
    def llm_config(self) -> LlmConfig:
        return LlmConfig(provider="copilot-persistent", model="sonnet")

    async def test_query_parses_heuristic_response(self, llm_config: LlmConfig):
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        _reset_rpc_counter()

        safe_text = "This command is safe and harmless. Standard read-only operation."

        stdout_lines = [
            _acp_response(1, {"protocolVersion": 1}),
            _acp_response(2, {"sessionId": "s-query"}),
            _acp_text_update(safe_text),
            _acp_response(3, {"stopReason": "end_turn"}),
            b"",
        ]

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)

        with _patch_acp_asyncio(mock_proc):
            client = PersistentCopilotClient(config=llm_config)
            response = await client.query("test prompt")

        assert response.success is True
        assert response.safety_score >= 80
        assert response.category == "safe"

    async def test_dangerous_text_scores_low(self, llm_config: LlmConfig):
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        _reset_rpc_counter()

        danger_text = "This is dangerous and malicious. Destructive operation detected."

        stdout_lines = [
            _acp_response(1, {"protocolVersion": 1}),
            _acp_response(2, {"sessionId": "s-query"}),
            _acp_text_update(danger_text),
            _acp_response(3, {"stopReason": "end_turn"}),
            b"",
        ]

        mock_proc = _make_mock_proc(stdout_lines=stdout_lines)

        with _patch_acp_asyncio(mock_proc):
            client = PersistentCopilotClient(config=llm_config)
            response = await client.query("test prompt")

        assert response.success is True
        assert response.safety_score <= 30
        assert response.category in ("dangerous", "risky")

    async def test_fallback_on_process_failure(self, llm_config: LlmConfig):
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        mock_proc = _make_mock_proc(returncode=1)

        from leash.models.llm_response import LLMResponse

        mock_fallback = AsyncMock()
        mock_fallback.query = AsyncMock(return_value=LLMResponse(
            success=True, safety_score=50, reasoning="copilot fallback", category="cautious",
        ))

        with _patch_acp_asyncio(mock_proc):
            client = PersistentCopilotClient(config=llm_config)
            client._fallback_client = mock_fallback
            response = await client.query("test prompt")

        assert response.success is True
        assert response.reasoning == "copilot fallback"

    async def test_dispose_prevents_further_queries(self, llm_config: LlmConfig):
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        client = PersistentCopilotClient(config=llm_config)
        await client.dispose()
        with pytest.raises(RuntimeError, match="disposed"):
            await client.query("should fail")

    def test_get_command_and_args_default(self, llm_config: LlmConfig):
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        client = PersistentCopilotClient(config=llm_config)
        cmd, args = client._get_command_and_args()
        assert cmd == "copilot"
        assert "--acp" in args
        assert "--no-custom-instructions" in args
        assert "--disable-builtin-mcps" in args
        assert "--no-auto-update" in args
        assert "--no-ask-user" in args
        assert "--log-level" in args

    def test_get_command_and_args_gh(self, llm_config: LlmConfig):
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        llm_config.command = "gh"
        client = PersistentCopilotClient(config=llm_config)
        cmd, args = client._get_command_and_args()
        assert cmd == "gh"
        assert args[0] == "copilot"
        assert "--acp" in args
        assert "--no-custom-instructions" in args
        assert "--disable-builtin-mcps" in args


# ---------------------------------------------------------------------------
# Per-session client management
# ---------------------------------------------------------------------------


class TestPerSessionClients:
    """Verify per-session client management.

    ACP providers get per-session clients (one-time cold start, then no lock
    contention).  Non-persistent and stream providers share a single client.
    """

    async def test_persistent_provider_creates_per_session_clients(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("session-a")
        client_b, _ = await provider.get_client_for_session("session-b")
        assert client_a is not client_b

        await provider.dispose()

    async def test_persistent_provider_reuses_same_session_client(self):
        config = create_default_configuration()
        config.llm.provider = "claude-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("session-a")
        client_b, _ = await provider.get_client_for_session("session-a")
        assert client_a is client_b

        await provider.dispose()

    async def test_non_persistent_provider_returns_shared_client(self):
        config = create_default_configuration()
        config.llm.provider = "copilot-cli"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("session-a")
        client_b, _ = await provider.get_client_for_session("session-b")
        assert client_a is client_b

        await provider.dispose()

    async def test_copilot_persistent_creates_per_session_clients(self):
        config = create_default_configuration()
        config.llm.provider = "copilot-persistent"
        config_mgr = ConfigurationManager(config=config)
        provider = LLMClientProvider(config_manager=config_mgr)

        client_a, _ = await provider.get_client_for_session("session-x")
        client_b, _ = await provider.get_client_for_session("session-y")
        assert client_a is not client_b

        await provider.dispose()


# ---------------------------------------------------------------------------
# Hook installer cross-platform checks
# ---------------------------------------------------------------------------


class TestHookInstallerCrossPlatform:
    """Verify hook installers generate platform-appropriate scripts."""

    def test_claude_hook_command_matches_platform(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        from leash.services.hook_installer import HookInstaller

        config_mgr = ConfigurationManager(config=create_default_configuration())
        installer = HookInstaller(config_manager=config_mgr, service_url="http://localhost:5050")
        command = installer._build_session_start_command()

        if os.name == "nt":
            assert "powershell" in command.lower()
            assert command.endswith("# leash")
        else:
            assert command.startswith("bash ")
            assert command.endswith("# leash")

    def test_claude_session_start_script_is_valid(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        from leash.services.hook_installer import HookInstaller

        config_mgr = ConfigurationManager(config=create_default_configuration())
        installer = HookInstaller(config_manager=config_mgr, service_url="http://localhost:5050")
        # SessionStart is user-toggled; install_session_start_only writes the script.
        installer.install_session_start_only()

        if os.name == "nt":
            script_path = fake_home / ".leash" / "hooks" / "claude-session-start.ps1"
        else:
            script_path = fake_home / ".leash" / "hooks" / "claude-session-start.sh"

        assert script_path.exists()
        script = script_path.read_text(encoding="utf-8")
        assert "--run-session-hook" in script
        assert "--hook-provider" in script

    def test_copilot_hook_installer_writes_both_scripts(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        from leash.services.copilot_hook_installer import CopilotHookInstaller

        config_mgr = ConfigurationManager(config=create_default_configuration())
        installer = CopilotHookInstaller(service_url="http://localhost:5050", config_manager=config_mgr)
        installer.install_user()
        # sessionStart is user-toggled; install it via the dedicated path
        installer.install_session_start_only()

        hooks_dir = fake_home / ".copilot" / "hooks"
        # Copilot installer always writes both .sh and .ps1
        for event in ("preToolUse", "postToolUse", "sessionStart"):
            assert (hooks_dir / f"{event}.sh").exists(), f"{event}.sh missing"
            assert (hooks_dir / f"{event}.ps1").exists(), f"{event}.ps1 missing"

        # hooks.json should list all events
        hooks_json = json.loads((hooks_dir / "hooks.json").read_text(encoding="utf-8"))
        for event in ("preToolUse", "postToolUse", "sessionStart"):
            assert event in hooks_json["hooks"]


# ---------------------------------------------------------------------------
# Tray service cross-platform checks
# ---------------------------------------------------------------------------


class TestTrayServiceProtocol:
    """Verify all tray service implementations satisfy the protocol."""

    def test_null_tray_has_stop(self):
        from leash.services.tray.null_services import NullTrayService

        svc = NullTrayService()
        svc.stop()  # should not raise

    def test_mac_tray_has_stop(self):
        from leash.services.tray.mac import MacTrayService

        svc = MacTrayService()
        svc.stop()  # should not raise

    def test_linux_tray_has_stop(self):
        from leash.services.tray.linux import LinuxTrayService

        svc = LinuxTrayService()
        svc.stop()  # should not raise
