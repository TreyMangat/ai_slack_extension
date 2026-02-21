from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings


class LLMProviderError(RuntimeError):
    pass


@dataclass
class LLMCodePatch:
    commit_message: str
    patch: str
    rationale: str = ""


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise LLMProviderError("LLM response was empty")

    if raw.startswith("```"):
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL | re.IGNORECASE)
        if match:
            raw = match.group(1).strip()

    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMProviderError(f"Could not parse LLM JSON payload: {e}") from e
    if not isinstance(parsed, dict):
        raise LLMProviderError("LLM JSON payload must be an object")
    return parsed


class LLMProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _read_openai_content(data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            raise LLMProviderError("Unexpected chat completion response format") from e
        if not isinstance(content, str):
            raise LLMProviderError("LLM completion content was not a string")
        return content

    @staticmethod
    def _read_gemini_content(data: dict[str, Any]) -> str:
        try:
            candidates = data.get("candidates") or []
            candidate = candidates[0]
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
        except Exception as e:  # noqa: BLE001
            raise LLMProviderError("Unexpected Gemini response format") from e
        chunks: list[str] = []
        for part in parts:
            text = str((part or {}).get("text") or "").strip()
            if text:
                chunks.append(text)
        merged = "\n".join(chunks).strip()
        if not merged:
            raise LLMProviderError("Gemini response did not contain text output")
        return merged

    def _chat_complete_openai(self, messages: list[dict[str, str]], *, api_key: str) -> str:
        base = self.settings.llm_api_base.rstrip("/")
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "response_format": {"type": "json_object"},
        }

        with httpx.Client(timeout=120) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        if not isinstance(data, dict):
            raise LLMProviderError("OpenAI response was not a JSON object")
        return self._read_openai_content(data)

    def _chat_complete_gemini(self, messages: list[dict[str, str]], *, api_key: str) -> str:
        base = self.settings.llm_api_base.rstrip("/")
        model = (self.settings.llm_model or "").strip()
        if not model:
            raise LLMProviderError("LLM_MODEL is required for Gemini mode")
        url = f"{base}/models/{model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "X-goog-api-key": api_key,
        }

        # Gemini API does not use OpenAI-style role objects; flatten to text.
        text_blocks = []
        for message in messages:
            role = str(message.get("role") or "user").strip()
            content = str(message.get("content") or "").strip()
            if content:
                text_blocks.append(f"[{role}] {content}")
        prompt = "\n\n".join(text_blocks).strip()
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.settings.llm_temperature,
                "responseMimeType": "application/json",
            },
        }

        with httpx.Client(timeout=120) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise LLMProviderError("Gemini response was not a JSON object")
        return self._read_gemini_content(data)

    def _chat_complete(self, messages: list[dict[str, str]]) -> str:
        provider = (self.settings.llm_provider or "").strip().lower()
        if provider in {"", "openai"}:
            provider = "openai"
        elif provider in {"google", "gemini"}:
            provider = "gemini"
        else:
            raise LLMProviderError(f"Unsupported LLM_PROVIDER '{self.settings.llm_provider}'")

        api_key = (self.settings.llm_api_key or "").strip()
        if not api_key:
            raise LLMProviderError("LLM_API_KEY is required for native_llm mode")

        if provider == "openai":
            return self._chat_complete_openai(messages, api_key=api_key)
        return self._chat_complete_gemini(messages, api_key=api_key)

    def request_code_patch(
        self,
        *,
        optimized_prompt: str,
        repository_context: str,
        previous_failure: str = "",
    ) -> LLMCodePatch:
        system_prompt = (
            "You are a senior software engineer. "
            "Return strict JSON with keys: commit_message, patch, rationale. "
            "patch must be a unified git diff (no markdown fences). "
            "Do not include commentary outside JSON."
        )

        user_prompt = (
            "Implement the request below by producing a patch that can be applied with `git apply`.\n\n"
            "Request brief:\n"
            f"{optimized_prompt}\n\n"
            "Repository context:\n"
            f"{repository_context}\n\n"
            "Previous test/build failure context (if any):\n"
            f"{previous_failure or '(none)'}\n\n"
            "Output JSON only."
        )

        content = self._chat_complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        parsed = _extract_json_object(content)

        commit_message = str(parsed.get("commit_message") or "").strip()
        patch = str(parsed.get("patch") or "").strip()
        rationale = str(parsed.get("rationale") or "").strip()

        if not patch:
            raise LLMProviderError("LLM did not return a patch")
        if not commit_message:
            commit_message = "feat: implement requested feature"

        return LLMCodePatch(commit_message=commit_message, patch=patch, rationale=rationale)
