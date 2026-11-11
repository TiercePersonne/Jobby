"""Teamtailor Provider for Jobby.

Extracts job offers from tech companies and scaleups hosted on Teamtailor ATS.
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


class TeamtailorProvider(JobSourceProvider):
    name: str = "teamtailor"
    display_name: str = "Teamtailor (ATS & Scaleups)"
    supports_posts: bool = False
    enabled: bool = True

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search Teamtailor jobs across company boards."""
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

                # Search Teamtailor jobs indexed on web
                search_query = f'site:teamtailor.com/jobs "{keywords}" {location}'
                search_url = f"https://www.bing.com/search?q={quote(search_query)}"
                page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)

                cards = page.query_selector_all("li.b_algo, div.b_caption")
                seen_urls = set()

                for card in cards:
                    if len(results) >= params.max_results:
                        break

                    link_el = card.query_selector("h2 a, a[href*='teamtailor.com']")
                    if not link_el:
                        continue
                    url = link_el.get_attribute("href") or ""
                    if not url or url in seen_urls or "teamtailor.com" not in url:
                        continue
                    seen_urls.add(url)

                    title = link_el.inner_text().strip()
                    # Clean title e.g. "Software Engineer - Company - Teamtailor"
                    title = re.sub(r"\s*-\s*Teamtailor.*", "", title, flags=re.I).strip()

                    desc_el = card.query_selector("p, div.b_snippet")
                    snippet = desc_el.inner_text().strip() if desc_el else ""

                    # Extract company name from URL subdomain
                    company_match = re.search(r"https?://([^.]+)\.teamtailor\.com", url)
                    company = company_match.group(1).capitalize() if company_match else "Teamtailor Partner"

                    description = (
                        f"Offre d'emploi Teamtailor : {title}\n"
                        f"Entreprise : {company}\n"
                        f"Localisation : {location}\n\n"
                        f"Descriptif :\n{snippet}\n\n"
                        f"Consultez l'offre sur le portail carrière Teamtailor pour postuler."
                    )

                    opp = JobOpportunity(
                        source=self.name,
                        opportunity_type=OpportunityType.OFFER,
                        title=title,
                        company=company,
                        location=location,
                        url=url,
                        description=description,
                        tags=["teamtailor", "ats", "carriere"],
                    )
                    results.append(opp)

                browser.close()
        except Exception as e:
            logger.warning(f"Error searching Teamtailor: {e}")

        return results
