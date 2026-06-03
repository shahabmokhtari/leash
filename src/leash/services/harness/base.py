"""Base protocol for harness clients.

A HarnessClient encapsulates client-specific behavior for an AI coding assistant
(Claude Code, GitHub Copilot CLI, etc.): input/output formats, transcript parsing,
settings paths, and event naming conventions.
"""

from __future__ import annotations

from typing import Any, Protocol

from leash.models.hook_input import HookInput
from leash.models.hook_output import HookOutput
from leash.services.transcript_watcher import ClaudeProject, ClaudeSession, TranscriptEntry


class HarnessClient(Protocol):
    """Protocol defining the interface for an AI coding assistant harness client."""

    @property
    def name(self) -> str:
        """Machine-readable name, e.g. 'claude', 'copilot'."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable display name, e.g. 'Claude Code', 'GitHub Copilot CLI'."""
        ...

    # -- Hook Input / Output --------------------------------------------------

    def map_input(self, raw_input: dict[str, Any], hook_event: str) -> HookInput:
        """Map raw JSON from the client's hook into a normalised HookInput."""
        ...

    def format_response(self, hook_event: str, output: HookOutput) -> dict[str, Any]:
        """Format a HookOutput into the JSON structure the client expects."""
        ...

    def format_passthrough(self) -> dict[str, Any]:
        """Return the empty / passthrough response for this client (no opinion)."""
        ...

    def normalize_event_name(self, raw_event: str) -> str:
        """Normalise client-specific event names to internal PascalCase format."""
        ...

    def is_passthrough_tool(self, tool_name: str) -> bool:
        """Return True if the tool should skip analysis (non-actionable)."""
        ...

    # -- Transcripts ----------------------------------------------------------

    def get_transcript_directory(self) -> str | None:
        """Root directory where this client stores transcripts, or None."""
        ...

    def discover_projects(self) -> list[ClaudeProject]:
        """Discover projects/sessions from the transcript directory."""
        ...

    def get_sessions_for_project(self, project_path: str) -> list[ClaudeSession]:
        """List sessions within a project directory."""
        ...

    def find_transcript_file(self, session_id: str) -> str | None:
        """Find the transcript file for a given session ID, or None."""
        ...

    def parse_transcript_line(self, json_line: str) -> TranscriptEntry | None:
        """Parse a single JSONL line into a TranscriptEntry."""
        ...

    # -- Settings & Configuration ---------------------------------------------

    def get_settings_file_path(self) -> str | None:
        """Path to the client's settings/hooks file, or None."""
        ...

    def get_default_prompt_template(self, event_name: str) -> str | None:
        """Return the default prompt template name for a given hook event, or None."""
        ...
