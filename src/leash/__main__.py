"""CLI entry point for Leash."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from leash.installer import run_console_installer, should_run_installer

_LOG_DIR = Path.home() / ".leash" / "logs"
_MAX_BYTES = 1 * 1024 * 1024  # 1 MB per file
_BACKUP_COUNT = 9  # 9 backups + 1 active = 10 MB max


def _setup_file_logging() -> None:
    """Configure rotating file logging for all leash and uvicorn loggers."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = _LOG_DIR / f"leash_{timestamp}.log"

    handler = RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # Attach to root logger so all loggers (leash.*, uvicorn.*) are captured
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="leash",
        description="Observe and enforce Claude Code permission requests",
    )
    parser.add_argument("--port", type=int, default=5050, help="Port to listen on (default: 5050)")
    parser.add_argument("--host", type=str, default="localhost", help="Host to bind (default: localhost)")
    parser.add_argument("--enforce", action="store_true", help="Start in enforcement mode")
    parser.add_argument("--no-hooks", action="store_true", help="Skip hook installation on startup")
    parser.add_argument(
        "--hooks",
        type=str,
        default=None,
        choices=["claude", "copilot", "both", "none"],
        help="Which hooks to install on startup (default: claude)",
    )
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser on startup")
    parser.add_argument("--config", type=str, default=None, help="Path to config.json")
    parser.add_argument("--run-session-hook", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--hook-provider", type=str, default="", help=argparse.SUPPRESS)
    parser.add_argument("--hook-event", type=str, default="", help=argparse.SUPPRESS)
    parser.add_argument("--service-url", type=str, default="", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.no_hooks and args.hooks and args.hooks != "none":
        parser.error("--no-hooks and --hooks are mutually exclusive (use --hooks none instead)")

    if args.run_session_hook:
        from leash.session_start_hook import run_session_hook_proxy

        raise SystemExit(
            run_session_hook_proxy(
                provider=args.hook_provider,
                event=args.hook_event,
                service_url=args.service_url,
            )
        )

    _setup_file_logging()

    installer_ran = False
    if should_run_installer(args.config):
        run_console_installer(
            config_path=args.config,
            no_hooks=args.no_hooks,
            profile_default="moderate",
            enforcement_default="enforce" if args.enforce else "observe",
        )
        installer_ran = True

    # Store CLI args for the app lifespan to pick up
    import leash.app as app_module

    app = app_module.create_app(config_path=args.config)
    app.state.cli_enforce = args.enforce and not installer_ran
    app.state.cli_host = args.host
    app.state.cli_no_hooks = args.no_hooks
    app.state.cli_hooks_target = args.hooks
    app.state.cli_no_browser = args.no_browser
    app.state.cli_port = args.port

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main(sys.argv[1:])
