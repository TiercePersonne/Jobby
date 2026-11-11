"""Jobgether Provider for Jobby.

Extracts remote & flexible tech jobs from Jobgether (jobgether.com).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


class JobgetherProvider(JobSourceProvider):
    name: str = "jobgether"
    display_name: str = "Jobgether (Remote & Tech)"
    supports_posts: bool = False
    enabled: bool = True

    BASE_URL: str = "https://jobgether.com"

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search jobs on Jobgether."""
        keywords_str = " ".join(params.keywords) if params.keywords else "developer"
        search_url = f"{self.BASE_URL}/jobs?keyword={quote(keywords_str)}"

        # Run extraction in worker thread
        return await asyncio.to_thread(self._fetch_sync, search_url, params)

    def _fetch_sync(self, search_url: str, params: SearchParams) -> List[JobOpportunity]:
        results: List[JobOpportunity] = []
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                launch_args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
                try:
                    browser = p.chromium.launch(headless=True, channel="chrome", args=launch_args)
                except Exception:
                    browser = p.chromium.launch(headless=True, args=launch_args)
                page = browser.new_page()
                page.goto(search_url, timeout=25000, wait_until="domcontentloaded")
                page.wait_for_timeout(3500)

                # Find job offer links
                cards = page.query_selector_all('a[href*="/offer/"]')
                seen_urls = set()

                for card in cards:
                    if len(results) >= params.max_results:
                        break

                    rel_href = card.get_attribute("href") or ""
                    full_url = urljoin(self.BASE_URL, rel_href).split("?")[0]
                    if full_url in seen_urls:
                        continue
                    seen_urls.add(full_url)

                    card_text = card.inner_text().strip()
                    lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                    if not lines:
                        continue

                    title = lines[0]
                    company = lines[1] if len(lines) > 1 and "view job" not in lines[1].lower() else "Entreprise Tech"
                    location = "100% Télétravail / Remote"

                    # Description from card lines
                    desc_lines = [l for l in lines[1:] if "view job" not in l.lower()]
                    description = f"Offre Jobgether : {title}\nEntreprise : {company}\nMode : {location}\n\n" + "\n".join(desc_lines)

                    opp = JobOpportunity(
                        source=self.name,
                        opportunity_type=OpportunityType.OFFER,
                        title=title,
                        company=company,
                        location=location,
                        remote_policy="full",
                        url=full_url,
                        description=description,
                        tags=["remote", "jobgether", "tech"],
                    )
                    results.append(opp)

                browser.close()
        except Exception as e:
            logger.warning(f"Error scraping Jobgether: {e}")

        return results
