"""Jobgether Provider for Jobby.

Extracts remote & flexible jobs from Jobgether (jobgether.com/search-offers)
and enriches full descriptions via JSON-LD JobPosting schema.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider
from jobby.providers.relevance import expand_creative_queries, is_relevant_job_offer

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class JobgetherProvider(JobSourceProvider):
    name: str = "jobgether"
    display_name: str = "Jobgether (Remote & Tech)"
    supports_posts: bool = False
    enabled: bool = True

    BASE_URL: str = "https://jobgether.com"

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search jobs on Jobgether via /search-offers SSR and enrich via JSON-LD."""
        if not params.include_offers:
            return []

        queries = expand_creative_queries(params.keywords, max_queries=2)
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        results: List[JobOpportunity] = []
        seen_urls: set[str] = set()

        try:
            async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
                for query in queries:
                    if len(results) >= params.max_results:
                        break

                    try:
                        resp = await client.get(
                            f"{self.BASE_URL}/search-offers",
                            params={"keyword": query},
                        )
                        if resp.status_code != 200:
                            continue

                        parsed = self._parse_search_html(resp.text, params)
                        for opp in parsed:
                            if opp.url in seen_urls:
                                continue
                            seen_urls.add(opp.url)
                            results.append(opp)
                            if len(results) >= params.max_results:
                                break
                    except Exception as q_err:
                        logger.warning(f"Jobgether query failed for '{query}': {q_err}")

                if results:
                    sem = asyncio.Semaphore(6)

                    async def sem_enrich(opp: JobOpportunity):
                        async with sem:
                            await self._enrich_offer(client, opp)
                            await asyncio.sleep(0.05)

                    await asyncio.gather(*(sem_enrich(opp) for opp in results), return_exceptions=True)

        except Exception as e:
            logger.warning(f"Error scraping Jobgether: {e}")

        return results

    def _parse_search_html(self, html_text: str, params: SearchParams) -> List[JobOpportunity]:
        soup = BeautifulSoup(html_text, "html.parser")
        opportunities: List[JobOpportunity] = []
        seen_local: set[str] = set()

        for a in soup.select('a[href*="/offer/"]'):
            href = a.get("href") or ""
            if not href:
                continue
            title = a.get_text(" ", strip=True)
            if not title or "view job" in title.lower():
                continue

            full_url = urljoin(self.BASE_URL, href).split("?")[0]
            if full_url in seen_local:
                continue
            seen_local.add(full_url)

            # Climb up to card container (up to 4 parent levels)
            card = a
            for _ in range(4):
                if card.parent and card.parent.name in ("div", "article", "li"):
                    card = card.parent

            strings = [s.strip() for s in card.stripped_strings if s.strip()]
            company = "Entreprise"
            location = "100% Télétravail / Remote"
            salary_raw: Optional[str] = None
            tags: List[str] = ["Remote", "Jobgether"]

            # Typical sequence: [Title, '1 day ago', Company, 'Remote from', Location, 'Full time', Experience, Salary, ...skills]
            for idx, token in enumerate(strings):
                low = token.lower()
                if idx == 2 and token != title and "remote" not in low and "ago" not in low:
                    company = token
                if low in ("remote from", "hybrid in", "on-site in") and idx + 1 < len(strings):
                    prefix = "Remote" if "remote" in low else ("Hybride" if "hybrid" in low else "Présentiel")
                    location = f"{prefix} ({strings[idx + 1]})"
                if any(curr in token for curr in ("$", "€", "£")) and any(ch.isdigit() for ch in token) and len(token) < 35:
                    salary_raw = token

            if not is_relevant_job_offer(
                title,
                " ".join(strings),
                params.keywords,
                source=self.name,
                company=company,
                location=location,
                url=full_url,
            ):
                continue

            desc_lines = [
                f"🏢 Entreprise : {company}",
                f"📍 Localisation : {location}",
            ]
            if salary_raw:
                desc_lines.append(f"💰 Rémunération : {salary_raw}")

            opp = JobOpportunity(
                source=self.name,
                opportunity_type=OpportunityType.OFFER,
                title=title,
                company=company,
                location=location,
                salary_raw=salary_raw,
                remote_policy="full",
                url=full_url,
                description="\n".join(desc_lines),
                tags=tags,
            )
            opportunities.append(opp)

        return opportunities

    async def _enrich_offer(self, client: httpx.AsyncClient, opp: JobOpportunity) -> None:
        """Fetch full JobPosting JSON-LD from Jobgether offer detail page."""
        try:
            resp = await client.get(opp.url, timeout=10.0)
            if resp.status_code != 200:
                return
            soup = BeautifulSoup(resp.text, "html.parser")
            for s in soup.find_all("script", type="application/ld+json"):
                if not s.string:
                    continue
                try:
                    data: Any = json.loads(s.string)
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("@type") == "JobPosting":
                    desc_html = data.get("description") or ""
                    clean_desc = BeautifulSoup(desc_html, "html.parser").get_text("\n", strip=True)
                    if len(clean_desc) > 80:
                        header = opp.description
                        opp.description = f"{header}\n\n📝 Descriptif complet du poste :\n{clean_desc}"
                    return
        except Exception as e:
            logger.debug(f"Could not enrich Jobgether offer {opp.url}: {e}")
