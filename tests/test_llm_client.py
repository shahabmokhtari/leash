"""Tests for LLM client response parsing."""

from __future__ import annotations

import json
import re

from leash.models import LLMResponse

# ---------------------------------------------------------------------------
# Parse function stub (mirrors ClaudeCliClient.ParseResponse)
# ---------------------------------------------------------------------------


def parse_llm_response(raw: str) -> LLMResponse:
    """Extract a JSON object from potentially mixed output."""
    # Try to find a JSON object in the text
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL)
    if not match:
        return LLMResponse(
            safety_score=0,
            reasoning="No JSON object found in LLM response",
            category="error",
            success=False,
        )

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return LLMResponse(
            safety_score=0,
            reasoning="No JSON object found in LLM response",
            category="error",
            success=False,
        )

    score = data.get("safetyScore", data.get("safety_score", 0))
    # Clamp 0-100
    score = max(0, min(100, int(score)))

    return LLMResponse(
        safety_score=score,
        reasoning=data.get("reasoning", ""),
        category=data.get("category", "unknown"),
        success=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLLMResponseParsing:
    def test_valid_json_extraction_from_mixed_output(self):
        response = """Here's my analysis:

        {
          "safetyScore": 95,
          "reasoning": "Safe command",
          "category": "safe"
        }
        """
        result = parse_llm_response(response)

        assert result.safety_score == 95
        assert result.reasoning == "Safe command"
        assert result.category == "safe"
        assert result.success is True

    def test_score_clamping_upper(self):
        response = '{"safetyScore": 150, "reasoning": "test", "category": "safe"}'
        result = parse_llm_response(response)
        assert result.safety_score == 100

    def test_score_clamping_lower(self):
        response = '{"safetyScore": -10, "reasoning": "test", "category": "safe"}'
        result = parse_llm_response(response)
        assert result.safety_score == 0

    def test_invalid_json_handling(self):
        response = "This is not valid JSON"
        result = parse_llm_response(response)

        assert result.safety_score == 0
        assert "No JSON object found" in result.reasoning
        assert result.success is False

    def test_missing_fields(self):
        response = '{"safetyScore": 42}'
        result = parse_llm_response(response)

        assert result.safety_score == 42
        assert result.reasoning == ""
        assert result.category == "unknown"

    def test_snake_case_keys(self):
        response = '{"safety_score": 88, "reasoning": "ok", "category": "cautious"}'
        result = parse_llm_response(response)
        assert result.safety_score == 88

    def test_pure_json(self):
        response = '{"safetyScore": 50, "reasoning": "moderate", "category": "cautious"}'
        result = parse_llm_response(response)
        assert result.safety_score == 50
        assert result.success is True
