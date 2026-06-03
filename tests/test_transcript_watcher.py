"""Tests for TranscriptWatcher."""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# TranscriptWatcher stub
# ---------------------------------------------------------------------------


class TranscriptWatcher:
    """Monitors transcript directories for live session data."""

    def __init__(self, projects_dir: str | Path | None = None):
        self._projects_dir = Path(projects_dir) if projects_dir else Path.home() / ".claude" / "projects"
        self._running = False

    def get_projects(self) -> list[dict]:
        """Return list of discovered projects."""
        if not self._projects_dir.exists():
            return []
        projects = []
        for d in sorted(self._projects_dir.iterdir()):
            if d.is_dir():
                projects.append({"name": d.name, "path": str(d)})
        return projects

    def get_transcript(self, session_id: str) -> list[dict]:
        """Return transcript entries for a session. Empty if not found."""
        # In the stub, we just look for a .jsonl file matching the session ID
        if not self._projects_dir.exists():
            return []
        for f in self._projects_dir.rglob(f"*{session_id}*.jsonl"):
            entries = []
            for line in f.read_text().splitlines():
                if line.strip():
                    import json

                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return entries
        return []

    def find_transcript_file(self, session_id: str) -> Path | None:
        """Find the transcript file for a session."""
        if not self._projects_dir.exists():
            return None
        for f in self._projects_dir.rglob(f"*{session_id}*.jsonl"):
            return f
        return None

    def start(self) -> None:
        """Start watching (no-op if directory does not exist)."""
        self._running = True

    def dispose(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTranscriptWatcher:
    def test_get_projects_empty_when_dir_not_exists(self, tmp_path: Path):
        watcher = TranscriptWatcher(tmp_path / "nonexistent")
        projects = watcher.get_projects()
        assert projects is not None
        assert projects == []

    def test_get_projects_discovers_directories(self, tmp_path: Path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "project-a").mkdir()
        (projects_dir / "project-b").mkdir()

        watcher = TranscriptWatcher(projects_dir)
        projects = watcher.get_projects()

        assert len(projects) == 2
        names = [p["name"] for p in projects]
        assert "project-a" in names
        assert "project-b" in names

    def test_get_transcript_empty_for_nonexistent(self, tmp_path: Path):
        watcher = TranscriptWatcher(tmp_path / "projects")
        entries = watcher.get_transcript("nonexistent-session-id")
        assert entries is not None
        assert entries == []

    def test_find_transcript_file_returns_none(self, tmp_path: Path):
        watcher = TranscriptWatcher(tmp_path / "projects")
        assert watcher.find_transcript_file("nonexistent") is None

    def test_start_does_not_throw_when_dir_missing(self, tmp_path: Path):
        watcher = TranscriptWatcher(tmp_path / "nonexistent")
        watcher.start()  # Should not raise

    def test_dispose_is_idempotent(self, tmp_path: Path):
        watcher = TranscriptWatcher(tmp_path / "projects")
        watcher.dispose()
        watcher.dispose()  # Should not raise

    def test_get_transcript_reads_jsonl(self, tmp_path: Path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj = projects_dir / "my-project"
        proj.mkdir()

        transcript = proj / "session-abc123.jsonl"
        transcript.write_text(
            '{"type":"message","content":"hello"}\n'
            '{"type":"tool_use","tool":"Bash"}\n'
        )

        watcher = TranscriptWatcher(projects_dir)
        entries = watcher.get_transcript("session-abc123")
        assert len(entries) == 2
        assert entries[0]["type"] == "message"
        assert entries[1]["tool"] == "Bash"
