"""Shared integration result models and errors."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class IntegrationError(Exception):
    """Raised when a platform integration fails."""


@dataclass
class ActionResult:
    success: bool
    spoken_response: str
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
