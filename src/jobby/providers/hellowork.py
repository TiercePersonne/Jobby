"""HelloWork Job Provider for Jobby."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider
from jobby.providers.relevance import (
    expand_creative_queries,
    is_location_compatible,
    is_relevant_job_offer,
)

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

        queries = expand_creative_queries(params.keywords, max_queries=4)
        location_str = params.location or "France"

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        url = f"{self.BASE_URL}{self.SEARCH_PATH}"
        results: List[JobOpportunity] = []
        seen_keys: set[str] = set()

        try:
            async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
                for query in queries:
                    if len(results) >= params.max_results:
                        break

                    query_params: Dict[str, Any] = {
                        "k": query,
                        "l": location_str,
                    }
                    if params.remote_only:
                        query_params["tf"] = "full"

                    try:
                        resp = await client.get(url, params=query_params)
                        if resp.status_code != 200:
                            logger.warning(f"HelloWork search returned status {resp.status_code} for '{query}'")
                            continue

                        parsed = self._parse_html(resp.text, limit=params.max_results * 2)
                        for opp in parsed:
                            if not is_relevant_job_offer(opp.title, opp.description, params.keywords):
                                continue
                            if not is_location_compatible(opp.location, opp.remote_policy, location_str):
                                continue

                            dedup_sig = f"{(opp.company or '').strip().lower()}::{opp.title.strip().lower()}"
                            if opp.url in seen_keys or dedup_sig in seen_keys:
                                continue
                            seen_keys.add(opp.url)
                            seen_keys.add(dedup_sig)
                            results.append(opp)
                            if len(results) >= params.max_results:
                                break
                    except Exception as q_err:
                        logger.warning(f"HelloWork query error for '{query}': {q_err}")

                # Concurrently enrich job descriptions from public job pages
                if results:
                    sem = asyncio.Semaphore(5)

                    async def sem_enrich(opp: JobOpportunity):
                        async with sem:
                            await self._enrich_job_description(client, opp)
                            await asyncio.sleep(0.08)

                    await asyncio.gather(*(sem_enrich(opp) for opp in results), return_exceptions=True)

                return results

        except Exception as e:
            logger.error(f"HelloWork query failed: {e}")
            return []

    def _parse_html(self, html_text: str, limit: int = 25) -> List[JobOpportunity]:
        soup = BeautifulSoup(html_text, "html.parser")
        results: List[JobOpportunity] = []
        seen_urls = set()

        h3_elements = soup.find_all("h3")

        for h3 in h3_elements:
            if len(results) >= limit:
                break

            card = h3.find_parent(["li", "article"]) or h3.find_parent("div")
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

            # Extract clean title and company from h3 spans (modern HelloWork markup) or fallback
            h3_spans = h3.find_all("span")
            company: Optional[str] = None
            if h3_spans:
                title = h3_spans[0].get_text(strip=True)
                if len(h3_spans) >= 2:
                    company = h3_spans[1].get_text(strip=True)
            else:
                raw_title = h3.get_text(" ", strip=True)
                title = raw_title.split("\n")[0].strip()

            if not company:
                company_elem = card.find(class_=re.compile(r"company|recruiter", re.I))
                if company_elem:
                    company = company_elem.get_text(strip=True)
                else:
                    all_text = [t.strip() for t in card.stripped_strings if len(t.strip()) > 1]
                    for candidate in all_text:
                        if (
                            candidate != title
                            and "favoris" not in candidate.lower()
                            and "candidature" not in candidate.lower()
                            and "voir l" not in candidate.lower()
                        ):
                            company = candidate
                            break

            # Location extraction: prioritize explicit data-cy="localisationCard"
            location: Optional[str] = None
            loc_cy = card.find(attrs={"data-cy": "localisationCard"})
            if loc_cy:
                location = loc_cy.get_text(strip=True)
            else:
                loc_elem = card.find(class_=re.compile(r"location|city|town", re.I))
                if loc_elem:
                    location = loc_elem.get_text(strip=True)
                else:
                    for span in card.find_all(["span", "p", "div"]):
                        stext = span.get_text(strip=True)
                        if stext == title or stext == company:
                            continue
                        if re.search(
                            r"\b(75\d{0,3}|92\d{0,3}|93\d{0,3}|94\d{0,3}|69\d{0,3}|13\d{0,3}|33\d{0,3}|44\d{0,3}|31\d{0,3}|59\d{0,3}|\d{2}\s\d{3})\b",
                            stext,
                        ) or any(
                            city in stext.lower()
                            for city in ["paris", "lyon", "bordeaux", "marseille", "nantes", "toulouse", "lille", "remote", "télétravail"]
                        ):
                            if len(stext) < 40 and not any(
                                w in stext.lower()
                                for w in ["candidature", "publiée", "sauvegarder", "postuler", "salaire", "offre", "favoris"]
                            ):
                                location = stext
                                break

            # Contract & Salary extraction
            tags: List[str] = []
            contract_cy = card.find(attrs={"data-cy": "contractCard"})
            if contract_cy and contract_cy.get_text(strip=True):
                tags.append(contract_cy.get_text(strip=True))

            card_text_lower = card.get_text(" ", strip=True).lower()
            if "cdi" in card_text_lower and "CDI" not in tags:
                tags.append("CDI")
            if ("télétravail" in card_text_lower or "remote" in card_text_lower) and "Remote" not in tags:
                tags.append("Remote")

            salary_raw: Optional[str] = None
            for s_str in card.stripped_strings:
                if "€" in s_str and len(s_str) < 45:
                    salary_raw = s_str.replace("\u202f", " ").strip()
                    break

            remote_policy = None
            if "télétravail complet" in card_text_lower or "100% télétravail" in card_text_lower:
                remote_policy = "full"
            elif "télétravail" in card_text_lower or "remote" in card_text_lower:
                remote_policy = "hybrid"

            desc_lines = [
                f"🏢 Entreprise : {company or 'Entreprise'}",
                f"📍 Localisation : {location or 'France'}",
            ]
            if tags:
                desc_lines.append(f"📄 Contrat : {', '.join(tags)}")
            if salary_raw:
                desc_lines.append(f"💰 Rémunération : {salary_raw}")

            description = "\n".join(desc_lines)

            opp = JobOpportunity(
                source=self.name,
                opportunity_type=OpportunityType.OFFER,
                title=title,
                company=company,
                location=location,
                salary_raw=salary_raw,
                remote_policy=remote_policy,
                url=job_url,
                tags=tags,
                description=description,
            )
            results.append(opp)

        return results

    async def _enrich_job_description(self, client: httpx.AsyncClient, opp: JobOpportunity) -> None:
        """Fetch full job posting details from HelloWork public job page."""
        if not opp.url or not opp.url.startswith("http"):
            return
        try:
            resp = await client.get(opp.url, timeout=10.0)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                # 1. Try JSON-LD schema
                for s in soup.find_all("script", type="application/ld+json"):
                    if not s.string:
                        continue
                    try:
                        data = json.loads(s.string)
                        if isinstance(data, dict) and data.get("description"):
                            raw_html = data["description"]
                            clean_text = BeautifulSoup(raw_html, "html.parser").get_text("\n", strip=True)
                            if len(clean_text) > 80:
                                opp.description = f"📝 Descriptif complet du poste :\n{clean_text}"
                                return
                    except Exception:
                        pass

                # 2. Try main section HTML
                for sel in ["section.peer", "div[data-cy='job-description']", "section[class*='border-b-grey']"]:
                    main_el = soup.select_one(sel)
                    if main_el:
                        clean_text = main_el.get_text("\n", strip=True)
                        if len(clean_text) > 80:
                            opp.description = f"📝 Descriptif complet du poste :\n{clean_text}"
                            return
        except Exception as e:
            logger.debug(f"Could not enrich HelloWork job {opp.url}: {e}")
