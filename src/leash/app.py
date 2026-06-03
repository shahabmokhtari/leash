"""FastAPI application factory with lifespan, middleware, and auto-router discovery."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import pkgutil
import signal
import sys
import sysconfig
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from leash import __version__
from leash.config import ConfigurationManager
from leash.middleware.security_headers import SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


def _find_data_dir(name: str) -> Path:
    """Locate a shared-data directory (e.g. 'static', 'prompts').

    Search order:
    1. Repo root (development with ``uv run``)
    2. Venv/install prefix via sysconfig (``uvx``, ``pip install``)
    3. ~/.local/share/leash/ (legacy fallback)
    """
    pkg_dir = Path(__file__).resolve().parent
    candidates = [
        pkg_dir.parent.parent / name,
        Path(sysconfig.get_path("data")) / "share" / "leash" / name,
        Path.home() / ".local" / "share" / "leash" / name,
    ]
    for d in candidates:
        if d.is_dir():
            return d
    return candidates[0]


def _find_static_dir() -> Path:
    """Locate the static files directory."""
    return _find_data_dir("static")


def _find_prompts_dir() -> Path:
    """Locate the prompts directory."""
    return _find_data_dir("prompts")


def _find_scripts_dir() -> Path:
    """Locate the bundled scripts directory."""
    return _find_data_dir("scripts")


def _discover_routers(app: FastAPI) -> None:
    """Auto-discover and include all routers from leash.routes package."""
    import leash.routes as routes_pkg

    for module_info in pkgutil.iter_modules(routes_pkg.__path__):
        if module_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"leash.routes.{module_info.name}")
            if hasattr(mod, "router"):
                app.include_router(mod.router)
                logger.debug("Registered router: leash.routes.%s", module_info.name)
        except Exception:
            logger.exception("Failed to load router module: leash.routes.%s", module_info.name)


def _install_hooks_on_startup(
    hooks_target: str | None,
    config: Any,
    hook_installer: Any,
    copilot_hook_installer: Any,
    console_status_svc: Any,
) -> None:
    """Install Claude/Copilot hooks based on the --hooks CLI target."""
    install_claude = hooks_target in (None, "claude", "both")
    install_copilot = hooks_target in ("copilot", "both")

    # Install Claude hooks (user-level — always effective)
    if install_claude:
        if config.hooks_user_uninstalled:
            logger.warning(
                "Claude hooks not installed: previously uninstalled by user. "
                "Use the dashboard to re-install.",
            )
            console_status_svc.log("WARNING: Claude hooks not installed (user previously uninstalled)")
        else:
            try:
                hook_installer.install()
                console_status_svc.set_hooks_installed(True)
                logger.info("Claude hooks installed on startup")
            except Exception:
                logger.warning("Failed to install Claude hooks on startup", exc_info=True)

    # Copilot hooks: CLI officially reads from .github/hooks/ per repo.
    # User-level installation (~/.copilot/hooks/) is a convenience fallback
    # that may work with some CLI versions.  Install with a warning.
    if install_copilot:
        if config.copilot_hooks_user_uninstalled:
            logger.warning(
                "Copilot hooks not installed: previously uninstalled by user. "
                "Re-install via the dashboard (user-level) or 'install_repo(path)' for per-repo.",
            )
            console_status_svc.log("WARNING: Copilot hooks not installed (user previously uninstalled)")
        else:
            try:
                copilot_hook_installer.install_user()
                console_status_svc.set_hooks_installed(True)
                logger.info(
                    "Copilot hooks installed at user level. Note: Copilot CLI "
                    "reads hooks from .github/hooks/ per repo — user-level hooks "
                    "may not be read by all CLI versions.",
                )
                console_status_svc.log(
                    "INFO: Copilot hooks installed (user-level). "
                    "Per-repo (.github/hooks/) recommended for guaranteed effectiveness."
                )
            except Exception:
                logger.warning("Failed to install Copilot hooks on startup", exc_info=True)

    if not install_claude and not install_copilot:
        logger.warning("Unrecognized --hooks value %r; no hooks installed", hooks_target)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize services on startup, clean up on shutdown."""
    config_path = getattr(app.state, "config_path", None)
    config_mgr = ConfigurationManager(config_path=config_path)
    config = await config_mgr.load()
    app.state.config_manager = config_mgr
    app.state.configuration = config

    prompts_dir = str(_find_prompts_dir())
    app.state.prompts_dir = prompts_dir

    # --- Initialize all services ---
    from leash.services.adaptive_threshold_service import AdaptiveThresholdService
    from leash.services.audit_report_generator import AuditReportGenerator
    from leash.services.console_status_service import ConsoleStatusService
    from leash.services.copilot_hook_installer import CopilotHookInstaller
    from leash.services.enforcement_service import EnforcementService
    from leash.services.harness.claude import ClaudeHarnessClient
    from leash.services.harness.copilot import CopilotHarnessClient
    from leash.services.harness.registry import HarnessClientRegistry
    from leash.services.hook_handler_factory import HookHandlerFactory
    from leash.services.hook_installer import HookInstaller
    from leash.services.insights_engine import InsightsEngine
    from leash.services.llm_client_provider import LLMClientProvider
    from leash.services.pre_validation_service import PreValidationService
    from leash.services.profile_service import ProfileService
    from leash.services.prompt_builder import PromptBuilder
    from leash.services.prompt_template_service import PromptTemplateService
    from leash.services.session_manager import SessionManager
    from leash.services.terminal_output_service import TerminalOutputService
    from leash.services.transcript_watcher import TranscriptWatcher
    from leash.services.tray.null_services import NullNotificationService, NullTrayService
    from leash.services.tray.pending_decision import PendingDecisionService
    from leash.services.trigger_service import TriggerService
    from leash.session_start_hook import build_service_url, persist_launch_metadata

    # Core services — use actual constructor signatures
    storage_dir = config.session.storage_dir
    bind_host = getattr(app.state, "cli_host", None) or config.server.host
    bind_port = getattr(app.state, "cli_port", None) or config.server.port
    session_mgr = SessionManager(storage_dir=storage_dir, max_history_size=config.session.max_history_per_session)
    enforcement_svc = EnforcementService(config_manager=config_mgr)
    service_url = build_service_url(bind_host, bind_port)
    hook_installer = HookInstaller(config_manager=config_mgr, service_url=service_url)
    copilot_hook_installer = CopilotHookInstaller(service_url=service_url, config_manager=config_mgr)
    prompt_template_svc = PromptTemplateService(prompts_dir=prompts_dir)
    user_scripts_dir = str(Path.home() / ".leash" / "scripts")
    bundled_scripts_dir = str(_find_scripts_dir())
    pre_validation_svc = PreValidationService(
        scripts_dir=user_scripts_dir,
        bundled_scripts_dir=bundled_scripts_dir,
    )
    profile_svc = ProfileService(config_manager=config_mgr)
    await profile_svc.initialize()
    adaptive_threshold_svc = AdaptiveThresholdService()
    insights_engine = InsightsEngine(adaptive_service=adaptive_threshold_svc, session_manager=session_mgr)
    audit_report_gen = AuditReportGenerator(
        session_manager=session_mgr,
        adaptive_service=adaptive_threshold_svc,
        profile_service=profile_svc,
    )
    trigger_svc = TriggerService(config_manager=config_mgr)
    console_status_svc = ConsoleStatusService(enforcement_service=enforcement_svc, hooks_installed=False)
    terminal_output_svc = TerminalOutputService()
    llm_client_provider = LLMClientProvider(config_manager=config_mgr, terminal_output=terminal_output_svc)

    # Harness clients
    claude_client = ClaudeHarnessClient()
    copilot_client = CopilotHarnessClient()
    harness_registry = HarnessClientRegistry([claude_client, copilot_client])

    # Transcript watcher
    transcript_watcher = TranscriptWatcher()
    transcript_watcher.set_harness_clients([claude_client, copilot_client])
    transcript_watcher.set_resolve_symlinks(config.resolve_symlinks)

    # Tray services — use platform-specific services when available
    pending_decision_svc = PendingDecisionService()
    tray_svc = NullTrayService()
    notification_svc = NullNotificationService()
    if config.tray.enabled:
        if sys.platform == "win32":
            try:
                from leash.services.tray.windows import (
                    WindowsNotificationService,
                    WindowsTrayService,
                )

                tray_svc = WindowsTrayService(dashboard_url=service_url)
                notification_svc = WindowsNotificationService(
                    tray_service=tray_svc,
                    use_large_popup=config.tray.use_large_popup,
                )
                logger.info("Windows tray service enabled")
            except Exception:
                logger.warning(
                    "Failed to initialize Windows tray services (tray.enabled=true), "
                    "falling back to null services. Install pystray and Pillow for tray support.",
                    exc_info=True,
                )
        elif sys.platform == "darwin":
            try:
                from leash.services.tray.mac import MacNotificationService, MacTrayService

                tray_svc = MacTrayService()
                notification_svc = MacNotificationService()
                logger.info("macOS notification service enabled")
            except Exception:
                logger.warning("Failed to initialize macOS tray services", exc_info=True)
        else:
            # Linux / other Unix
            try:
                from leash.services.tray.linux import LinuxNotificationService, LinuxTrayService

                tray_svc = LinuxTrayService()
                notification_svc = LinuxNotificationService()
                logger.info("Linux notification service enabled")
            except Exception:
                logger.warning("Failed to initialize Linux tray services", exc_info=True)

    # Handler factory
    handler_factory = HookHandlerFactory(
        llm_client_provider=llm_client_provider,
        prompt_template_service=prompt_template_svc,
        session_manager=session_mgr,
    )

    # Store all services on app.state for route access
    app.state.session_manager = session_mgr
    app.state.enforcement_service = enforcement_svc
    app.state.hook_installer = hook_installer
    app.state.copilot_hook_installer = copilot_hook_installer
    app.state.prompt_template_service = prompt_template_svc
    app.state.pre_validation_service = pre_validation_svc
    app.state.prompt_builder = PromptBuilder
    app.state.profile_service = profile_svc
    app.state.adaptive_threshold_service = adaptive_threshold_svc
    app.state.insights_engine = insights_engine
    app.state.audit_report_generator = audit_report_gen
    app.state.trigger_service = trigger_svc
    app.state.console_status_service = console_status_svc
    app.state.terminal_output_service = terminal_output_svc
    app.state.llm_client_provider = llm_client_provider
    app.state.harness_client_registry = harness_registry
    app.state.claude_harness_client = claude_client
    app.state.copilot_harness_client = copilot_client
    app.state.transcript_watcher = transcript_watcher
    app.state.tray_service = tray_svc
    app.state.notification_service = notification_svc
    app.state.pending_decision_service = pending_decision_svc
    app.state.handler_factory = handler_factory

    hooks_target = getattr(app.state, "cli_hooks_target", None)
    no_hooks = getattr(app.state, "cli_no_hooks", False)
    skip_hooks = no_hooks or hooks_target == "none"

    try:
        persist_launch_metadata(bind_host, bind_port, config_path=config_path)
    except Exception:
        logger.warning("Failed to persist launch metadata for SessionStart hooks", exc_info=True)

    # Start transcript watcher and preload project list in background
    transcript_watcher.start()
    asyncio.create_task(transcript_watcher.preload_projects())

    # Warm up npx package cache for persistent Claude ACP (background, non-blocking)
    async def _warmup_acp_package() -> None:
        proc = None
        try:
            provider = config.llm.provider or "anthropic-api"
            if provider != "claude-persistent":
                return
            package = "@zed-industries/claude-agent-acp"
            logger.info("Warming up npx package: %s", package)
            cmd = "npx"
            args = ["--yes", package, "--version"]
            if sys.platform == "win32":
                import shutil
                resolved = shutil.which(cmd)
                if resolved and resolved.lower().endswith((".cmd", ".bat")):
                    cmd = "cmd"
                    args = ["/c", resolved, "--yes", package, "--version"]
            proc = await asyncio.create_subprocess_exec(
                cmd, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                logger.info("npx warmup complete: %s", stdout.decode(errors="replace").strip())
            else:
                logger.warning("npx warmup exited %d: %s", proc.returncode, stderr.decode(errors="replace").strip()[:200])
        except asyncio.TimeoutError:
            logger.warning("npx warmup timed out after 120s")
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
        except Exception:
            logger.debug("npx warmup failed", exc_info=True)

    asyncio.create_task(_warmup_acp_package())

    # Start tray service
    try:
        await tray_svc.start()
    except Exception:
        logger.warning("Failed to start tray service, continuing without tray", exc_info=True)
        tray_svc = NullTrayService()
        notification_svc = NullNotificationService()
        app.state.tray_service = tray_svc
        app.state.notification_service = notification_svc

    # Apply CLI args
    if getattr(app.state, "cli_enforce", False):
        await enforcement_svc.set_mode("enforce")

    if skip_hooks:
        logger.info("Hook installation skipped (--no-hooks or --hooks none)")
    else:
        _install_hooks_on_startup(
            hooks_target, config, hook_installer, copilot_hook_installer,
            console_status_svc,
        )

    # Install a force-exit handler: second Ctrl+C kills immediately.
    # On Windows, uvicorn's signal handling can fail to trigger lifespan
    # shutdown, leaving the process stuck.  This ensures a way out.
    _shutting_down = False

    def _force_exit_handler(sig: int, frame: Any) -> None:
        nonlocal _shutting_down
        if _shutting_down:
            logger.warning("Force exit (second signal received)")
            os._exit(1)
        _shutting_down = True
        logger.info("Shutdown signal received — press Ctrl+C again to force exit")
        raise KeyboardInterrupt

    if sys.platform == "win32":
        signal.signal(signal.SIGINT, _force_exit_handler)
        signal.signal(signal.SIGBREAK, _force_exit_handler)
    else:
        signal.signal(signal.SIGINT, _force_exit_handler)
        signal.signal(signal.SIGTERM, _force_exit_handler)

    # Route log messages from leash and uvicorn to the console log section
    class _ConsoleLogHandler(logging.Handler):
        _NOISY_PATTERNS = (
            "GET /api/dashboard/",
            "GET /api/terminal/",
            "GET /api/hooks/status",
            "GET /health",
        )

        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = self.format(record)
                # Skip noisy polling endpoint messages in console (still logged to file)
                for pattern in self._NOISY_PATTERNS:
                    if pattern in msg:
                        return
                console_status_svc.log(msg)
            except Exception:
                self.handleError(record)

    _console_handler = _ConsoleLogHandler()
    _console_handler.setLevel(logging.INFO)
    _console_handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    logging.getLogger("leash").addHandler(_console_handler)

    # Redirect uvicorn loggers through the console panel instead of stderr
    for _uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _uv_logger = logging.getLogger(_uv_name)
        # Remove default stream handlers so they don't write to stderr
        for _h in list(_uv_logger.handlers):
            if isinstance(_h, logging.StreamHandler):
                _uv_logger.removeHandler(_h)
        _uv_logger.addHandler(_console_handler)

    logger.info("Leash started - port %d, enforcement: %s", bind_port, enforcement_svc.mode)

    # Auto-launch browser unless --no-browser was passed
    hooks_installed = False
    if not getattr(app.state, "cli_no_hooks", False):
        try:
            hooks_installed = hook_installer.is_installed() or copilot_hook_installer.is_user_installed()
        except Exception:
            logger.debug("Failed to check startup hook installation status", exc_info=True)
    if not getattr(app.state, "cli_no_browser", False):
        try:
            import webbrowser

            webbrowser.open(service_url)
        except Exception:
            logger.debug("Failed to open browser", exc_info=True)
    else:
        # Show tray notification when browser is not opened
        hook_status_msg = "Hooks: installed" if hooks_installed else "Hooks: NOT installed"
        try:
            from leash.models.tray_models import NotificationInfo

            await notification_svc.show_alert(
                NotificationInfo(
                    title="Leash is running",
                    body=f"Dashboard: {service_url}\n{hook_status_msg}",
                )
            )
        except Exception:
            logger.debug("Failed to show startup notification", exc_info=True)

    yield

    _shutting_down = True

    # Cleanup — force exit after 5 seconds if graceful shutdown stalls
    logger.info("Leash shutting down")

    async def _graceful_cleanup() -> None:
        # Always remove regular runtime hooks on shutdown.  The SessionStart
        # bootstrap hook is preserved because it is user-toggled separately
        # via the dashboard (install-on-click / remove-on-toggle-off).
        try:
            hook_installer.uninstall(preserve_session_start=True)
            logger.debug("Claude runtime hooks removed; SessionStart preserved")
        except Exception:
            logger.debug("Error uninstalling Claude hooks during shutdown", exc_info=True)
        # Always remove Copilot user-level runtime hooks on shutdown so they
        # do not linger and error out when Leash is not running.  sessionStart
        # entries are preserved by the installer (user-toggled).
        try:
            copilot_hook_installer.uninstall_user()
            logger.debug("Copilot user-level runtime hooks removed; sessionStart preserved")
        except Exception:
            logger.debug("Error uninstalling Copilot user-level hooks during shutdown", exc_info=True)

        # Stop tray (unblocks its message loop thread)
        try:
            tray_svc.stop()
        except Exception:
            logger.debug("Error stopping tray service during shutdown", exc_info=True)

        # Dispose LLM clients (kills persistent subprocesses)
        try:
            await llm_client_provider.dispose()
        except Exception:
            logger.debug("Error disposing LLM client provider", exc_info=True)

        # Stop transcript watcher
        try:
            transcript_watcher.stop()
        except Exception:
            logger.debug("Error stopping transcript watcher", exc_info=True)

        # Dispose console status service
        logging.getLogger("leash").removeHandler(_console_handler)
        for _uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(_uv_name).removeHandler(_console_handler)
        console_status_svc.dispose()

    try:
        await asyncio.wait_for(_graceful_cleanup(), timeout=5.0)
        logger.info("Graceful shutdown complete")
    except asyncio.TimeoutError:
        logger.error("Graceful shutdown timed out after 5s")

    # Schedule a hard exit to ensure the process terminates even if
    # background threads (tray, SSE connections) keep it alive.
    # Use a daemon thread so it doesn't block Python's natural shutdown.
    def _deferred_exit() -> None:
        import time as _time
        _time.sleep(2.0)
        os._exit(0)

    _exit_thread = threading.Thread(target=_deferred_exit, daemon=True)
    _exit_thread.start()


def create_app(config_path: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Leash",
        description="Observe and enforce Claude Code permission requests",
        version=__version__,
        lifespan=lifespan,
    )

    if config_path:
        app.state.config_path = config_path

    # Security headers (no-cache, CSP, X-Frame-Options, etc.) on all responses
    app.add_middleware(SecurityHeadersMiddleware)

    # Auto-discover and register route modules
    _discover_routers(app)

    # Mount static files (HTML dashboard)
    static_dir = _find_static_dir()
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
