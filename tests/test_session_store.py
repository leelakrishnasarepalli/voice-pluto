from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.state.session_store import save_last_transcript


class SessionStoreTests(unittest.TestCase):
    def test_save_last_transcript_with_numpy_scalar_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_path = Path(tmp_dir) / "session.json"
            save_last_transcript(
                session_path,
                "open browser",
                metadata={"wakeword": "alexa", "wake_score": np.float32(0.999)},
            )

            payload = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["last_transcript"], "open browser")
            self.assertEqual(payload["metadata"]["wakeword"], "alexa")
            self.assertAlmostEqual(payload["metadata"]["wake_score"], 0.999, places=6)


if __name__ == "__main__":
    unittest.main()
