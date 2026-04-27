"""Subprocess helpers with timeout/retry for local integrations."""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Sequence


def run_command(
    cmd: Sequence[str],
    *,
    timeout_sec: float = 8.0,
    retries: int = 1,
    logger: logging.Logger | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a local command with bounded retries and timeout."""

    attempts = max(1, retries + 1)
    last_error = ""

    for attempt in range(1, attempts + 1):
        try:
            return subprocess.run(list(cmd), check=False, capture_output=True, text=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            last_error = f"timeout after {timeout_sec}s"
            if logger:
                logger.warning("Command timed out (%s/%s): %s", attempt, attempts, " ".join(cmd))
        except Exception as exc:
            last_error = str(exc)
            if logger:
                logger.warning("Command failed (%s/%s): %s err=%s", attempt, attempts, " ".join(cmd), exc)

        if attempt < attempts:
            time.sleep(0.25 * attempt)

    return subprocess.CompletedProcess(args=list(cmd), returncode=124, stdout="", stderr=last_error)
