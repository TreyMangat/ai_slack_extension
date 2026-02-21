from __future__ import annotations

import pytest

from app.services.llm_provider import LLMProvider, LLMProviderError, _extract_json_object


def test_extract_json_object_plain_payload() -> None:
    payload = '{"commit_message":"feat: x","patch":"diff --git a/a b/a\\n"}'
    parsed = _extract_json_object(payload)
    assert parsed["commit_message"] == "feat: x"


def test_extract_json_object_from_code_fence() -> None:
    payload = "```json\n{\"commit_message\":\"feat: y\",\"patch\":\"diff --git a/a b/a\\n\"}\n```"
    parsed = _extract_json_object(payload)
    assert parsed["commit_message"] == "feat: y"


def test_extract_json_object_raises_on_invalid_json() -> None:
    with pytest.raises(LLMProviderError):
        _extract_json_object("not json")


def test_read_gemini_content_from_candidates() -> None:
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": '{"commit_message":"feat: a","patch":"diff --git a/a b/a\\n"}'},
                    ]
                }
            }
        ]
    }
    text = LLMProvider._read_gemini_content(payload)
    assert '"commit_message":"feat: a"' in text
