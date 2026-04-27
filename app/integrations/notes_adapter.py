"""Apple Notes integration via AppleScript."""

from __future__ import annotations

import re

from app.integrations.base import ActionResult
from app.utils.process_utils import run_command


class NotesAdapter:
    """Create/read notes using local AppleScript bridge."""

    def create_html_note(self, title: str, html_body: str) -> ActionResult:
        safe_title = title.strip() or "Pluto Note"
        body = html_body.strip() or f"<h1>{self._escape_html(safe_title)}</h1>"
        escaped = self._escape_applescript_preserve(body)
        script = (
            'tell application "Notes"\n'
            '  tell default account\n'
            '    make new note with properties {body:"' + escaped + '"}\n'
            '  end tell\n'
            'end tell'
        )

        result = self._run_applescript(script)
        if result.returncode != 0:
            return ActionResult(success=False, spoken_response="I couldn't create the note.", error=result.stderr.strip())

        return ActionResult(success=True, spoken_response="Note created.")

    def create_note(self, note_text: str) -> ActionResult:
        body = note_text.strip()
        if not body:
            return ActionResult(success=False, spoken_response="I need note text first.", error="empty_note")

        escaped = self._escape_applescript(body)
        script = (
            'tell application "Notes"\n'
            '  tell default account\n'
            '    make new note with properties {body:"' + escaped + '"}\n'
            '  end tell\n'
            'end tell'
        )

        result = self._run_applescript(script)
        if result.returncode != 0:
            return ActionResult(success=False, spoken_response="I couldn't create the note.", error=result.stderr.strip())

        return ActionResult(success=True, spoken_response="Note created.")

    def read_recent_notes(self, limit: int = 5) -> ActionResult:
        safe_limit = max(1, min(limit, 20))
        script = (
            'tell application "Notes"\n'
            '  tell default account\n'
            '    set nList to notes\n'
            '    set maxN to ' + str(safe_limit) + '\n'
            '    if (count of nList) < maxN then\n'
            '      set maxN to (count of nList)\n'
            '    end if\n'
            '    set outText to ""\n'
            '    repeat with i from 1 to maxN\n'
            '      set n to item i of nList\n'
            '      set outText to outText & (name of n) & "\\n"\n'
            '    end repeat\n'
            '    return outText\n'
            '  end tell\n'
            'end tell'
        )

        result = self._run_applescript(script)
        if result.returncode != 0:
            return ActionResult(
                success=False,
                spoken_response="I can't read notes directly on this system yet, but I can still create notes.",
                error=result.stderr.strip(),
            )

        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return ActionResult(success=True, spoken_response="You have no notes.", data={"notes": []})

        return ActionResult(
            success=True,
            spoken_response=f"I found {len(lines)} notes.",
            data={"notes": lines},
        )

    @staticmethod
    def _run_applescript(script: str):
        return run_command(["osascript", "-e", script], timeout_sec=10, retries=1)

    @staticmethod
    def _escape_applescript(value: str) -> str:
        value = value.replace("\\", "\\\\")
        value = value.replace('"', '\\"')
        value = re.sub(r"\s+", " ", value).strip()
        return value

    @staticmethod
    def _escape_applescript_preserve(value: str) -> str:
        value = value.replace("\\", "\\\\")
        value = value.replace('"', '\\"')
        value = value.replace("\r", "")
        value = value.replace("\n", "\\n")
        return value

    @staticmethod
    def _escape_html(value: str) -> str:
        value = value.replace("&", "&amp;")
        value = value.replace("<", "&lt;")
        value = value.replace(">", "&gt;")
        return value
