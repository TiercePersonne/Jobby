"""Glassdoor Provider for Jobby.

Extracts job offers from Glassdoor (glassdoor.fr / glassdoor.com).
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


class GlassdoorProvider(JobSourceProvider):
    name: str = "glassdoor"
    display_name: str = "Glassdoor"
    supports_posts: bool = False
    enabled: bool = True

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search Glassdoor job postings."""
        keywords_str = " ".join(params.keywords) if params.keywords else "developer"
        location_str = params.location or "France"

        return await asyncio.to_thread(self._fetch_sync, keywords_str, location_str, params)

    def _fetch_sync(self, keywords: str, location: str, params: SearchParams) -> List[JobOpportunity]:
        results: List[JobOpportunity] = []
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, channel="chrome", args=["--no-sandbox"])
                page = browser.new_page()

                search_query = f'site:glassdoor.fr/job-listing "{keywords}" {location}'
                search_url = f"https://www.bing.com/search?q={quote(search_query)}"
                page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)

                cards = page.query_selector_all("li.b_algo")
                seen_urls = set()

                for card in cards:
                    if len(results) >= params.max_results:
                        break

                    link_el = card.query_selector("h2 a, a[href*='glassdoor']")
                    if not link_el:
                        continue
                    url = link_el.get_attribute("href") or ""
                    if not url or url in seen_urls or "glassdoor" not in url:
                        continue
                    seen_urls.add(url)

                    raw_title = link_el.inner_text().strip()
                    title = re.sub(r"\s*-\s*Glassdoor.*", "", raw_title, flags=re.I).strip()
                    parts = title.split(" - ")
                    clean_title = parts[0].strip() if parts else raw_title
                    company = parts[1].strip() if len(parts) > 1 else "Entreprise (Glassdoor)"

                    desc_el = card.query_selector("p, div.b_snippet")
                    snippet = desc_el.inner_text().strip() if desc_el else ""

                    description = (
                        f"Offre d'emploi Glassdoor : {clean_title}\n"
                        f"Entreprise : {company}\n"
                        f"Localisation : {location}\n\n"
                        f"Descriptif :\n{snippet}\n\n"
                        f"Consultez l'offre sur Glassdoor pour avis sur l'entreprise, salaires et postuler."
                    )

                    opp = JobOpportunity(
                        source=self.name,
                        opportunity_type=OpportunityType.OFFER,
                        title=clean_title,
                        company=company,
                        location=location,
                        url=url,
                        description=description,
                        tags=["glassdoor", "avis-salaires"],
                    )
                    results.append(opp)

                browser.close()
        except Exception as e:
            logger.warning(f"Error searching Glassdoor: {e}")

        return results
