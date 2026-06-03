"""Tests for session titles, symlink resolution, and metadata extraction from PR #4."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from leash.models.configuration import Configuration
from leash.services.harness.claude import ClaudeHarnessClient
from leash.services.harness.copilot import CopilotHarnessClient
from leash.services.transcript_watcher import ClaudeProject, ClaudeSession, TranscriptWatcher


# ===========================================================================
# ClaudeHarnessClient._extract_text_content
# ===========================================================================


class TestExtractTextContent:
    """Tests for ClaudeHarnessClient._extract_text_content()."""

    def test_string_content_returns_string(self):
        assert ClaudeHarnessClient._extract_text_content("hello world") == "hello world"

    def test_list_with_text_block(self):
        content = [{"type": "text", "text": "some text"}]
        assert ClaudeHarnessClient._extract_text_content(content) == "some text"

    def test_list_with_multiple_blocks_returns_first_text(self):
        content = [
            {"type": "image", "data": "..."},
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
        assert ClaudeHarnessClient._extract_text_content(content) == "first"

    def test_list_with_no_text_blocks(self):
        content = [{"type": "image", "data": "..."}, {"type": "tool_use", "id": "1"}]
        assert ClaudeHarnessClient._extract_text_content(content) is None

    def test_empty_list(self):
        assert ClaudeHarnessClient._extract_text_content([]) is None

    def test_none_input(self):
        assert ClaudeHarnessClient._extract_text_content(None) is None

    def test_dict_input(self):
        assert ClaudeHarnessClient._extract_text_content({"type": "text", "text": "hi"}) is None

    def test_non_string_text_field(self):
        content = [{"type": "text", "text": 42}]
        assert ClaudeHarnessClient._extract_text_content(content) is None


# ===========================================================================
# ClaudeHarnessClient._read_session_metadata (title extraction)
# ===========================================================================


class TestReadSessionMetadataTitle:
    """Tests for title extraction in ClaudeHarnessClient._read_session_metadata()."""

    def _write_jsonl(self, path: Path, lines: list[dict]) -> str:
        file_path = path / "session.jsonl"
        with open(file_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return str(file_path)

    def test_extracts_title_from_string_content(self, tmp_path: Path):
        fp = self._write_jsonl(tmp_path, [
            {"message": {"role": "user", "content": "Fix the login bug"}},
        ])
        session = ClaudeSession()
        ClaudeHarnessClient._read_session_metadata(fp, session)
        assert session.title == "Fix the login bug"

    def test_extracts_title_from_content_blocks(self, tmp_path: Path):
        fp = self._write_jsonl(tmp_path, [
            {"message": {"role": "user", "content": [{"type": "text", "text": "Add caching"}]}},
        ])
        session = ClaudeSession()
        ClaudeHarnessClient._read_session_metadata(fp, session)
        assert session.title == "Add caching"

    def test_truncates_title_at_250_chars(self, tmp_path: Path):
        long_text = "x" * 500
        fp = self._write_jsonl(tmp_path, [
            {"message": {"role": "user", "content": long_text}},
        ])
        session = ClaudeSession()
        ClaudeHarnessClient._read_session_metadata(fp, session)
        assert session.title == "x" * 250

    def test_skips_assistant_messages_for_title(self, tmp_path: Path):
        fp = self._write_jsonl(tmp_path, [
            {"message": {"role": "assistant", "content": "I can help with that"}},
            {"message": {"role": "user", "content": "Build the feature"}},
        ])
        session = ClaudeSession()
        ClaudeHarnessClient._read_session_metadata(fp, session)
        assert session.title == "Build the feature"

    def test_does_not_overwrite_existing_title(self, tmp_path: Path):
        fp = self._write_jsonl(tmp_path, [
            {"message": {"role": "user", "content": "New title"}},
        ])
        session = ClaudeSession(title="Original title")
        ClaudeHarnessClient._read_session_metadata(fp, session)
        assert session.title == "Original title"

    def test_extracts_cwd_branch_and_title_together(self, tmp_path: Path):
        fp = self._write_jsonl(tmp_path, [
            {"cwd": "/home/user/project", "gitBranch": "main"},
            {"message": {"role": "user", "content": "Refactor auth"}},
        ])
        session = ClaudeSession()
        ClaudeHarnessClient._read_session_metadata(fp, session)
        assert session.cwd == "/home/user/project"
        assert session.branch == "main"
        assert session.title == "Refactor auth"

    def test_handles_nonexistent_file(self):
        session = ClaudeSession()
        ClaudeHarnessClient._read_session_metadata("/no/such/file.jsonl", session)
        assert session.title is None

    def test_handles_empty_file(self, tmp_path: Path):
        fp = str(tmp_path / "empty.jsonl")
        Path(fp).write_text("", encoding="utf-8")
        session = ClaudeSession()
        ClaudeHarnessClient._read_session_metadata(fp, session)
        assert session.title is None

    def test_skips_malformed_json_lines(self, tmp_path: Path):
        fp = str(tmp_path / "bad.jsonl")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("not json\n")
            f.write(json.dumps({"message": {"role": "user", "content": "Good line"}}) + "\n")
        session = ClaudeSession()
        ClaudeHarnessClient._read_session_metadata(fp, session)
        assert session.title == "Good line"


# ===========================================================================
# CopilotHarnessClient._parse_simple_yaml
# ===========================================================================


class TestParseSimpleYaml:
    """Tests for CopilotHarnessClient._parse_simple_yaml()."""

    def _write_yaml(self, path: Path, content: str) -> str:
        fp = path / "test.yaml"
        fp.write_text(content, encoding="utf-8")
        return str(fp)

    def test_basic_key_value_pairs(self, tmp_path: Path):
        fp = self._write_yaml(tmp_path, "name: my-project\nbranch: main\n")
        result = CopilotHarnessClient._parse_simple_yaml(fp)
        assert result == {"name": "my-project", "branch": "main"}

    def test_double_quoted_values_stripped(self, tmp_path: Path):
        fp = self._write_yaml(tmp_path, 'summary: "Hello World"\n')
        result = CopilotHarnessClient._parse_simple_yaml(fp)
        assert result == {"summary": "Hello World"}

    def test_single_quoted_values_stripped(self, tmp_path: Path):
        fp = self._write_yaml(tmp_path, "summary: 'Hello World'\n")
        result = CopilotHarnessClient._parse_simple_yaml(fp)
        assert result == {"summary": "Hello World"}

    def test_skips_comments(self, tmp_path: Path):
        fp = self._write_yaml(tmp_path, "# comment\nname: proj\n")
        result = CopilotHarnessClient._parse_simple_yaml(fp)
        assert result == {"name": "proj"}

    def test_skips_blank_lines(self, tmp_path: Path):
        fp = self._write_yaml(tmp_path, "\nname: proj\n\n")
        result = CopilotHarnessClient._parse_simple_yaml(fp)
        assert result == {"name": "proj"}

    def test_skips_indented_lines(self, tmp_path: Path):
        fp = self._write_yaml(tmp_path, "name: proj\n  nested: value\n\tindented: tab\n")
        result = CopilotHarnessClient._parse_simple_yaml(fp)
        assert result == {"name": "proj"}

    def test_first_colon_as_separator(self, tmp_path: Path):
        fp = self._write_yaml(tmp_path, "url: https://example.com:8080/path\n")
        result = CopilotHarnessClient._parse_simple_yaml(fp)
        assert result == {"url": "https://example.com:8080/path"}

    def test_empty_value_excluded(self, tmp_path: Path):
        fp = self._write_yaml(tmp_path, "name:\nbranch: main\n")
        result = CopilotHarnessClient._parse_simple_yaml(fp)
        assert result == {"branch": "main"}

    def test_nonexistent_file(self):
        result = CopilotHarnessClient._parse_simple_yaml("/no/such/file.yaml")
        assert result == {}

    def test_empty_file(self, tmp_path: Path):
        fp = self._write_yaml(tmp_path, "")
        result = CopilotHarnessClient._parse_simple_yaml(fp)
        assert result == {}


# ===========================================================================
# CopilotHarnessClient._read_session_start_metadata
# ===========================================================================


class TestReadSessionStartMetadata:
    """Tests for CopilotHarnessClient._read_session_start_metadata()."""

    def _make_session_dir(self, tmp_path: Path, events: list[dict] | None = None, yaml_content: str | None = None) -> str:
        session_dir = tmp_path / "session1"
        session_dir.mkdir()
        events_file = session_dir / "events.jsonl"
        if events is not None:
            with open(events_file, "w", encoding="utf-8") as f:
                for e in events:
                    f.write(json.dumps(e) + "\n")
        else:
            events_file.write_text("", encoding="utf-8")
        if yaml_content is not None:
            (session_dir / "workspace.yaml").write_text(yaml_content, encoding="utf-8")
        return str(events_file)

    def test_extracts_metadata_from_session_start(self, tmp_path: Path):
        events = [{"type": "session.start", "data": {"context": {
            "cwd": "/home/user/proj",
            "gitRoot": "/home/user/proj",
            "branch": "main",
            "repository": "user/proj",
        }}}]
        fp = self._make_session_dir(tmp_path, events=events)
        session = ClaudeSession()
        CopilotHarnessClient._read_session_start_metadata(fp, session)
        assert session.cwd == "/home/user/proj"
        assert session.git_root == "/home/user/proj"
        assert session.branch == "main"
        assert session.repository == "user/proj"

    def test_reads_title_from_workspace_yaml(self, tmp_path: Path):
        fp = self._make_session_dir(tmp_path, events=[], yaml_content="summary: Fix login bug\n")
        session = ClaudeSession()
        CopilotHarnessClient._read_session_start_metadata(fp, session)
        assert session.title == "Fix login bug"

    def test_truncates_workspace_yaml_title_at_250(self, tmp_path: Path):
        long_title = "y" * 500
        fp = self._make_session_dir(tmp_path, events=[], yaml_content=f"summary: {long_title}\n")
        session = ClaudeSession()
        CopilotHarnessClient._read_session_start_metadata(fp, session)
        assert session.title == "y" * 250

    def test_events_fields_take_priority_over_yaml(self, tmp_path: Path):
        events = [{"type": "session.start", "data": {"context": {
            "branch": "feature",
            "cwd": "/from/events",
        }}}]
        yaml_content = "branch: main\ncwd: /from/yaml\nsummary: My Title\n"
        fp = self._make_session_dir(tmp_path, events=events, yaml_content=yaml_content)
        session = ClaudeSession()
        CopilotHarnessClient._read_session_start_metadata(fp, session)
        # events.jsonl values take priority (set first, yaml only fills if missing)
        assert session.branch == "feature"
        assert session.cwd == "/from/events"
        # title only comes from yaml
        assert session.title == "My Title"

    def test_handles_missing_workspace_yaml(self, tmp_path: Path):
        events = [{"type": "session.start", "data": {"context": {"cwd": "/proj"}}}]
        fp = self._make_session_dir(tmp_path, events=events, yaml_content=None)
        session = ClaudeSession()
        CopilotHarnessClient._read_session_start_metadata(fp, session)
        assert session.cwd == "/proj"
        assert session.title is None

    def test_handles_empty_events_file(self, tmp_path: Path):
        fp = self._make_session_dir(tmp_path, events=None, yaml_content="summary: From YAML\n")
        session = ClaudeSession()
        CopilotHarnessClient._read_session_start_metadata(fp, session)
        assert session.title == "From YAML"


# ===========================================================================
# TranscriptWatcher._apply_symlink_resolution
# ===========================================================================


class TestApplySymlinkResolution:
    """Tests for TranscriptWatcher._apply_symlink_resolution()."""

    def test_resolves_project_fields(self):
        project = ClaudeProject(
            cwd="/sym/link/proj",
            path="/sym/link/path",
            git_root="/sym/link/root",
        )
        with patch("os.path.realpath", side_effect=lambda p: "/real" + p):
            TranscriptWatcher._apply_symlink_resolution([project])
        assert project.cwd == "/real/sym/link/proj"
        assert project.path == "/real/sym/link/path"
        assert project.git_root == "/real/sym/link/root"

    def test_resolves_session_fields(self):
        session = ClaudeSession(cwd="/sym/sess", git_root="/sym/root")
        project = ClaudeProject(sessions=[session])
        with patch("os.path.realpath", side_effect=lambda p: "/real" + p):
            TranscriptWatcher._apply_symlink_resolution([project])
        assert session.cwd == "/real/sym/sess"
        assert session.git_root == "/real/sym/root"

    def test_none_fields_left_untouched(self):
        project = ClaudeProject(cwd=None, path=None, git_root=None)
        session = ClaudeSession(cwd=None, git_root=None)
        project.sessions = [session]
        with patch("os.path.realpath", side_effect=lambda p: "/real" + p):
            TranscriptWatcher._apply_symlink_resolution([project])
        assert project.cwd is None
        assert project.path is None
        assert session.cwd is None

    def test_empty_project_list_is_noop(self):
        # Should not raise
        TranscriptWatcher._apply_symlink_resolution([])


# ===========================================================================
# TranscriptWatcher.set_resolve_symlinks
# ===========================================================================


class TestSetResolveSymlinks:
    """Tests for TranscriptWatcher.set_resolve_symlinks()."""

    def _make_watcher(self) -> TranscriptWatcher:
        watcher = TranscriptWatcher()
        watcher._projects_cache = ["cached_data"]  # seed cache for invalidation tests
        return watcher

    def test_toggling_on_invalidates_cache(self):
        watcher = self._make_watcher()
        watcher.set_resolve_symlinks(True)
        assert watcher._resolve_symlinks is True
        assert watcher._projects_cache is None

    def test_toggling_off_invalidates_cache(self):
        watcher = self._make_watcher()
        watcher._resolve_symlinks = True
        watcher._projects_cache = ["cached_data"]
        watcher.set_resolve_symlinks(False)
        assert watcher._resolve_symlinks is False
        assert watcher._projects_cache is None

    def test_same_value_does_not_invalidate_cache(self):
        watcher = self._make_watcher()
        watcher.set_resolve_symlinks(False)
        assert watcher._projects_cache == ["cached_data"]

    def test_flag_value_is_updated(self):
        watcher = self._make_watcher()
        assert watcher._resolve_symlinks is False
        watcher.set_resolve_symlinks(True)
        assert watcher._resolve_symlinks is True


# ===========================================================================
# Configuration.resolve_symlinks field alias
# ===========================================================================


class TestConfigResolveSymlinks:
    """Tests for the Configuration.resolve_symlinks field and its camelCase alias."""

    def test_default_is_false(self):
        config = Configuration()
        assert config.resolve_symlinks is False

    def test_camel_case_serialization(self):
        config = Configuration(resolve_symlinks=True)
        data = config.model_dump(by_alias=True)
        assert "resolveSymlinks" in data
        assert data["resolveSymlinks"] is True

    def test_camel_case_deserialization(self):
        config = Configuration.model_validate({"resolveSymlinks": True})
        assert config.resolve_symlinks is True

    def test_round_trip(self):
        config = Configuration(resolve_symlinks=True)
        data = config.model_dump(by_alias=True)
        restored = Configuration.model_validate(data)
        assert restored.resolve_symlinks is True
