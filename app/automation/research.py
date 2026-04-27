"""Research collection workflow support."""

from __future__ import annotations

import html
import logging
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.config import PlutoSettings
from app.integrations.base import ActionResult
from app.integrations.notes_adapter import NotesAdapter


MAX_HTML_BYTES = 600_000
MAX_SUMMARY_CHARS = 8_000
HTTP_TIMEOUT_SEC = 8


@dataclass(frozen=True)
class ResearchSource:
    name: str
    kind: str
    url: str


@dataclass(frozen=True)
class ResearchItem:
    source_name: str
    source_url: str
    title: str
    url: str
    summary: str = ""


@dataclass
class ResearchCollection:
    title: str
    generated_at: str
    sections: dict[str, list[ResearchItem]] = field(default_factory=dict)
    skipped_count: int = 0
    open_errors: list[str] = field(default_factory=list)

    @property
    def item_count(self) -> int:
        return sum(len(items) for items in self.sections.values())


class ResearchCollector:
    """Fetches research sources, summarizes linked pages, and writes an HTML note."""

    def __init__(
        self,
        settings: PlutoSettings,
        notes: NotesAdapter,
        *,
        logger: logging.Logger | None = None,
        fetch_html: Callable[[str], str] | None = None,
        summarize: Callable[[ResearchItem, str], str] | None = None,
    ) -> None:
        self.settings = settings
        self.notes = notes
        self.logger = logger or logging.getLogger(__name__)
        self.fetch_html = fetch_html or fetch_url_html
        self.summarize = summarize or self._summarize_with_openai

    def collect_to_note(
        self,
        raw_sources: list[dict] | None,
        *,
        limit_per_source: int,
        read_mode: str = "concise",
    ) -> ActionResult:
        if not self.settings.openai_api_key:
            return ActionResult(
                success=False,
                spoken_response="I need OPENAI_API_KEY set before I can summarize research.",
                error="missing_openai_api_key",
            )

        sources = parse_sources(raw_sources)
        if not sources:
            return ActionResult(
                success=False,
                spoken_response="No research sources are configured.",
                error="missing_research_sources",
            )

        now = datetime.now(ZoneInfo(self.settings.timezone))
        collection = ResearchCollection(
            title=f"Research notes - {now.strftime('%Y-%m-%d %H:%M')}",
            generated_at=now.strftime("%Y-%m-%d %H:%M %Z"),
        )

        limit = max(1, min(int(limit_per_source), 20))
        for source in sources:
            try:
                source_html = self.fetch_html(source.url)
                candidates = extract_source_items(source, source_html, limit)
                if not candidates:
                    self.logger.warning("Research source produced no candidates: %s (%s)", source.name, source.url)
                    collection.skipped_count += limit
                    collection.sections[source.name] = []
                    continue
            except Exception as exc:
                self.logger.warning("Research source failed: %s (%s): %s", source.name, source.url, exc)
                collection.skipped_count += limit
                collection.sections[source.name] = []
                continue

            section_items: list[ResearchItem] = []
            for item in candidates:
                try:
                    article_html = self.fetch_html(item.url)
                    article_text = extract_readable_text(article_html)
                    capped_text = article_text[:MAX_SUMMARY_CHARS]
                    summary = self.summarize(item, capped_text).strip()
                    if not summary:
                        summary = fallback_summary(capped_text)
                    section_items.append(
                        ResearchItem(
                            source_name=item.source_name,
                            source_url=item.source_url,
                            title=item.title,
                            url=item.url,
                            summary=summary,
                        )
                    )
                except Exception as exc:
                    self.logger.warning("Research item failed: %s (%s): %s", item.title, item.url, exc)
                    collection.skipped_count += 1
                    section_items.append(
                        ResearchItem(
                            source_name=item.source_name,
                            source_url=item.source_url,
                            title=item.title,
                            url=item.url,
                            summary="Open the article for full details. I could not fetch readable page text automatically.",
                        )
                    )

            collection.sections[source.name] = section_items

        html_body = render_research_note_html(collection)
        result = self.notes.create_html_note(collection.title, html_body)
        if not result.success:
            return result

        return ActionResult(
            success=True,
            spoken_response=f"Research note created with {collection.item_count} items.",
            data={
                "title": collection.title,
                "items": collection.item_count,
                "skipped": collection.skipped_count,
                "sources": [source.name for source in sources],
                "spoken_digest": build_spoken_research_digest(collection, mode=read_mode),
            },
        )

    def _summarize_with_openai(self, item: ResearchItem, article_text: str) -> str:
        if not article_text.strip():
            raise ValueError("empty article text")

        from openai import OpenAI

        client = OpenAI(api_key=self.settings.openai_api_key, timeout=self.settings.intent_timeout_sec)
        response = client.responses.create(
            model=self.settings.research_model,
            max_output_tokens=140,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the webpage for a personal research digest. "
                        "Return 2 concise bullets. Do not use markdown links."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Title: {item.title}\nURL: {item.url}\n\nContent:\n{article_text}",
                },
            ],
        )
        summary = (response.output_text or "").strip()
        if summary:
            return summary
        return fallback_summary(article_text)


