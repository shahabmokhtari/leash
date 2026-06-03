"""Prompt template file CRUD with hot-reload via watchfiles."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import aiofiles

logger = logging.getLogger(__name__)


class PromptTemplateService:
    """Manages prompt template files with caching and file watching."""

    def __init__(self, prompts_dir: str) -> None:
        self._prompts_dir = Path(prompts_dir).expanduser().resolve()
        self._cache: dict[str, str] = {}
        self._watch_task: asyncio.Task[None] | None = None

        # Create prompts directory if it does not exist
        if not self._prompts_dir.exists():
            try:
                self._prompts_dir.mkdir(parents=True, exist_ok=True)
                logger.debug("Created prompts directory: %s", self._prompts_dir)
            except Exception as e:
                logger.warning("Could not create prompts directory %s: %s", self._prompts_dir, e)

        self._load_all_templates()

    def _load_all_templates(self) -> None:
        """Synchronously load all .txt templates from the prompts directory."""
        if not self._prompts_dir.exists():
            return

        try:
            for file_path in self._prompts_dir.glob("*.txt"):
                try:
                    name = file_path.name
                    content = file_path.read_text(encoding="utf-8")
                    self._cache[name] = content
                    logger.debug("Loaded prompt template: %s", name)
                except Exception as e:
                    logger.warning("Failed to load prompt template %s: %s", file_path, e)

            logger.debug("Loaded %d prompt templates from %s", len(self._cache), self._prompts_dir)
        except Exception as e:
            logger.warning("Failed to enumerate prompt templates in %s: %s", self._prompts_dir, e)

    def start_watching(self) -> None:
        """Start the background file watcher task using watchfiles."""
        if self._watch_task is not None:
            return

        try:
            asyncio.get_running_loop()
            self._watch_task = asyncio.create_task(self._watch_loop())
        except RuntimeError:
            logger.debug("No running event loop, skipping file watcher start")

    def stop_watching(self) -> None:
        """Stop the background file watcher task."""
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None

    async def _watch_loop(self) -> None:
        """Watch the prompts directory for file changes using watchfiles."""
        try:
            from watchfiles import Change, awatch

            logger.debug("Watching for prompt template changes in %s", self._prompts_dir)
            async for changes in awatch(self._prompts_dir):
                for change_type, path_str in changes:
                    path = Path(path_str)
                    if path.suffix != ".txt":
                        continue

                    name = path.name

                    if change_type in (Change.added, Change.modified):
                        try:
                            content = path.read_text(encoding="utf-8")
                            self._cache[name] = content
                            logger.debug("Prompt template updated: %s", name)
                        except Exception as e:
                            logger.warning("Failed to reload prompt template %s: %s", path, e)

                    elif change_type == Change.deleted:
                        self._cache.pop(name, None)
                        logger.debug("Prompt template removed: %s", name)

        except asyncio.CancelledError:
            logger.debug("Prompt template watcher cancelled")
        except ImportError:
            logger.warning("watchfiles not available, prompt template hot-reload disabled")
        except Exception as e:
            logger.warning("Prompt template watcher error: %s", e)

    def get_template(self, template_name: str) -> str | None:
        """Load a template by name, from cache or disk.

        Normalizes the name by stripping directory paths and ensuring .txt extension.
        """
        if not template_name:
            return None

        # Normalize: strip directory path so full-path configs resolve to filename-only keys
        template_name = Path(template_name).name

        # Normalize: ensure .txt extension
        if not template_name.lower().endswith(".txt"):
            template_name += ".txt"

        if template_name in self._cache:
            return self._cache[template_name]

        # Try loading on demand
        file_path = self._prompts_dir / template_name
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8")
                self._cache[template_name] = content
                return content
            except Exception as e:
                logger.warning("Failed to load prompt template %s: %s", template_name, e)

        return None

    async def save_template(self, template_name: str, content: str) -> bool:
        """Save a template to disk and update the cache.

        Returns True on success, False on validation failure or I/O error.
        """
        if not template_name:
            return False

        if not template_name.lower().endswith(".txt"):
            template_name += ".txt"

        # Path traversal protection: reject suspicious characters and verify
        # the resolved path stays within the prompts directory.
        if ".." in template_name or "/" in template_name or "\\" in template_name:
            return False

        try:
            file_path = (self._prompts_dir / template_name).resolve()
            if not str(file_path).startswith(str(self._prompts_dir.resolve()) + os.sep):
                return False
            async with aiofiles.open(file_path, "w") as f:
                await f.write(content)
            self._cache[template_name] = content
            logger.debug("Saved prompt template %s", template_name)
            return True
        except Exception as e:
            logger.error("Failed to save prompt template %s: %s", template_name, e)
            return False

    def get_all_templates(self) -> dict[str, str]:
        """Return a copy of all cached templates."""
        return dict(self._cache)

    def get_template_names(self) -> list[str]:
        """Return a sorted list of template names."""
        return sorted(self._cache.keys())
