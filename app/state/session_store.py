"""Session state persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _to_json_compatible(value: Any) -> Any:
    """Convert common runtime scalar wrappers (e.g., numpy scalars) into JSON-safe values."""

    if isinstance(value, dict):
        return {str(k): _to_json_compatible(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_compatible(item) for item in value]
    if hasattr(value, "item") and value.__class__.__module__.startswith("numpy"):
        return value.item()
    return value


def save_last_transcript(path: Path, transcript: str, metadata: dict[str, Any] | None = None) -> None:
    """Persist the last recognized transcript for debugging and downstream steps."""

    payload = {
        "last_transcript": transcript,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    if metadata:
        payload["metadata"] = _to_json_compatible(metadata)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