def parse_sources(raw_sources: list[dict] | None) -> list[ResearchSource]:
    sources: list[ResearchSource] = []
    for raw in raw_sources or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        kind = str(raw.get("kind", "")).strip()
        url = str(raw.get("url", "")).strip()
        if name and kind and is_safe_http_url(url):
            sources.append(ResearchSource(name=name, kind=kind, url=url))
    return sources


def fetch_url_html(url: str) -> str:
    if not is_safe_http_url(url):
        raise ValueError(f"unsafe URL: {url}")

    errors = []
    for fetcher in (_fetch_url_html_urllib, _fetch_url_html_curl, _fetch_url_html_chrome):
        try:
            return fetcher(url)
        except Exception as exc:
            errors.append(f"{fetcher.__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


def _fetch_url_html_urllib(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 VoicePluto/1.0 research collector",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower() and "text/plain" not in content_type.lower():
                raise ValueError(f"unsupported content type: {content_type}")
            payload = response.read(MAX_HTML_BYTES + 1)
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    if len(payload) > MAX_HTML_BYTES:
        payload = payload[:MAX_HTML_BYTES]
    return payload.decode("utf-8", errors="replace")


def _fetch_url_html_curl(url: str) -> str:
    completed = subprocess.run(
        [
            "curl",
            "-L",
            "--max-time",
            str(HTTP_TIMEOUT_SEC),
            "-A",
            "Mozilla/5.0 VoicePluto/1.0 research collector",
            "-H",
            "Accept: text/html,application/xhtml+xml",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=HTTP_TIMEOUT_SEC + 2,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"curl exited {completed.returncode}")
    if not completed.stdout.strip():
        raise RuntimeError("curl returned empty body")
    return completed.stdout[:MAX_HTML_BYTES]


def _fetch_url_html_chrome(url: str) -> str:
    escaped_url = _escape_applescript(url)
    script = (
        'tell application "Google Chrome"\n'
        "  activate\n"
        "  if (count of windows) = 0 then make new window\n"
        f'  set URL of active tab of front window to "{escaped_url}"\n'
        "  delay 3\n"
        '  return execute active tab of front window javascript "document.documentElement.outerHTML"\n'
        "end tell"
    )
    completed = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=HTTP_TIMEOUT_SEC + 8,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"osascript exited {completed.returncode}")
    if not completed.stdout.strip():
        raise RuntimeError("Chrome returned empty page source")
    return completed.stdout[:MAX_HTML_BYTES]


def extract_source_items(source: ResearchSource, page_html: str, limit: int) -> list[ResearchItem]:
    soup = BeautifulSoup(page_html, "html.parser")
    if source.kind == "hacker_news":
        return extract_hacker_news_items(source, soup, limit)
    if source.kind == "openai_news":
        return extract_openai_news_items(source, soup, page_html, limit)
    if source.kind == "anthropic_learn":
        return extract_link_items(source, soup, limit, host=None, required_path=None)
    return extract_link_items(source, soup, limit, host=None, required_path=None)


def extract_hacker_news_items(source: ResearchSource, soup: BeautifulSoup, limit: int) -> list[ResearchItem]:
    items: list[ResearchItem] = []
    for row in soup.select("tr.athing"):
        anchor = row.select_one(".titleline a")
        if anchor is None:
            continue
        item = item_from_anchor(source, anchor)
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return dedupe_items(items)[:limit]


def extract_openai_news_items(source: ResearchSource, soup: BeautifulSoup, page_html: str, limit: int) -> list[ResearchItem]:
    items: list[ResearchItem] = []
    source_path = urllib.parse.urlparse(source.url).path.rstrip("/")

    for anchor in soup.find_all("a"):
        href = str(anchor.get("href", "")).strip()
        if not href:
            continue

        url = urllib.parse.urljoin(source.url, href)
        if not is_safe_http_url(url):
            continue

        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.lower().replace("www.", "") != "openai.com":
            continue
        path = parsed.path.rstrip("/")
        if not path.startswith("/news/") or path == source_path:
            continue
        if path in {"/news", "/news/company-announcements"}:
            continue

        title = title_from_openai_anchor(anchor, path)
        if not title:
            continue

        items.append(ResearchItem(source_name=source.name, source_url=source.url, title=title, url=url))
        if len(dedupe_items(items)) >= limit:
            break

    if len(dedupe_items(items)) < limit:
        for path in re.findall(r"(?:https://openai\.com)?(/news/[a-z0-9][a-z0-9-]+/?)", page_html, flags=re.IGNORECASE):
            normalized_path = path.rstrip("/")
            if normalized_path == source_path or normalized_path in {"/news", "/news/company-announcements"}:
                continue
            url = urllib.parse.urljoin(source.url, path)
            title = " ".join(part.capitalize() for part in normalized_path.split("/")[-1].split("-") if part)
            items.append(ResearchItem(source_name=source.name, source_url=source.url, title=title, url=url))
            if len(dedupe_items(items)) >= limit:
                break

    return dedupe_items(items)[:limit]


def extract_link_items(
    source: ResearchSource,
    soup: BeautifulSoup,
    limit: int,
    *,
    host: str | None,
    required_path: str | None,
) -> list[ResearchItem]:
    items: list[ResearchItem] = []
    source_url = urllib.parse.urlparse(source.url)
    for anchor in soup.find_all("a"):
        item = item_from_anchor(source, anchor)
        if item is None:
            continue

        parsed = urllib.parse.urlparse(item.url)
        if host is not None and parsed.netloc.lower().replace("www.", "") != host:
            continue
        if required_path is not None and not parsed.path.startswith(required_path):
            continue
        if parsed.path.rstrip("/") == source_url.path.rstrip("/"):
            continue
        if item.title.lower() in {"learn more", "see all courses", "courses"}:
            continue

        items.append(item)
        if len(dedupe_items(items)) >= limit:
            break
    return dedupe_items(items)[:limit]


def item_from_anchor(source: ResearchSource, anchor) -> ResearchItem | None:
    title = " ".join(anchor.get_text(" ", strip=True).split())
    href = str(anchor.get("href", "")).strip()
    if not title or not href:
        return None

    url = urllib.parse.urljoin(source.url, href)
    if not is_safe_http_url(url):
        return None
    return ResearchItem(source_name=source.name, source_url=source.url, title=title, url=url)


def title_from_openai_anchor(anchor, path: str) -> str:
    raw = " ".join(anchor.get_text(" ", strip=True).split())
    title = clean_openai_title(raw)
    if title:
        return title

    for parent in anchor.parents:
        name = getattr(parent, "name", "")
        if name not in {"article", "li", "div"}:
            continue
        parent_text = " ".join(parent.get_text(" ", strip=True).split())
        title = clean_openai_title(parent_text)
        if title:
            return title

    slug = path.rstrip("/").split("/")[-1]
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def clean_openai_title(value: str) -> str:
    title = re.sub(r"\b(?:Company|Research|Product|Safety|Engineering|Security|Global Affairs|AI Adoption)\b\s+[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}$", "", value)
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) < 8:
        return ""
    if title.lower() in {"recent news", "load more", "filter", "sort"}:
        return ""
    if title.lower().startswith("image:"):
        return ""
    return title


def dedupe_items(items: list[ResearchItem]) -> list[ResearchItem]:
    seen: set[str] = set()
    output: list[ResearchItem] = []
    for item in items:
        key = item.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def extract_readable_text(page_html: str) -> str:
    soup = BeautifulSoup(page_html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "form", "noscript", "svg"]):
        tag.decompose()

    candidates = soup.select("article, main")
    root = candidates[0] if candidates else soup.body or soup
    parts: list[str] = []
    for node in root.find_all(["h1", "h2", "h3", "p", "li"]):
        text = " ".join(node.get_text(" ", strip=True).split())
        if len(text) >= 40:
            parts.append(text)
    if not parts:
        text = " ".join(root.get_text(" ", strip=True).split())
        return text[:MAX_SUMMARY_CHARS]
    return "\n".join(parts)[:MAX_SUMMARY_CHARS]


