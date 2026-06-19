"""On-disk response cache.

Key = sha256(model_id | prompt_version | prompt_text | sorted image set). Including
model_id and prompt_version is deliberate: switching model or prompt must NOT reuse
a previous configuration's responses (that would contaminate experiments). The
cache also makes runs resumable — a rate-limit hit leaves completed cells cached,
so a resumed run only fills the gaps and still yields a complete (model,prompt) run.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import List, Optional

DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


class Cache:
    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR, enabled: bool = True):
        self.dir = cache_dir
        self.enabled = enabled
        if self.enabled:
            os.makedirs(self.dir, exist_ok=True)

    @staticmethod
    def make_key(model_id: str, prompt_version: str, prompt: str, image_paths: List[str]) -> str:
        h = hashlib.sha256()
        h.update(model_id.encode("utf-8"))
        h.update(b"|")
        h.update(prompt_version.encode("utf-8"))
        h.update(b"|")
        h.update(prompt.encode("utf-8"))
        h.update(b"|")
        h.update("|".join(sorted(image_paths)).encode("utf-8"))
        return h.hexdigest()

    def _path(self, key: str) -> str:
        return os.path.join(self.dir, key + ".json")

    def get(self, key: str) -> Optional[str]:
        if not self.enabled:
            return None
        path = self._path(key)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)["response"]

    def set(self, key: str, response: str, meta: Optional[dict] = None) -> None:
        if not self.enabled:
            return
        with open(self._path(key), "w", encoding="utf-8") as f:
            json.dump({"response": response, "meta": meta or {}}, f, ensure_ascii=False)
