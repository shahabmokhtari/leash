"""Generic REST endpoint LLM client via httpx."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from leash.models.llm_response import LLMResponse
from leash.services.claude_cli_client import parse_response
from leash.services.llm_client_base import LLMClientBase

if TYPE_CHECKING:
    from leash.config import ConfigurationManager
    from leash.services.terminal_output_service import TerminalOutputService

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


class GenericRestClient(LLMClientBase):
    """LLM client that calls a configurable REST endpoint.

    Supports any LLM API (OpenAI, local, etc.) via URL/headers/body template.
    The body template uses {PROMPT} as a placeholder for the user prompt.
    Response text is extracted using a dot-notation path
    (e.g. "choices[0].message.content").
    Retries up to 3 times with exponential backoff for 429/5xx.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        config_manager: ConfigurationManager,
        terminal_output: TerminalOutputService | None = None,
    ) -> None:
        super().__init__(config_manager=config_manager, terminal_output=terminal_output)
        if http_client is None:
            raise ValueError("http_client is required")
        if config_manager is None:
            raise ValueError("config_manager is required")
        self._http_client = http_client
        self._config_manager = config_manager

    async def query(self, prompt: str) -> LLMResponse:
        """Send a prompt to the configured REST endpoint and return a structured response."""
        config = self._config_manager.get_configuration()
        rest_config = config.llm.generic_rest

        if rest_config is None or not rest_config.url:
            return self.create_failure_response(
                "Generic REST not configured. Set llm.genericRest.url in config.",
                "Missing REST endpoint configuration",
            )

        timeout = self.current_timeout
        total_start = time.monotonic()

        for attempt in range(1, _MAX_RETRIES + 1):
            start = time.monotonic()

            try:
                # Replace {PROMPT} in body template with JSON-escaped prompt
                escaped_prompt = json.dumps(prompt)
                # Strip outer quotes -- the template should provide its own quotes
                prompt_value = escaped_prompt[1:-1]
                body = (rest_config.body_template or "").replace("{PROMPT}", prompt_value)

                self._push_terminal("generic-rest", "info", f"POST {rest_config.url} ({len(prompt)} chars)")
                self._push_terminal("generic-rest", "stdout", f"Prompt: {self.preview_prompt(prompt)}")
                logger.info(
                    "POST %s (%d chars): %s",
                    rest_config.url,
                    len(prompt),
                    self.preview_prompt(prompt),
                )

                headers = dict(rest_config.headers)
                headers.setdefault("content-type", "application/json")

                response = await self._http_client.post(
                    rest_config.url,
                    headers=headers,
                    content=body,
                    timeout=timeout / 1000.0,
                )

                elapsed_ms = int((time.monotonic() - start) * 1000)

                # Retry on rate limit or server errors
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < _MAX_RETRIES:
                        delay = (2**attempt) * 0.5
                        logger.warning(
                            "Attempt %d/%d got %d, retrying in %.1fs...",
                            attempt,
                            _MAX_RETRIES,
                            response.status_code,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                response_body = response.text

                if response.status_code >= 400:
                    preview = response_body[:500] if len(response_body) > 500 else response_body
                    self._push_terminal("generic-rest", "stderr", f"API error {response.status_code}: {preview[:200]}")
                    logger.error("REST API error %d: %s", response.status_code, preview)
                    return self.create_failure_response(
                        f"REST API returned {response.status_code}",
                        "API request failed",
                        elapsed_ms,
                    )

                # Extract text using configured response path
                text = extract_by_path(response_body, rest_config.response_path)
                self._push_terminal("generic-rest", "info", f"Response received in {elapsed_ms}ms ({len(text)} chars)")
                logger.info("Generic REST response received in %dms (%d chars)", elapsed_ms, len(text))

                # Parse the safety analysis JSON from the text
                result = parse_response(text)
                result.elapsed_ms = elapsed_ms
                return result

            except httpx.TimeoutException:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "Attempt %d/%d timed out, retrying...",
                        attempt,
                        _MAX_RETRIES,
                    )
                    continue

                total_elapsed = int((time.monotonic() - total_start) * 1000)
                return self.create_timeout_response("REST API", _MAX_RETRIES, timeout, total_elapsed)

            except httpx.HTTPError as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if attempt < _MAX_RETRIES:
                    delay = (2**attempt) * 0.5
                    logger.warning(
                        "Attempt %d/%d failed: %s, retrying in %.1fs...",
                        attempt,
                        _MAX_RETRIES,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                total_elapsed = int((time.monotonic() - total_start) * 1000)
                logger.error("Generic REST request failed after %d attempts: %s", _MAX_RETRIES, exc)
                return self.create_failure_response(
                    f"REST API request failed: {exc}",
                    "Network error",
                    total_elapsed,
                )

        return self.create_retries_exhausted_response("REST API")


def extract_by_path(json_str: str, path: str | None) -> str:
    """Extract a string value from JSON using a simple dot-notation path with array index support.

    E.g. "choices[0].message.content" navigates: choices -> [0] -> message -> content.

    Args:
        json_str: The raw JSON string to parse and navigate.
        path: Dot-notation path with optional array indices. None or empty returns raw JSON.

    Returns:
        The extracted string value, or empty string if navigation fails.
    """
    if not path:
        return json_str

    try:
        data: Any = json.loads(json_str)
    except json.JSONDecodeError:
        return ""

    current: Any = data
    segments = path.split(".")

    for segment in segments:
        bracket_idx = segment.find("[")
        if bracket_idx >= 0:
            prop = segment[:bracket_idx]
            close_bracket = segment.find("]")
            if close_bracket < 0:
                return ""
            idx_str = segment[bracket_idx + 1 : close_bracket]

            if prop:
                if not isinstance(current, dict) or prop not in current:
                    return ""
                current = current[prop]

            try:
                array_idx = int(idx_str)
            except ValueError:
                return ""

            if not isinstance(current, list) or array_idx >= len(current):
                return ""
            current = current[array_idx]
        else:
            if not isinstance(current, dict) or segment not in current:
                return ""
            current = current[segment]

    if isinstance(current, str):
        return current
    return json.dumps(current)