def render_research_note_html(collection: ResearchCollection) -> str:
    body = [
        f"<h1>{html.escape(collection.title)}</h1>",
        f"<p>Generated {html.escape(collection.generated_at)}.</p>",
    ]

    for source_name, items in collection.sections.items():
        body.append(f"<h2>{html.escape(source_name)}</h2>")
        if not items:
            body.append("<p>No items collected.</p>")
            continue

        body.append("<ul>")
        for item in items:
            body.append("<li>")
            body.append(f"<p><strong>{html.escape(item.title)}</strong></p>")
            body.append(f'<p><a href="{html.escape(item.url, quote=True)}">Open article</a></p>')
            body.append(f'<p><a href="{html.escape(item.source_url, quote=True)}">Source page</a></p>')
            body.append(f"<p>{html.escape(item.summary).replace(chr(10), '<br>')}</p>")
            body.append("</li>")
        body.append("</ul>")

    body.append("<h2>Run details</h2>")
    body.append(f"<p>Skipped or failed items: {collection.skipped_count}</p>")
    body.append("<h2>Follow-ups</h2>")
    body.append("<ul><li></li></ul>")
    return "\n".join(body)


def build_spoken_research_digest(collection: ResearchCollection, mode: str = "concise") -> str:
    if collection.item_count == 0:
        return "I created the research note, but I did not collect any readable items."

    normalized_mode = (mode or "concise").strip().lower()
    parts = [f"I created the research note with {collection.item_count} items. Here is the research briefing."]
    for source_name, items in collection.sections.items():
        if not items:
            continue
        parts.append(f"From {source_name}.")
        for item in items:
            summary = spoken_summary(item.summary, mode=normalized_mode)
            if normalized_mode == "headlines" or not summary:
                parts.append(item.title)
            else:
                parts.append(f"{item.title}. {summary}")

    if collection.skipped_count:
        parts.append(f"I skipped {collection.skipped_count} items that I could not read.")
    parts.append("The full note has clickable links for each article.")
    return " ".join(parts)


