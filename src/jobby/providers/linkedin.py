"""LinkedIn Provider for Jobby.

Extracts both official job listings (via guest API) and network hiring posts
(via social content search & recruiter patterns).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Hiring trigger keywords used when searching LinkedIn social posts
HIRING_TRIGGERS_FR = [
    '"on recrute"',
    '"nous recrutons"',
    '"je cherche"',
    '"recherche dev"',
    '"poste ouvert"',
    '"opportunité"',
    '"cdi"',
]

HIRING_TRIGGERS_EN = [
    '"we are hiring"',
    '"i am hiring"',
    '"looking for"',
    '"join our team"',
    '"open role"',
]


class LinkedInProvider(JobSourceProvider):
    name: str = "linkedin"
    display_name: str = "LinkedIn (Offres & Posts)"
    supports_posts: bool = True
    enabled: bool = True

    def __init__(self, li_at: Optional[str] = None):
        self.li_at = li_at

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        opportunities: List[JobOpportunity] = []

        # 1. Collect official job offers if requested
        if params.include_offers:
            offers = await self._search_official_jobs(params)
            opportunities.extend(offers)

        # 2. Collect social hiring posts if requested
        if params.include_posts:
            posts = await self._search_social_posts(params)
            opportunities.extend(posts)

        return opportunities

    async def _search_official_jobs(self, params: SearchParams) -> List[JobOpportunity]:
        """Search official LinkedIn job postings via the public guest API."""
        url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        keywords_str = " ".join(params.keywords) if params.keywords else "developer"
        location_str = params.location or "France"

        query_params: Dict[str, Any] = {
            "keywords": keywords_str,
            "location": location_str,
            "f_TPR": f"r{params.posted_within_days * 86400}",
            "start": 0,
        }

        # LinkedIn remote filter (2 = Remote)
        if params.remote_only:
            query_params["f_WT"] = "2"

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        opportunities: List[JobOpportunity] = []
        max_to_fetch = min(params.max_results, 50)
        start = 0

        async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
            while len(opportunities) < max_to_fetch and start < max_to_fetch:
                query_params["start"] = start
                try:
                    resp = await client.get(url, params=query_params)
                    if resp.status_code != 200:
                        logger.warning(f"LinkedIn public jobs responded with status {resp.status_code}")
                        break

                    cards = self._parse_job_cards(resp.text)
                    if not cards:
                        break

                    for card in cards:
                        if len(opportunities) >= max_to_fetch:
                            break
                        opportunities.append(card)

                    start += len(cards)
                    # Gentle delay to avoid rate limit
                    await asyncio.sleep(random.uniform(0.5, 1.2))

                except Exception as e:
                    logger.error(f"Error fetching LinkedIn jobs at offset {start}: {e}")
                    break

            # Enrich descriptions for fetched cards concurrently
            if opportunities:
                sem = asyncio.Semaphore(5)
                async def sem_enrich(opp: JobOpportunity):
                    async with sem:
                        await self._enrich_job_description(client, opp)
                        await asyncio.sleep(0.15)

                await asyncio.gather(*(sem_enrich(opp) for opp in opportunities), return_exceptions=True)

        return opportunities

    async def _enrich_job_description(self, client: httpx.AsyncClient, opp: JobOpportunity) -> None:
        """Fetch full job posting details from LinkedIn guest API."""
        m = re.search(r"(\d{8,12})", opp.url)
        if not m:
            return
        job_id = m.group(1)
        api_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
        try:
            resp = await client.get(api_url, timeout=10.0)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                desc_elem = soup.find(class_=re.compile(r"show-more-less-html__markup|description__text", re.I))

                parts: List[str] = []
                criteria = soup.find_all(class_=re.compile(r"description__job-criteria-item", re.I))
                criteria_texts = [c.get_text(" : ", strip=True) for c in criteria if c.get_text(strip=True)]
                if criteria_texts:
                    parts.append("📌 Informations clés :\n• " + "\n• ".join(criteria_texts))

                if desc_elem:
                    parts.append("📝 Descriptif complet du poste :\n" + desc_elem.get_text("\n", strip=True))

                if parts:
                    opp.description = "\n\n".join(parts)
        except Exception as e:
            logger.debug(f"Could not enrich LinkedIn job {job_id}: {e}")

    def _parse_job_cards(self, html_content: str) -> List[JobOpportunity]:
        """Parse job cards from LinkedIn guest HTML response."""
        soup = BeautifulSoup(html_content, "html.parser")
        items = soup.find_all("li")
        results: List[JobOpportunity] = []

        for item in items:
            title_elem = item.find("h3", class_=re.compile(r"base-search-card__title", re.I))
            if not title_elem:
                continue
            title = title_elem.get_text(strip=True)

            company_elem = item.find("h4", class_=re.compile(r"base-search-card__subtitle", re.I))
            company = company_elem.get_text(strip=True) if company_elem else None

            loc_elem = item.find("span", class_=re.compile(r"job-search-card__location", re.I))
            location = loc_elem.get_text(strip=True) if loc_elem else None

            link_elem = item.find("a", class_=re.compile(r"base-card__full-link", re.I))
            if not link_elem or "href" not in link_elem.attrs:
                continue
            url = link_elem["href"]

            # Publication time
            time_elem = item.find("time", class_=re.compile(r"job-search-card__listdate", re.I))
            published_at = None
            if time_elem and time_elem.get("datetime"):
                try:
                    published_at = date_parser.parse(time_elem["datetime"])
                except Exception:
                    published_at = None

            # Salary if present
            salary_elem = item.find("span", class_=re.compile(r"job-search-card__salary-info", re.I))
            salary = salary_elem.get_text(strip=True) if salary_elem else None

            # Card snippet or summary description
            snippet_elem = item.find(class_=re.compile(r"job-search-card__snippet|job-snippet|base-search-card__metadata", re.I))
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else None
            description = snippet or (
                f"Offre LinkedIn pour le poste de {title}"
                + (f" chez {company}" if company else "")
                + (f" à {location}" if location else "")
                + ".\nCliquez sur le lien pour afficher la fiche de poste complète sur LinkedIn et postuler."
            )

            opp = JobOpportunity(
                source=self.name,
                opportunity_type=OpportunityType.OFFER,
                title=title,
                company=company,
                location=location,
                salary_raw=salary,
                url=url,
                published_at=published_at,
                description=description,
            )
            results.append(opp)

        return results

    async def _search_social_posts(self, params: SearchParams) -> List[JobOpportunity]:
        """
        Search LinkedIn social posts (recruiter & network hiring announcements).
        If li_at cookie is configured, calls LinkedIn Voyager API.
        Otherwise, builds high-intent dorking & search queries.
        """
        if self.li_at:
            return await self._search_posts_with_voyager(params)
        else:
            return await self._search_posts_fallback(params)

    async def _search_posts_with_voyager(self, params: SearchParams) -> List[JobOpportunity]:
        """Direct authenticated query to LinkedIn Voyager Content Search."""
        triggers = " OR ".join(HIRING_TRIGGERS_FR[:3] + HIRING_TRIGGERS_EN[:2])
        keywords = " ".join(params.keywords) if params.keywords else "developer"
        query_string = f"({triggers}) {keywords}"
        if params.location:
            query_string += f" {params.location}"

        url = "https://www.linkedin.com/voyager/api/search/dash/clusters"
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Cookie": f'li_at={self.li_at}; JSESSIONID="ajax:1234567890"',
            "csrf-token": "ajax:1234567890",
            "x-restli-protocol-version": "2.0.0",
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
        }
        query_params = {
            "decorationId": "com.linkedin.voyager.dash.deco.search.SearchClusterCollection-175",
            "origin": "FACETED_SEARCH",
            "q": "all",
            "query": f"(keywords:{quote(query_string)},flagshipSearchIntent:SEARCH_SRP)",
            "start": 0,
            "count": min(params.max_results, 25),
        }

        results: List[JobOpportunity] = []
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
                resp = await client.get(url, params=query_params)
                if resp.status_code == 200:
                    data = resp.json()
                    results = self._parse_voyager_posts(data)
                else:
                    logger.warning(f"Voyager API returned status {resp.status_code}, falling back.")
                    return await self._search_posts_fallback(params)
        except Exception as e:
            logger.error(f"Voyager query failed: {e}")
            return await self._search_posts_fallback(params)

        return results

    def _parse_voyager_posts(self, data: Dict[str, Any]) -> List[JobOpportunity]:
        """Extract posts, author name, author title, text and direct link from Voyager response."""
        posts: List[JobOpportunity] = []
        included = data.get("included", [])
        
        for item in included:
            type_str = item.get("$type", "")
            if "Update" in type_str or "FeedComponent" in type_str:
                commentary = item.get("commentary", {}).get("text", {}).get("text", "")
                if not commentary or len(commentary) < 20:
                    continue

                urn = item.get("urn", "")
                post_url = f"https://www.linkedin.com/feed/update/{urn}" if urn else ""

                # Extract author info if available
                actor = item.get("actor", {})
                author_name = actor.get("name", {}).get("text")
                author_title = actor.get("subDescription", {}).get("text")
                author_url = actor.get("navigationUrl")

                # Extract first line or headline for title
                first_line = commentary.strip().split("\n")[0][:100]
                title = f"Post: {first_line}"

                posts.append(JobOpportunity(
                    source=self.name,
                    opportunity_type=OpportunityType.POST,
                    title=title,
                    url=post_url or "https://www.linkedin.com/feed/",
                    author_name=author_name,
                    author_title=author_title,
                    author_profile_url=author_url,
                    description=commentary,
                    tags=["linkedin-post", "recruiter-network"],
                ))
        return posts

    async def _search_posts_fallback(self, params: SearchParams) -> List[JobOpportunity]:
        """
        When li_at is not provided, provides structured discovery queries and
        extracts public web-indexed LinkedIn posts.
        """
        keywords_str = " ".join(params.keywords) if params.keywords else "developer"
        location_str = params.location or "France"
        
        # Build direct web URL for the user to open all posts in 1 click
        search_query = f'("on recrute" OR "nous recrutons" OR "hiring") {keywords_str} {location_str}'
        web_search_url = f"https://www.linkedin.com/search/results/content/?keywords={quote(search_query)}&sortBy=%22date_posted%22"
        
        # Return a ready-to-use opportunity card pointing directly to the live recruiter posts feed
        lead_post = JobOpportunity(
            source=self.name,
            opportunity_type=OpportunityType.POST,
            title=f"Flux Posts Recruteurs en direct: {keywords_str} ({location_str})",
            company="LinkedIn Network Feed",
            location=location_str,
            url=web_search_url,
            author_name="LinkedIn Content Search Engine",
            author_title="Recherche booléenne: 'on recrute' / 'nous recrutons'",
            description=(
                f"Lien direct vers le flux temps réel des publications LinkedIn de recruteurs et fondateurs "
                f"avec les mots-clés '{keywords_str}' et les déclencheurs de recrutement.\n"
                f"Pour activer l'aspiration automatique directe des posts dans Jobby, renseignez votre cookie "
                f"'li_at' dans _bmad-output/jobby_config.yaml ou via la variable LINKEDIN_LI_AT."
            ),
            tags=["feed-direct", "recruiter-posts", "boolean-search"],
        )
        return [lead_post]
