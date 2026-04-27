"""Persistent timer manager for in-app timers."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TimerEntry:
    timer_id: str
    label: str
    duration_seconds: int
    created_at_epoch: float
    expires_at_epoch: float

    def remaining_seconds(self, now_epoch: float | None = None) -> int:
        now = now_epoch if now_epoch is not None else time.time()
        return max(0, int(round(self.expires_at_epoch - now)))


class TimerStore:
    """Timer persistence and lifecycle operations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.timers: list[TimerEntry] = []
        self._load()

    def set_timer(self, duration_seconds: int, label: str | None = None) -> TimerEntry:
        now = time.time()
        entry = TimerEntry(
            timer_id=uuid.uuid4().hex[:8],
            label=(label or "timer").strip() or "timer",
            duration_seconds=int(duration_seconds),
            created_at_epoch=now,
            expires_at_epoch=now + int(duration_seconds),
        )
        self.timers.append(entry)
        self._save()
        return entry

    def list_active(self) -> list[TimerEntry]:
        now = time.time()
        active = [t for t in self.timers if t.expires_at_epoch > now]
        active.sort(key=lambda t: t.expires_at_epoch)
        return active

    def cancel(self, target: str | None) -> TimerEntry | None:
        active = self.list_active()
        if not active:
            return None

        if target is None or not target.strip():
            cancelled = active[0]
            self.timers = [t for t in self.timers if t.timer_id != cancelled.timer_id]
            self._save()
            return cancelled

        query = target.strip().lower()
        direct = next((t for t in active if t.timer_id.lower().startswith(query)), None)
        if direct is None:
            direct = next((t for t in active if query in t.label.lower()), None)
        if direct is None:
            return None

        self.timers = [t for t in self.timers if t.timer_id != direct.timer_id]
        self._save()
        return direct

    def pop_due(self) -> list[TimerEntry]:
        now = time.time()
        due = [t for t in self.timers if t.expires_at_epoch <= now]
        if due:
            due_ids = {d.timer_id for d in due}
            self.timers = [t for t in self.timers if t.timer_id not in due_ids]
            self._save()
        due.sort(key=lambda t: t.expires_at_epoch)
        return due

    def _load(self) -> None:
        if not self.path.exists():
            self.timers = []
            return

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.timers = []
            return

        raw_timers = payload.get("timers", [])
        parsed: list[TimerEntry] = []
        for item in raw_timers:
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(
                    TimerEntry(
                        timer_id=str(item["timer_id"]),
                        label=str(item.get("label", "timer")),
                        duration_seconds=int(item["duration_seconds"]),
                        created_at_epoch=float(item["created_at_epoch"]),
                        expires_at_epoch=float(item["expires_at_epoch"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        self.timers = parsed

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timers": [asdict(t) for t in self.timers]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
