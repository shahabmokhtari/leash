"""Shared test fixtures for Leash tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from leash.app import create_app
from leash.config import ConfigurationManager, create_default_configuration
from leash.models.configuration import Configuration
from leash.models.llm_response import LLMResponse


@pytest.fixture
def tmp_config_path(tmp_path: Path) -> Path:
    """Temporary config file path."""
    return tmp_path / "config.json"


@pytest.fixture
def tmp_sessions_dir(tmp_path: Path) -> Path:
    """Temporary sessions directory."""
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def tmp_prompts_dir(tmp_path: Path) -> Path:
    """Temporary prompts directory with sample templates."""
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "bash-prompt.txt").write_text("Analyze bash command: {COMMAND}")
    (d / "file-read-prompt.txt").write_text("Analyze file read: {FILE_PATH}")
    return d


@pytest.fixture
def sample_config() -> Configuration:
    """A default configuration instance for testing."""
    return create_default_configuration()


@pytest.fixture
def config_manager(tmp_config_path: Path) -> ConfigurationManager:
    """A ConfigurationManager backed by a temporary file."""
    return ConfigurationManager(config_path=tmp_config_path)


@pytest.fixture
def mock_llm_client() -> AsyncMock:
    """A mock LLM client that returns a safe response."""
    client = AsyncMock()
    client.query.return_value = LLMResponse(
        success=True, safety_score=95, reasoning="Safe", category="safe"
    )
    return client


@pytest.fixture
def app(tmp_config_path: Path):
    """A FastAPI test application."""
    application = create_app(config_path=str(tmp_config_path))
    application.state.cli_no_browser = True
    application.state.cli_no_hooks = True
    return application


@pytest.fixture
def client(app) -> TestClient:
    """A synchronous test client for the FastAPI app."""
    return TestClient(app)
