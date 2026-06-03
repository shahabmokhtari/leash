"""Pre-validation service — runs lightweight scripts before LLM analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCRIPT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class PreValidationResult:
    """Result from a pre-validation script."""

    decision: str  # "approve", "deny", or "passthrough"
    reason: str = ""


_PASSTHROUGH = PreValidationResult(decision="passthrough")


class PreValidationService:
    """Manages and executes pre-validation scripts stored in ~/.leash/scripts/."""

    def __init__(self, scripts_dir: str, bundled_scripts_dir: str | None = None) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._bundled_scripts_dir = Path(bundled_scripts_dir) if bundled_scripts_dir else None
        self._ensure_scripts_dir()

    def _ensure_scripts_dir(self) -> None:
        """Create scripts dir and copy bundled defaults if they don't exist."""
        self._scripts_dir.mkdir(parents=True, exist_ok=True)

        if self._bundled_scripts_dir and self._bundled_scripts_dir.is_dir():
            for src_file in self._bundled_scripts_dir.iterdir():
                if src_file.is_file():
                    dest = self._scripts_dir / src_file.name
                    if not dest.exists():
                        try:
                            shutil.copy2(src_file, dest)
                            logger.info("Copied default script: %s", src_file.name)
                        except OSError:
                            logger.warning("Failed to copy default script %s", src_file.name, exc_info=True)

    def _resolve_script(self, script_name: str) -> Path | None:
        """Resolve a script filename to a full path, or None if not found.

        Validates that the resolved path stays within the scripts directory
        to prevent path traversal attacks (e.g. ``../../malicious.py``).
        """
        path = (self._scripts_dir / script_name).resolve()
        if not path.is_relative_to(self._scripts_dir.resolve()):
            logger.warning("Script path escapes scripts directory: %s", script_name)
            return None
        if path.is_file():
            return path
        return None

    async def run(self, script_name: str, context: dict[str, Any]) -> PreValidationResult:
        """Execute a pre-validation script and return its decision.

        Returns passthrough on any error (fail-safe).
        """
        script_path = self._resolve_script(script_name)
        if script_path is None:
            logger.warning("Pre-validation script not found: %s", script_name)
            return _PASSTHROUGH

        try:
            cmd, args = self._build_command(script_path)
            context_json = json.dumps(context)

            proc = await asyncio.create_subprocess_exec(
                cmd,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._sanitized_env(),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=context_json.encode()),
                    timeout=SCRIPT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("Pre-validation script %s timed out after %ds", script_name, SCRIPT_TIMEOUT_SECONDS)
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except Exception:
                    try:
                        proc.kill()
                        await asyncio.wait_for(proc.wait(), timeout=2)
                    except Exception:
                        pass
                return _PASSTHROUGH
            except BaseException:
                # CancelledError is a BaseException — ensure subprocess is
                # killed to prevent leaked processes and blocked pipes.
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except Exception:
                    pass
                raise

            if proc.returncode != 0:
                logger.warning(
                    "Pre-validation script %s exited with code %d: %s",
                    script_name,
                    proc.returncode,
                    stderr.decode(errors="replace").strip()[:200],
                )
                return _PASSTHROUGH

            return self._parse_output(script_name, stdout.decode(errors="replace"))

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Pre-validation script %s failed", script_name, exc_info=True)
            return _PASSTHROUGH

    _SENSITIVE_ENV_PREFIXES = (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "AZURE_API_KEY", "AZURE_OPENAI_", "AZURE_CLIENT_SECRET",
        "AWS_SECRET", "AWS_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "GH_TOKEN", "GITHUB_TOKEN",
        "LEASH_",
        "GOOGLE_API_KEY", "HF_TOKEN", "NPM_TOKEN",
    )

    @classmethod
    def _sanitized_env(cls) -> dict[str, str]:
        """Return a copy of the environment with sensitive variables removed."""
        return {
            k: v for k, v in os.environ.items()
            if not any(k.upper().startswith(prefix) for prefix in cls._SENSITIVE_ENV_PREFIXES)
        }

    def _build_command(self, script_path: Path) -> tuple[str, list[str]]:
        """Build the subprocess command for a script."""
        suffix = script_path.suffix.lower()
        if suffix == ".py":
            python = sys.executable
            return python, [str(script_path)]

        # Shell scripts and other executables: run directly
        if sys.platform == "win32" and suffix in (".cmd", ".bat"):
            return "cmd", ["/c", str(script_path)]

        return str(script_path), []

    def _parse_output(self, script_name: str, stdout: str) -> PreValidationResult:
        """Parse script stdout as JSON decision."""
        stdout = stdout.strip()
        if not stdout:
            logger.warning("Pre-validation script %s produced no output", script_name)
            return _PASSTHROUGH

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            logger.warning("Pre-validation script %s produced invalid JSON: %s", script_name, stdout[:200])
            return _PASSTHROUGH

        decision = data.get("decision", "").lower()
        if decision not in {"approve", "deny", "passthrough"}:
            logger.warning("Pre-validation script %s returned unknown decision: %s", script_name, decision)
            return _PASSTHROUGH

        return PreValidationResult(
            decision=decision,
            reason=data.get("reason", ""),
        )
