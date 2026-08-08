"""Per-file facts cache keyed by content hash (sha256). Deterministic JSON on disk."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class FactsCache:
    def __init__(self, repo_root: Path, root_name: str):
        self.path = Path(repo_root) / ".limen" / "cache" / f"{root_name}.json"
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}
        self._dirty = False

    @staticmethod
    def content_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def get(self, rel_path: str, digest: str):
        entry = self._data.get(rel_path)
        if entry and entry.get("hash") == digest:
            return entry.get("facts"), entry.get("unresolved", [])
        return None

    def put(self, rel_path: str, digest: str, facts: dict, unresolved: list) -> None:
        self._data[rel_path] = {"hash": digest, "facts": facts, "unresolved": unresolved}
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8")
