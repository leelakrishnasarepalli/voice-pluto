"""Persistence for announced item dedupe state."""

from __future__ import annotations

import json
import time
from pathlib import Path


class AnnouncementStore:
    """Tracks which background items were already announced."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen: dict[str, float] = {}
        self._load()

    def has_announced(self, key: str) -> bool:
        return key in self.seen

    def mark_announced(self, key: str) -> None:
        self.seen[key] = time.time()
        self._prune(max_age_sec=60 * 60 * 24 * 7)
        self._save()

    def _prune(self, max_age_sec: int) -> None:
        cutoff = time.time() - max_age_sec
        self.seen = {k: ts for k, ts in self.seen.items() if ts >= cutoff}

    def _load(self) -> None:
        if not self.path.exists():
            return

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            raw = payload.get("seen", {})
            if isinstance(raw, dict):
                self.seen = {str(k): float(v) for k, v in raw.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            self.seen = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"seen": self.seen}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
