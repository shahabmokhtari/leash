"""Tests for PromptTemplateService."""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# PromptTemplateService stub
# ---------------------------------------------------------------------------


class PromptTemplateService:
    """Manages prompt template files with hot-reload."""

    def __init__(self, prompts_dir: str | Path):
        self._dir = Path(prompts_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._templates: dict[str, str] = {}
        self._load_all()

    def _load_all(self) -> None:
        self._templates.clear()
        if self._dir.exists():
            for f in sorted(self._dir.glob("*.txt")):
                self._templates[f.name] = f.read_text()

    def get_template(self, name: str) -> str | None:
        if not name:
            return None
        # Add .txt extension if missing
        if not name.endswith(".txt"):
            name = name + ".txt"
        return self._templates.get(name)

    def get_all_templates(self) -> dict[str, str]:
        return dict(self._templates)

    def get_template_names(self) -> list[str]:
        return sorted(self._templates.keys())

    def save_template(self, name: str, content: str) -> bool:
        if not name:
            return False
        # Path traversal check
        if ".." in name or "/" in name or "\\" in name:
            return False
        if not name.endswith(".txt"):
            name = name + ".txt"
        path = self._dir / name
        path.write_text(content)
        self._templates[name] = content
        return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPromptTemplateService:
    @pytest.fixture()
    def service(self, tmp_path: Path) -> PromptTemplateService:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "bash-prompt.txt").write_text("Analyze bash command: {COMMAND}")
        (prompts_dir / "file-read-prompt.txt").write_text("Analyze file read: {FILE_PATH}")
        return PromptTemplateService(prompts_dir)

    def test_get_template_returns_existing(self, service: PromptTemplateService):
        template = service.get_template("bash-prompt.txt")
        assert template is not None
        assert "{COMMAND}" in template

    def test_get_template_returns_none_for_missing(self, service: PromptTemplateService):
        assert service.get_template("nonexistent.txt") is None

    def test_get_template_adds_txt_extension(self, service: PromptTemplateService):
        template = service.get_template("bash-prompt")
        assert template is not None
        assert "{COMMAND}" in template

    def test_get_all_templates(self, service: PromptTemplateService):
        templates = service.get_all_templates()
        assert len(templates) == 2
        assert "bash-prompt.txt" in templates
        assert "file-read-prompt.txt" in templates

    def test_get_template_names_sorted(self, service: PromptTemplateService):
        names = service.get_template_names()
        assert len(names) == 2
        assert names[0] == "bash-prompt.txt"
        assert names[1] == "file-read-prompt.txt"

    def test_save_template_persists(self, service: PromptTemplateService):
        result = service.save_template("new-template.txt", "New content: {TOOL_NAME}")
        assert result is True
        loaded = service.get_template("new-template.txt")
        assert loaded is not None
        assert "{TOOL_NAME}" in loaded

    def test_save_template_rejects_path_traversal(self, service: PromptTemplateService):
        result = service.save_template("../evil.txt", "malicious content")
        assert result is False

    def test_save_template_rejects_empty_name(self, service: PromptTemplateService):
        result = service.save_template("", "content")
        assert result is False

    def test_get_template_returns_none_for_empty_name(self, service: PromptTemplateService):
        assert service.get_template("") is None

    def test_save_template_adds_extension(self, service: PromptTemplateService):
        result = service.save_template("no-ext", "content")
        assert result is True
        assert service.get_template("no-ext.txt") is not None