def spoken_summary(summary: str, mode: str = "concise") -> str:
    if mode in {"briefing", "detailed"}:
        return summary_excerpt(summary, max_sentences=2, max_chars=420)
    return first_summary_sentence(summary)


def summary_excerpt(summary: str, *, max_sentences: int, max_chars: int) -> str:
    cleaned = clean_summary_text(summary)
    if not cleaned:
        return ""

    sentences = re.findall(r".+?(?:[.!?])(?:\s|$)", cleaned)
    if sentences:
        excerpt = " ".join(sentence.strip() for sentence in sentences[:max_sentences])
    else:
        excerpt = cleaned
    return excerpt[:max_chars].rstrip()


def first_summary_sentence(summary: str) -> str:
    cleaned = clean_summary_text(summary)
    if not cleaned:
        return ""

    match = re.search(r"(.+?[.!?])(?:\s|$)", cleaned)
    if match:
        return match.group(1).strip()
    return cleaned[:180].rstrip()


def clean_summary_text(summary: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", summary)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"(?m)^\s*[-*]\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def fallback_summary(article_text: str) -> str:
    cleaned = clean_summary_text(article_text)
    if not cleaned:
        return "Open the article for full details."
    return cleaned[:360].rstrip()


def is_safe_http_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _escape_applescript(value: str) -> str:
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    return value
