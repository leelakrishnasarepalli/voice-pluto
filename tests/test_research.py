from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.automation.research import (
    ResearchCollector,
    ResearchCollection,
    ResearchItem,
    ResearchSource,
    build_spoken_research_digest,
    extract_readable_text,
    extract_source_items,
    fallback_summary,
    first_summary_sentence,
    render_research_note_html,
    spoken_summary,
)
from app.config import PlutoSettings
from app.integrations.base import ActionResult
from app.integrations.executor import AssistantExecutor
from app.integrations.notes_adapter import NotesAdapter
from app.intent.schema import ParsedIntent


HN_HTML = """
<html><body><table>
<tr class="athing"><td class="title"><span class="titleline"><a href="https://example.com/a">Article A</a></span></td></tr>
<tr class="athing"><td class="title"><span class="titleline"><a href="item?id=1">HN Discussion</a></span></td></tr>
</table></body></html>
"""

OPENAI_HTML = """
<html><body>
<a href="/news/introducing-one">Introducing One Product Apr 23, 2026</a>
<a href="/news/introducing-two">Introducing Two Product Apr 22, 2026</a>
<a href="/research">Research</a>
</body></html>
"""

OPENAI_CARD_HTML = """
<html><body>
<main>
<article><a href="/news/scaling-codex-to-enterprises-worldwide/"><img alt=""></a><h2>Scaling Codex to enterprises worldwide</h2><p>Company Apr 21, 2026</p></article>
<article><a href="/news/the-next-phase-of-enterprise-ai/">The next phase of enterprise AI Company Apr 8, 2026</a></article>
</main>
</body></html>
"""

ANTHROPIC_HTML = """
<html><body><main>
<a href="https://anthropic.skilljar.com/course-a">Featured Course Claude Code in action</a>
<a href="/learn/build-with-claude">Build with Claude</a>
<a href="/learn/claude-for-work">Claude for work</a>
</main><footer><a href="/privacy">Privacy policy</a></footer></body></html>
"""

ARTICLE_HTML = """
<html><body>
<nav>Navigation should disappear</nav>
<main>
<h1>Article title</h1>
<p>This is a useful paragraph with enough text to be extracted for a research summary.</p>
<script>bad()</script>
</main>
</body></html>
"""


class ResearchParserTests(unittest.TestCase):
    def test_extracts_source_items(self) -> None:
        hn = ResearchSource(name="Hacker News", kind="hacker_news", url="https://news.ycombinator.com/")
        openai = ResearchSource(name="OpenAI", kind="openai_news", url="https://openai.com/news/company-announcements/")
        anthropic = ResearchSource(name="Anthropic", kind="anthropic_learn", url="https://www.anthropic.com/learn")

        self.assertEqual(extract_source_items(hn, HN_HTML, 5)[0].url, "https://example.com/a")
        self.assertEqual(extract_source_items(openai, OPENAI_HTML, 5)[0].url, "https://openai.com/news/introducing-one")
        self.assertIn("Featured Course", extract_source_items(anthropic, ANTHROPIC_HTML, 5)[0].title)

    def test_extracts_openai_items_when_anchor_text_is_sparse(self) -> None:
        openai = ResearchSource(name="OpenAI", kind="openai_news", url="https://openai.com/news/company-announcements/")

        items = extract_source_items(openai, OPENAI_CARD_HTML, 5)

        self.assertEqual(items[0].title, "Scaling Codex to enterprises worldwide")
        self.assertEqual(items[0].url, "https://openai.com/news/scaling-codex-to-enterprises-worldwide/")
        self.assertEqual(items[1].title, "The next phase of enterprise AI")

    def test_extracts_openai_items_from_raw_page_paths(self) -> None:
        openai = ResearchSource(name="OpenAI", kind="openai_news", url="https://openai.com/news/company-announcements/")
        html = '<html><script>{"href":"/news/openai-acquires-example/"}</script></html>'

        items = extract_source_items(openai, html, 5)

        self.assertEqual(items[0].title, "Openai Acquires Example")
        self.assertEqual(items[0].url, "https://openai.com/news/openai-acquires-example/")

    def test_extract_readable_text_strips_noise(self) -> None:
        text = extract_readable_text(ARTICLE_HTML)

        self.assertIn("useful paragraph", text)
        self.assertNotIn("Navigation", text)
        self.assertNotIn("bad()", text)

    def test_render_research_note_html_escapes_text_and_keeps_links(self) -> None:
        item = ResearchItem(
            source_name="Source",
            source_url="https://example.com/source",
            title="<Title>",
            url="https://example.com/article?a=1&b=2",
            summary="Line 1\nLine 2",
        )
        collection = Mock(
            title="Research <notes>",
            generated_at="2026-04-25 10:00 EDT",
            sections={"Source": [item]},
            skipped_count=1,
        )

        html = render_research_note_html(collection)

        self.assertIn("&lt;Title&gt;", html)
        self.assertIn("https://example.com/article?a=1&amp;b=2", html)
        self.assertIn("Line 1<br>Line 2", html)
        self.assertIn("Skipped or failed items: 1", html)

    def test_build_spoken_digest_groups_sources_and_titles(self) -> None:
        collection = ResearchCollection(title="Research", generated_at="now")
        collection.sections = {
            "Hacker News": [
                ResearchItem(
                    source_name="Hacker News",
                    source_url="https://news.ycombinator.com/",
                    title="Article A",
                    url="https://example.com/a",
                    summary="- First useful sentence. Second sentence.",
                )
            ],
            "OpenAI": [
                ResearchItem(
                    source_name="OpenAI",
                    source_url="https://openai.com/news",
                    title="Announcement B",
                    url="https://openai.com/news/b",
                    summary="Announcement summary without extra detail. More detail.",
                )
            ],
        }

        spoken = build_spoken_research_digest(collection)

        self.assertIn("with 2 items", spoken)
        self.assertIn("From Hacker News", spoken)
        self.assertIn("Article A. First useful sentence.", spoken)
        self.assertIn("From OpenAI", spoken)

    def test_first_summary_sentence_is_concise(self) -> None:
        summary = "- This is the first sentence. This should not be spoken in concise mode."

        self.assertEqual(first_summary_sentence(summary), "This is the first sentence.")

    def test_briefing_summary_keeps_more_detail(self) -> None:
        summary = "- First sentence. Second sentence. Third sentence."

        self.assertEqual(spoken_summary(summary, mode="briefing"), "First sentence. Second sentence.")

    def test_fallback_summary_uses_article_text(self) -> None:
        summary = fallback_summary("This article text is readable and should appear when OpenAI returns no text.")

        self.assertIn("readable", summary)


class ResearchCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.settings = PlutoSettings(
            openai_api_key="test-key",
            workflows_path=Path(self.tmp_dir.name) / "workflows.json",
            whitelist_path=Path(self.tmp_dir.name) / "sites.json",
            wakeword_models=["alexa"],
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_collect_to_note_groups_summaries(self) -> None:
        html_by_url = {
            "https://news.ycombinator.com/": HN_HTML,
            "https://example.com/a": ARTICLE_HTML,
            "https://news.ycombinator.com/item?id=1": ARTICLE_HTML,
        }
        notes = Mock()
        notes.create_html_note.return_value = ActionResult(success=True, spoken_response="Note created.")

        collector = ResearchCollector(
            self.settings,
            notes,
            fetch_html=lambda url: html_by_url[url],
            summarize=lambda item, _text: f"Summary for {item.title}",
        )
        result = collector.collect_to_note(
            [{"name": "Hacker News", "kind": "hacker_news", "url": "https://news.ycombinator.com/"}],
            limit_per_source=2,
            read_mode="briefing",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["items"], 2)
        self.assertIn("spoken_digest", result.data)
        title, html_body = notes.create_html_note.call_args.args
        self.assertIn("Research notes -", title)
        self.assertIn("Summary for Article A", html_body)
        self.assertIn("Open article", html_body)

    def test_missing_openai_key_fails(self) -> None:
        settings = self.settings.model_copy(update={"openai_api_key": ""})
        collector = ResearchCollector(settings, Mock())

        result = collector.collect_to_note([], limit_per_source=5)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "missing_openai_api_key")

    def test_article_fetch_failure_keeps_title_and_link(self) -> None:
        notes = Mock()
        notes.create_html_note.return_value = ActionResult(success=True, spoken_response="Note created.")

        def fetch(url: str) -> str:
            if url == "https://news.ycombinator.com/":
                return HN_HTML
            raise RuntimeError("network blocked")

        collector = ResearchCollector(
            self.settings,
            notes,
            fetch_html=fetch,
            summarize=lambda item, _text: f"Summary for {item.title}",
        )
        result = collector.collect_to_note(
            [{"name": "Hacker News", "kind": "hacker_news", "url": "https://news.ycombinator.com/"}],
            limit_per_source=1,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["items"], 1)
        self.assertEqual(result.data["skipped"], 1)
        _title, html_body = notes.create_html_note.call_args.args
        self.assertIn("Article A", html_body)
        self.assertIn("Open the article for full details", html_body)


class ResearchExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.workflow_path = Path(self.tmp_dir.name) / "workflows.json"
        self.whitelist_path = Path(self.tmp_dir.name) / "sites.json"
        self.workflow_path.write_text(json.dumps({"workflows": [{"name": "research", "steps": [{"intent": "collect_research"}]}]}))
        self.whitelist_path.write_text(
            json.dumps({"allowed_sites": [{"name": "Hacker News", "url": "https://news.ycombinator.com/"}]}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    @patch("app.integrations.executor.speak")
    def test_collect_research_opens_configured_sources_nonfatally(self, _mock_speak) -> None:
        settings = PlutoSettings(
            openai_api_key="test-key",
            workflows_path=self.workflow_path,
            whitelist_path=self.whitelist_path,
            wakeword_models=["alexa"],
        )
        executor = AssistantExecutor(settings, logger=Mock())
        executor.research = Mock()
        executor.research.collect_to_note.return_value = ActionResult(
            success=True,
            spoken_response="Research note created.",
            data={"spoken_digest": "Here is the digest."},
        )
        executor.chrome.open_site = Mock(return_value=ActionResult(success=True, spoken_response="Opening Hacker News."))

        result = executor.execute(
            ParsedIntent(
                intent="collect_research",
                utterance="workflow research",
                confidence=1.0,
                sources=[{"name": "Hacker News", "kind": "hacker_news", "url": "https://news.ycombinator.com/"}],
                limit_per_source=5,
                open_sources=True,
                read_aloud=True,
                read_mode="briefing",
            )
        )

        self.assertTrue(result.success)
        executor.research.collect_to_note.assert_called_once_with(
            [{"name": "Hacker News", "kind": "hacker_news", "url": "https://news.ycombinator.com/"}],
            limit_per_source=5,
            read_mode="briefing",
        )
        executor.chrome.open_site.assert_called_once()
        self.assertEqual(_mock_speak.call_count, 3)

    @patch("app.integrations.executor.speak")
    def test_collect_research_does_not_read_when_disabled(self, mock_speak) -> None:
        settings = PlutoSettings(
            openai_api_key="test-key",
            workflows_path=self.workflow_path,
            whitelist_path=self.whitelist_path,
            wakeword_models=["alexa"],
        )
        executor = AssistantExecutor(settings, logger=Mock())
        executor.research = Mock()
        executor.research.collect_to_note.return_value = ActionResult(
            success=True,
            spoken_response="Research note created.",
            data={"spoken_digest": "Digest should stay silent."},
        )

        result = executor.execute(
            ParsedIntent(
                intent="collect_research",
                utterance="workflow research",
                confidence=1.0,
                sources=[],
                limit_per_source=5,
                read_aloud=False,
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(mock_speak.call_count, 2)
        mock_speak.assert_any_call("Starting research. This can take a minute.", settings)
        mock_speak.assert_any_call("Research note created.", settings)

    @patch("app.integrations.executor.speak")
    def test_collect_research_does_not_read_when_note_creation_fails(self, mock_speak) -> None:
        settings = PlutoSettings(
            openai_api_key="test-key",
            workflows_path=self.workflow_path,
            whitelist_path=self.whitelist_path,
            wakeword_models=["alexa"],
        )
        executor = AssistantExecutor(settings, logger=Mock())
        executor.research = Mock()
        executor.research.collect_to_note.return_value = ActionResult(
            success=False,
            spoken_response="I couldn't create the note.",
            error="notes_failed",
            data={"spoken_digest": "Digest should stay silent."},
        )

        result = executor.execute(
            ParsedIntent(
                intent="collect_research",
                utterance="workflow research",
                confidence=1.0,
                sources=[],
                limit_per_source=5,
                read_aloud=True,
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(mock_speak.call_count, 2)
        mock_speak.assert_any_call("Starting research. This can take a minute.", settings)
        mock_speak.assert_any_call("I couldn't create the note.", settings)


class NotesHtmlTests(unittest.TestCase):
    @patch("app.integrations.notes_adapter.run_command")
    def test_create_html_note_escapes_applescript_and_preserves_links(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = NotesAdapter().create_html_note(
            'Research "quote"',
            '<h1>Research</h1><p><a href="https://example.com?a=1&amp;b=2">Open article</a></p>',
        )

        self.assertTrue(result.success)
        script = mock_run.call_args.args[0][2]
        self.assertIn('\\"https://example.com?a=1&amp;b=2\\"', script)
        self.assertIn("Open article", script)


if __name__ == "__main__":
    unittest.main()
