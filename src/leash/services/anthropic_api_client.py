"""Direct Anthropic API LLM client via httpx."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

import httpx

from leash.models.llm_response import LLMResponse
from leash.services.claude_cli_client import parse_response, read_anthropic_api_key
from leash.services.llm_client_base import LLMClientBase, resolve_model_for_provider, resolve_model_name

if TYPE_CHECKING:
    from leash.config import ConfigurationManager
    from leash.services.terminal_output_service import TerminalOutputService

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_DEFAULT_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"


class AnthropicApiClient(LLMClientBase):
    """LLM client that calls the Anthropic Messages API directly via HTTP.

    No subprocess needed -- fastest and most reliable provider.
    Uses API key from config or falls back to ~/.claude/config.json primaryApiKey.
    Retries up to 3 times with exponential backoff for 429/5xx responses.
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

    def _get_api_key(self) -> str:
        """Get the API key from config or fallback to Claude config."""
        config = self._config_manager.get_configuration()
        key = config.llm.api_key
        if key:
            return key

        key = read_anthropic_api_key()
        if key:
            return key

        raise ValueError(
            "No API key configured. Set llm.apiKey in config or ensure "
            "~/.claude/config.json has primaryApiKey."
        )

    @staticmethod
    def _map_model(model: str) -> str:
        """Map short model names to full Anthropic model identifiers."""
        return resolve_model_name(model)

    async def query(self, prompt: str) -> LLMResponse:
        """Send a prompt to the Anthropic Messages API and return a structured response."""
        timeout = self.current_timeout
        total_start = time.monotonic()

        for attempt in range(1, _MAX_RETRIES + 1):
            start = time.monotonic()

            try:
                config = self._config_manager.get_configuration()
                base_url = (config.llm.api_base_url or _DEFAULT_BASE_URL).rstrip("/")
                api_key = self._get_api_key()
                model = self._map_model(resolve_model_for_provider(config, "anthropic-api") or "sonnet")

                request_body = _build_request_body(model, config.llm.system_prompt, prompt)
                self._push_terminal("anthropic-api", "info", f"POST {base_url}/v1/messages (model: {model}, {len(prompt)} chars)")
                self._push_terminal("anthropic-api", "stdout", f"Prompt: {self.preview_prompt(prompt)}")
                logger.info(
                    "POST %s/v1/messages (model: %s, %d chars): %s",
                    base_url,
                    model,
                    len(prompt),
                    self.preview_prompt(prompt),
                )

                response = await self._http_client.post(
                    f"{base_url}/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": _API_VERSION,
                        "content-type": "application/json",
                    },
                    content=request_body,
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
                    self._push_terminal("anthropic-api", "stderr", f"API error {response.status_code}: {preview[:200]}")
                    logger.error("Anthropic API error %d: %s", response.status_code, preview)
                    return self.create_failure_response(
                        f"Anthropic API returned {response.status_code}: {response_body[:200]}",
                        "API request failed",
                        elapsed_ms,
                    )

                # Extract text from Anthropic response
                text = _extract_text_from_response(response_body)
                self._push_terminal("anthropic-api", "info", f"Response received in {elapsed_ms}ms ({len(text)} chars)")
                logger.info("Anthropic API response received in %dms (%d chars)", elapsed_ms, len(text))

                # Parse the safety analysis JSON from the LLM text
                result = parse_response(text)
                result.elapsed_ms = elapsed_ms
                return result

            except httpx.TimeoutException:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "Attempt %d/%d timed out after %dms, retrying...",
                        attempt,
                        _MAX_RETRIES,
                        elapsed_ms,
                    )
                    continue

                total_elapsed = int((time.monotonic() - total_start) * 1000)
                return self.create_timeout_response("Anthropic API", _MAX_RETRIES, timeout, total_elapsed)

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
                logger.error("Anthropic API request failed after %d attempts: %s", _MAX_RETRIES, exc)
                return self.create_failure_response(
                    f"Anthropic API request failed: {exc}",
                    "Network error calling Anthropic API",
                    total_elapsed,
                )

            except ValueError as exc:
                logger.error("Anthropic API configuration error: %s", exc)
                return self.create_failure_response(str(exc), str(exc))

        return self.create_retries_exhausted_response("Anthropic API")


def _build_request_body(model: str, system_prompt: str | None, prompt: str) -> str:
    """Build the JSON request body for the Anthropic Messages API."""
    body: dict = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        body["system"] = system_prompt
    return json.dumps(body)


def _extract_text_from_response(response_body: str) -> str:
    """Extract text content from an Anthropic Messages API response."""
    try:
        data = json.loads(response_body)
        content = data.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
    except json.JSONDecodeError:
        pass
    return ""
