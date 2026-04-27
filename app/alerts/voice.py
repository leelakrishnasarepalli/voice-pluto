"""Local voice output helpers (macOS say)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import PlutoSettings
from app.utils.process_utils import run_command


def _parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        hh, mm = value.split(":", 1)
        h = int(hh)
        m = int(mm)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        return h, m
    except Exception:
        return None


def is_voice_suppressed(settings: PlutoSettings) -> bool:
    if settings.do_not_disturb:
        return True

    if not settings.quiet_hours_enabled:
        return False

    start = _parse_hhmm(settings.quiet_hours_start)
    end = _parse_hhmm(settings.quiet_hours_end)
    if start is None or end is None:
        return False

    now = datetime.now(ZoneInfo(settings.timezone)).time()
    s = now.replace(hour=start[0], minute=start[1], second=0, microsecond=0)
    e = now.replace(hour=end[0], minute=end[1], second=0, microsecond=0)

    if start == end:
        return False
    if start < end:
        return s <= now < e
    return now >= s or now < e


def speak(text: str, settings: PlutoSettings | None = None, *, force: bool = False) -> bool:
    """Speak text using local macOS TTS.

    Returns True when speech was attempted, False if suppressed or empty.
    """

    safe_text = text.strip()
    if not safe_text:
        return False

    if settings is not None and not force and is_voice_suppressed(settings):
        return False

    result = run_command(["say", safe_text], timeout_sec=speech_timeout_sec(safe_text), retries=1)
    return result.returncode == 0


def speech_timeout_sec(text: str) -> int:
    """Estimate enough time for macOS say to finish without hanging forever."""

    word_count = len(text.split())
    estimated = int(word_count / 2.2) + 8
    return max(6, min(240, estimated))
