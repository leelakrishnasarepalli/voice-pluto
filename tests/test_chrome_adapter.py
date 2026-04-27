from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.integrations.chrome_adapter import ChromeAdapter


class ChromeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.whitelist = Path(self.tmp_dir.name) / "sites.json"
        self.whitelist.write_text(
            json.dumps(
                {
                    "allowed_sites": [
                        {"name": "YouTube", "url": "https://www.youtube.com"},
                        {"name": "Wikipedia", "url": "https://www.wikipedia.org"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.adapter = ChromeAdapter(self.whitelist)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    @patch("app.utils.process_utils.subprocess.run")
    def test_open_browser(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        result = self.adapter.open_site(site_name="browser", site_url=None, utterance="open browser")
        self.assertTrue(result.success)
        self.assertIn("Opening Chrome", result.spoken_response)

    @patch("app.utils.process_utils.subprocess.run")
    def test_open_whitelisted_site(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        result = self.adapter.open_site(site_name="youtube", site_url=None, utterance="open youtube")
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("site_name"), "YouTube")

    @patch("app.utils.process_utils.subprocess.run")
    def test_block_non_whitelisted_site(self, mock_run) -> None:
        result = self.adapter.open_site(site_name=None, site_url=None, utterance="open twitter")
        self.assertFalse(result.success)
        self.assertIn("blocked", result.spoken_response.lower())
        mock_run.assert_not_called()

    @patch("app.utils.process_utils.subprocess.run")
    def test_block_host_spoofing_variants(self, mock_run) -> None:
        # Contains allowed name as substring, but actual host is not whitelisted.
        result = self.adapter.open_site(
            site_name=None,
            site_url="https://www.youtube.com.evil.example",
            utterance="open https://www.youtube.com.evil.example",
        )
        self.assertFalse(result.success)
        mock_run.assert_not_called()

        result2 = self.adapter.open_site(
            site_name=None,
            site_url=None,
            utterance="open notyoutube.com",
        )
        self.assertFalse(result2.success)
        mock_run.assert_not_called()

    @patch("app.utils.process_utils.subprocess.run")
    def test_allow_exact_whitelisted_host(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        result = self.adapter.open_site(site_name=None, site_url="https://www.youtube.com/watch?v=1", utterance="open this")
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("site_name"), "YouTube")


if __name__ == "__main__":
    unittest.main()
