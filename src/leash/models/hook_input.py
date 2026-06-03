"""Hook input model - represents data sent by Claude Code hooks."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from pydantic.alias_generators import to_camel


class HookInput(BaseModel):
    """Data received from a Claude Code or Copilot hook via curl."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    hook_event_name: str = ""
    session_id: str = ""
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    cwd: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str = "claude"

    def resolve_symlinks(self) -> None:
        """Resolve symlinks in cwd and file paths within tool_input.

        When the working directory is a symlink (e.g. ``C:\\r\\project`` →
        ``C:\\Users\\...\\repos\\project``), the LLM sees mismatched paths and
        may flag safe operations as risky.  This method resolves all paths to
        their real locations so the LLM compares like-for-like.

        Relative paths in tool_input are resolved against the hook's ``cwd``
        (not the Leash server's working directory).
        """
        resolved_cwd = ""
        if self.cwd:
            resolved_cwd = os.path.realpath(self.cwd)
            self.cwd = resolved_cwd

        if self.tool_input:
            _PATH_KEYS = (
                "file_path", "path", "directory", "dir", "target", "destination",
                "source", "src", "from",
            )
            for key in _PATH_KEYS:
                val = self.tool_input.get(key)
                if isinstance(val, str) and val:
                    if os.path.isabs(val):
                        self.tool_input[key] = os.path.realpath(val)
                    elif resolved_cwd:
                        # Resolve relative paths against the hook's cwd
                        self.tool_input[key] = os.path.realpath(
                            os.path.join(resolved_cwd, val)
                        )
