"""VLM client abstraction.

The pipeline depends only on this interface, so the concrete model is a swappable
detail (model selection does not block the rest of the system, and comparing
strategies = swapping clients). Each client exposes a `model_id` that becomes part
of the cache key, so responses from different models never contaminate each other.

`analyze(prompt, image_paths)` returns the model's RAW text response (expected to
contain JSON). Parsing/validation live in the pipeline, not here.

Concrete client: GeminiVLMClient (native google-genai SDK). DeepSeek is not a VLM
(text-only API) and is intentionally not implemented for the core visual role.
"""

from __future__ import annotations

import json
import os
import time
from typing import List


class VLMClient:
    model_id: str = "base"

    def analyze(self, prompt: str, image_paths: List[str]) -> str:
        raise NotImplementedError


class StubVLMClient(VLMClient):
    """Deterministic, offline stub. Returns a conservative verdict so the whole
    pipeline can be exercised end-to-end with no API, cost, or nondeterminism.
    Useful as a plumbing test and as a baseline producer."""

    model_id = "stub-v1"

    def analyze(self, prompt: str, image_paths: List[str]) -> str:
        return json.dumps({
            "evidence_standard_met": False,
            "evidence_standard_met_reason": "stub client: no visual analysis performed",
            "risk_flags": "none",
            "issue_type": "unknown",
            "object_part": "unknown",
            "claim_status": "not_enough_information",
            "claim_status_justification": "stub client: image evidence not assessed",
            "supporting_image_ids": "none",
            "valid_image": True,
            "severity": "unknown",
        })


def _mime_for(path: str) -> str:
    p = path.lower()
    if p.endswith(".png"):
        return "image/png"
    if p.endswith(".webp"):
        return "image/webp"
    if p.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _image_id(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


class GeminiVLMClient(VLMClient):
    """Native Google Gemini client (multimodal). Reads GEMINI_API_KEY (or
    GOOGLE_API_KEY) from the env per AGENTS.md.

    Each image is preceded by a text part naming its image ID, so the model can
    map its `supporting_image_ids` answer to specific images. JSON output is
    requested via response_mime_type for reliable parsing.

    model_id must be an exact current Gemini model name (the SDK validates it);
    pass it explicitly — naming changes over time, so no default is hardcoded.
    """

    def __init__(self, model_id: str, max_retries: int = 2):
        if not model_id:
            raise ValueError("GeminiVLMClient requires an explicit model_id (e.g. a gemini-*-flash / -pro id)")
        self.model_id = model_id
        self.max_retries = max_retries
        # Lazy import so stub/eval paths don't require the package.
        from google import genai
        from google.genai import types
        self._genai = genai
        self._types = types
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self._client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def analyze(self, prompt: str, image_paths: List[str]) -> str:
        types = self._types
        contents = [self._types.Part.from_text(text=prompt)]
        for path in image_paths:
            with open(path, "rb") as f:
                data = f.read()
            contents.append(types.Part.from_text(text=f"Image id: {_image_id(path)}"))
            contents.append(types.Part.from_bytes(data=data, mime_type=_mime_for(path)))

        config = types.GenerateContentConfig(response_mime_type="application/json")

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.models.generate_content(
                    model=self.model_id, contents=contents, config=config,
                )
                return resp.text or ""
            except Exception as e:  # transient (rate limit / 5xx); backoff and retry
                last_exc = e
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
        raise last_exc


def get_client(name: str = "stub", model_id: str | None = None) -> VLMClient:
    """Factory so main.py can select a client by CLI flag."""
    if name == "stub":
        return StubVLMClient()
    if name == "gemini":
        return GeminiVLMClient(model_id=model_id or "")
    raise ValueError(f"unknown client {name!r}")
