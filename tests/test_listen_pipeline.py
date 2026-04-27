from __future__ import annotations

import unittest

from app.audio.listen_pipeline import AlwaysOnListener


class ListenPipelinePhraseTests(unittest.TestCase):
    def test_exit_phrase_detection(self) -> None:
        self.assertTrue(AlwaysOnListener._is_exit_listening_phrase("stop listening"))
        self.assertTrue(AlwaysOnListener._is_exit_listening_phrase("exit pluto"))
        self.assertFalse(AlwaysOnListener._is_exit_listening_phrase("open browser"))

    def test_strip_wake_phrase_from_command(self) -> None:
        self.assertEqual(AlwaysOnListener._strip_wake_phrase("Hey Pluto, run research."), "run research.")
        self.assertEqual(AlwaysOnListener._strip_wake_phrase("Pluto open browser"), "open browser")
        self.assertEqual(AlwaysOnListener._strip_wake_phrase("Pluto stop listening."), "stop listening.")
        self.assertEqual(AlwaysOnListener._strip_wake_phrase("Hey Pluto."), "")
        self.assertEqual(AlwaysOnListener._strip_wake_phrase("run research"), "run research")

    def test_exit_phrase_detection_after_wake_phrase_strip(self) -> None:
        command = AlwaysOnListener._strip_wake_phrase("Pluto stop listening.")

        self.assertTrue(AlwaysOnListener._is_exit_listening_phrase(command))


if __name__ == "__main__":
    unittest.main()
