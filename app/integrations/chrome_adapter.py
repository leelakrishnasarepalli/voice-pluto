"""Chrome integration with strict whitelist enforcement."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from urllib.parse import urlparse

from app.integrations.base import ActionResult
from app.utils.process_utils import run_command


@dataclass
class AllowedSite:
    name: str
    url: str


class ChromeAdapter:
    """Launches Chrome and opens only pre-whitelisted sites."""

    def __init__(self, whitelist_path: Path) -> None:
        self.whitelist_path = whitelist_path

    def open_browser(self) -> ActionResult:
        completed = run_command(["open", "-a", "Google Chrome"], timeout_sec=8, retries=1)
        if completed.returncode != 0:
            return ActionResult(success=False, spoken_response="I couldn't open Chrome.", error=completed.stderr.strip())
        return ActionResult(success=True, spoken_response="Opening Chrome.")

    def open_site(self, *, site_name: str | None, site_url: str | None, utterance: str) -> ActionResult:
        if self._is_browser_only(site_name, utterance):
            return self.open_browser()

        sites = self._load_allowed_sites()
        if not sites:
            return ActionResult(
                success=False,
                spoken_response="No allowed sites are configured.",
                error=f"No sites found in {self.whitelist_path}",
            )

        resolved = self._resolve_site(sites=sites, site_name=site_name, site_url=site_url, utterance=utterance)
        if resolved is None:
            return ActionResult(
                success=False,
                spoken_response="That site is blocked. I can only open whitelisted sites.",
                error="non_whitelisted_site",
            )

        completed = run_command(["open", "-a", "Google Chrome", resolved.url], timeout_sec=8, retries=1)
        if completed.returncode != 0:
            return ActionResult(
                success=False,
                spoken_response=f"I couldn't open {resolved.name}.",
                error=completed.stderr.strip(),
            )

        return ActionResult(
            success=True,
            spoken_response=f"Opening {resolved.name}.",
            data={"site_name": resolved.name, "site_url": resolved.url},
        )

    def _load_allowed_sites(self) -> list[AllowedSite]:
        if not self.whitelist_path.exists():
            return []

        try:
            payload = json.loads(self.whitelist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

        output: list[AllowedSite] = []
        for item in payload.get("allowed_sites", []):
            if isinstance(item, dict) and "name" in item and "url" in item:
                output.append(AllowedSite(name=str(item["name"]), url=str(item["url"])))
        return output

    @staticmethod
    def _is_browser_only(site_name: str | None, utterance: str) -> bool:
        candidate = (site_name or "").strip().lower()
        if candidate in {"browser", "chrome", "google chrome"}:
            return True

        normalized = re.sub(r"\s+", " ", utterance.lower()).strip()
        return any(
            phrase in normalized
            for phrase in [
                "open browser",
                "open chrome",
                "launch browser",
                "launch chrome",
                "opening browser",
                "opening chrome",
            ]
        )

    def _resolve_site(
        self,
        *,
        sites: list[AllowedSite],
        site_name: str | None,
        site_url: str | None,
        utterance: str,
    ) -> AllowedSite | None:
        if site_url:
            target_host = urlparse(site_url).netloc.lower().replace("www.", "")
            for site in sites:
                host = urlparse(site.url).netloc.lower().replace("www.", "")
                if target_host == host:
                    return site

        if site_name:
            by_name = self._match_name(sites, site_name)
            if by_name is not None:
                return by_name

        normalized = utterance.lower()
        hosts_in_text = self._extract_hosts_from_text(normalized)
        if hosts_in_text:
            for site in sites:
                host = urlparse(site.url).netloc.lower().replace("www.", "")
                if host in hosts_in_text:
                    return site
            # If user provided an explicit host and it's not in whitelist, block.
            return None

        for site in sites:
            name = site.name.lower()
            if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", normalized):
                return site

        tokens = re.split(r"\s+", normalized)
        for token in tokens:
            if token in {"open", "go", "to", "navigate", "launch", "browser", "chrome"}:
                continue
            by_name = self._match_name(sites, token)
            if by_name is not None:
                return by_name

        return None

    @staticmethod
    def _extract_hosts_from_text(text: str) -> set[str]:
        candidates = set()
        for match in re.findall(r"https?://[^\s/]+", text):
            host = urlparse(match).netloc.lower().replace("www.", "")
            if host:
                candidates.add(host)

        for token in re.findall(r"\b[a-z0-9.-]+\.[a-z]{2,}\b", text):
            candidates.add(token.lower().strip(".").replace("www.", ""))
        return candidates

    @staticmethod
    def _match_name(sites: list[AllowedSite], candidate: str) -> AllowedSite | None:
        candidate = candidate.strip().lower()
        names = [s.name.lower() for s in sites]
        matched = get_close_matches(candidate, names, n=1, cutoff=0.78)
        if not matched:
            return None
        winner = matched[0]
        for site in sites:
            if site.name.lower() == winner:
                return site
        return None
