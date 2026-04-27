from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.alerts.voice import speak, speech_timeout_sec


class VoiceTests(unittest.TestCase):
    def test_speech_timeout_scales_for_long_digest(self) -> None:
        short_timeout = speech_timeout_sec("hello Pluto")
        long_timeout = speech_timeout_sec("word " * 360)

        self.assertGreaterEqual(short_timeout, 6)
        self.assertGreater(long_timeout, 120)

    @patch("app.alerts.voice.run_command")
    def test_speak_uses_dynamic_timeout(self, mock_run) -> None:
        mock_run.return_value = Mock(returncode=0)

        speak("word " * 360)

        self.assertGreater(mock_run.call_args.kwargs["timeout_sec"], 120)


if __name__ == "__main__":
    unittest.main()
