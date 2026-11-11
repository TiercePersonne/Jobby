"""HelloWork Job Provider for Jobby."""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, List
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


class HelloWorkProvider(JobSourceProvider):
    name: str = "hellowork"
    display_name: str = "HelloWork"
    supports_posts: bool = False
    enabled: bool = True

    BASE_URL = "https://www.hellowork.com"
    SEARCH_PATH = "/fr-fr/emploi/recherche.html"

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        if not params.include_offers:
            return []

        keywords_str = " ".join(params.keywords) if params.keywords else "developpeur"
        location_str = params.location or "France"

        query_params: Dict[str, Any] = {
            "k": keywords_str,
            "l": location_str,
        }

        if params.remote_only:
            query_params["tf"] = "full"

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        url = f"{self.BASE_URL}{self.SEARCH_PATH}"
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url, params=query_params)
                if resp.status_code != 200:
                    logger.warning(f"HelloWork search returned status {resp.status_code}")
                    return []

                return self._parse_html(resp.text, limit=params.max_results)

        except Exception as e:
            logger.error(f"HelloWork query failed: {e}")
            return []

    def _parse_html(self, html_text: str, limit: int = 25) -> List[JobOpportunity]:
        soup = BeautifulSoup(html_text, "html.parser")
        results: List[JobOpportunity] = []
        seen_urls = set()

        # Find all h3 headings that contain job links
        h3_elements = soup.find_all("h3")

        for h3 in h3_elements:
            if len(results) >= limit:
                break

            card = h3.find_parent(["li", "article", "div"])
            if not card:
                continue

            link_elem = card.find("a", href=re.compile(r"/fr-fr/emplois/|\.html"))
            if not link_elem or not link_elem.get("href"):
                continue

            rel_href = link_elem["href"]
            job_url = urljoin(self.BASE_URL, rel_href)
            if job_url in seen_urls:
                continue
            seen_urls.add(job_url)

            raw_title = h3.get_text(strip=True)
            title = raw_title.split("\n")[0].strip()

            # Company name extraction
            company = None
            company_elem = card.find(class_=re.compile(r"company|recruiter", re.I))
            if company_elem:
                company = company_elem.get_text(strip=True)
            else:
                all_text = [t.strip() for t in card.stripped_strings if len(t.strip()) > 1]
                if len(all_text) >= 2:
                    candidate = all_text[1]
                    if "favoris" not in candidate.lower() and "candidature" not in candidate.lower():
                        company = candidate

            location = None
            loc_elem = card.find(class_=re.compile(r"location|city|town", re.I))
            if loc_elem:
                location = loc_elem.get_text(strip=True)

            tags: List[str] = []
            card_text_lower = card.get_text().lower()
            if "cdi" in card_text_lower:
                tags.append("CDI")
            if "télétravail" in card_text_lower or "remote" in card_text_lower:
                tags.append("Remote")

            opp = JobOpportunity(
                source=self.name,
                opportunity_type=OpportunityType.OFFER,
                title=title,
                company=company,
                location=location,
                url=job_url,
                tags=tags,
                description=card.get_text(" | ", strip=True)[:300],
            )
            results.append(opp)

        return results
