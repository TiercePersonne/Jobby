"""Welcome to the Jungle (WTTJ) Job Provider for Jobby."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from jobby.config import load_config
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


class WTTJProvider(JobSourceProvider):
    name: str = "wttj"
    display_name: str = "Welcome to the Jungle"
    supports_posts: bool = False
    enabled: bool = True

    API_BASE = "https://api.welcometothejungle.com/api/v3/public/jobs"

    def __init__(self, session_cookie: Optional[str] = None, auth_token: Optional[str] = None):
        self.session_cookie = session_cookie
        self.auth_token = auth_token

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        if not params.include_offers:
            return []

        queries = expand_creative_queries(params.keywords, max_queries=5)

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
            "Referer": "https://www.welcometothejungle.com/",
            "Origin": "https://www.welcometothejungle.com",
        }

        cfg = load_config()
        auth_token = self.auth_token or cfg.wttj_auth_token
        session_cookie = self.session_cookie or cfg.wttj_session_cookie

        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        if session_cookie:
            headers["Cookie"] = session_cookie

        results: List[JobOpportunity] = []
        seen_keys: set[str] = set()
        max_to_fetch = max(params.max_results, 80)
        max_to_fetch = min(max_to_fetch, 150)

        max_pages_per_query = 12 if len(queries) == 1 else 6

        async with httpx.AsyncClient(headers=headers, timeout=12.0) as client:
            for query in queries:
                if len(results) >= max_to_fetch:
                    break
                page = 1
                while len(results) < max_to_fetch and page <= max_pages_per_query:
                    query_params: Dict[str, Any] = {
                        "job_title": query,
                        "page": page,
                    }

                    try:
                        resp = await client.get(self.API_BASE, params=query_params)
                        if resp.status_code != 200:
                            logger.warning(
                                f"WTTJ API responded with status {resp.status_code} for query '{query}' at page {page}"
                            )
                            break

                        data = resp.json()
                        jobs_data = data.get("data", [])
                        if not jobs_data:
                            break

                        parsed_page = self._parse_jobs(jobs_data)
                        for opp in parsed_page:
                            if params.remote_only and opp.remote_policy != "full":
                                continue
                            if not is_relevant_job_offer(
                                opp.title,
                                opp.description,
                                params.keywords,
                                source=self.name,
                                company=opp.company,
                                location=opp.location,
                                url=opp.url,
                            ):
                                continue
                            if not is_location_compatible(
                                opp.location,
                                opp.remote_policy,
                                params.location,
                                source=self.name,
                                title=opp.title,
                                company=opp.company,
                                url=opp.url,
                                description=opp.description,
                            ):
                                continue

                            dedup_ref = (opp.raw_metadata or {}).get("wttj_reference") or opp.url
                            if dedup_ref in seen_keys:
                                continue
                            seen_keys.add(dedup_ref)
                            results.append(opp)
                            if len(results) >= max_to_fetch:
                                break

                        metadata = data.get("metadata", {})
                        page_count = metadata.get("page_count", 1)
                        if page >= page_count:
                            break

                        page += 1
                        await asyncio.sleep(0.06)

                    except Exception as e:
                        logger.error(f"Error querying WTTJ API for '{query}' page {page}: {e}")
                        break

            # Concurrently enrich job descriptions via WTTJ v1 public API
            if results:
                sem = asyncio.Semaphore(10)

                async def sem_enrich(opp: JobOpportunity):
                    meta = opp.raw_metadata or {}
                    org_slug = meta.get("org_slug")
                    job_slug = meta.get("job_slug")
                    if org_slug and job_slug:
                        async with sem:
                            await self._enrich_job_description(client, opp, org_slug, job_slug)
                            await asyncio.sleep(0.04)

                await asyncio.gather(*(sem_enrich(opp) for opp in results), return_exceptions=True)

        return results

    def _parse_jobs(self, raw_jobs: List[Dict[str, Any]]) -> List[JobOpportunity]:
        opportunities: List[JobOpportunity] = []

        for job in raw_jobs:
            title = job.get("name") or "Poste"
            
            organization = job.get("organization") or {}
            company = organization.get("name") if isinstance(organization, dict) else None
            org_slug = organization.get("slug") if isinstance(organization, dict) else ""
            job_slug = job.get("slug", "")

            # Construct canonical job page link
            if org_slug and job_slug:
                job_url = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{job_slug}"
            else:
                job_url = "https://www.welcometothejungle.com/fr/jobs"

            # Location extraction
            office = job.get("office") or {}
            city = office.get("city") if isinstance(office, dict) else None
            country = office.get("country") if isinstance(office, dict) else None
            if not city and job.get("offices") and isinstance(job["offices"], list) and len(job["offices"]) > 0:
                first_office = job["offices"][0]
                if isinstance(first_office, dict):
                    city = first_office.get("city")
                    country = first_office.get("country")
            loc_parts = [p for p in [city, country] if p]
            location = ", ".join(loc_parts) if loc_parts else None

            # Remote extraction
            remote_val = job.get("remote")
            remote_policy = "full" if remote_val == "fulltime" else ("hybrid" if remote_val in ["partial", "punctual"] else None)

            # Salary extraction
            salary_min = job.get("salary_min")
            salary_max = job.get("salary_max")
            salary_curr = job.get("salary_currency", "€")
            salary_period = job.get("salary_period", "an")
            salary_raw = None
            if salary_min or salary_max:
                if salary_min and salary_max:
                    salary_raw = f"{int(salary_min)} - {int(salary_max)} {salary_curr}/{salary_period}"
                elif salary_min:
                    salary_raw = f"Dès {int(salary_min)} {salary_curr}/{salary_period}"
                elif salary_max:
                    salary_raw = f"Jusqu'à {int(salary_max)} {salary_curr}/{salary_period}"

            # Publication time
            published_at = None
            pub_str = job.get("published_at")
            if pub_str:
                try:
                    published_at = date_parser.parse(pub_str)
                except Exception:
                    published_at = None

            # Tags & Sectors
            tags: List[str] = []
            contract_type = job.get("contract_type")
            if contract_type:
                tags.append(str(contract_type).upper())
            if remote_policy == "full":
                tags.append("Full Remote")

            sectors = organization.get("sectors", []) if isinstance(organization, dict) else []
            for s in sectors:
                if isinstance(s, dict) and "name" in s:
                    tags.append(s["name"])

            desc_lines = []
            if job.get("company_summary"):
                desc_lines.append(f"🏢 À propos de l'entreprise :\n{job.get('company_summary')}")
            if organization.get("industry"):
                desc_lines.append(f"📌 Secteur d'activité : {organization.get('industry')}")
            if contract_type:
                desc_lines.append(f"📄 Type de contrat : {str(contract_type).upper()}")
            if remote_policy:
                desc_lines.append(f"🏠 Mode de travail : {'100% Télétravail' if remote_policy == 'full' else 'Hybride / Présentiel'}")
            if salary_raw:
                desc_lines.append(f"💰 Rémunération indicative : {salary_raw}")
            if job.get("experience_min") is not None or job.get("experience_max") is not None:
                exp_min = job.get("experience_min", 0)
                exp_max = job.get("experience_max", "")
                desc_lines.append(f"🎓 Expérience : {exp_min} à {exp_max} an(s)")

            description = "\n\n".join(desc_lines) if desc_lines else (job.get("company_summary") or "")

            opp = JobOpportunity(
                source=self.name,
                opportunity_type=OpportunityType.OFFER,
                title=title,
                company=company,
                location=location,
                remote_policy=remote_policy,
                salary_raw=salary_raw,
                url=job_url,
                published_at=published_at,
                tags=tags[:5],
                description=description,
                raw_metadata={
                    "wttj_reference": job.get("reference"),
                    "is_bookmarked": job.get("is_bookmarked", False),
                    "org_slug": org_slug,
                    "job_slug": job_slug,
                }
            )
            opportunities.append(opp)

        return opportunities

    async def _enrich_job_description(
        self, client: httpx.AsyncClient, opp: JobOpportunity, org_slug: str, job_slug: str
    ) -> None:
        """Fetch full job posting details from WTTJ v1 public API."""
        url = f"https://api.welcometothejungle.com/api/v1/organizations/{org_slug}/jobs/{job_slug}"
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                job_data = resp.json().get("job", {})
                if not opp.location:
                    office = job_data.get("office") or {}
                    city = office.get("city") if isinstance(office, dict) else None
                    country = office.get("country") if isinstance(office, dict) else None
                    if not city and isinstance(job_data.get("offices"), list) and job_data["offices"]:
                        first_off = job_data["offices"][0]
                        if isinstance(first_off, dict):
                            city = first_off.get("city")
                            country = first_off.get("country")
                    loc_parts = [p for p in [city, country] if p]
                    if loc_parts:
                        opp.location = ", ".join(loc_parts)
                sections: List[str] = []

                # Company presentation
                comp_desc = job_data.get("company_description")
                if comp_desc:
                    clean_comp = BeautifulSoup(comp_desc, "html.parser").get_text("\n", strip=True)
                    if clean_comp:
                        sections.append(f"🏢 À propos de l'entreprise :\n{clean_comp}")

                # Job mission & description
                desc_html = job_data.get("description")
                if desc_html:
                    clean_desc = BeautifulSoup(desc_html, "html.parser").get_text("\n", strip=True)
                    if clean_desc:
                        sections.append(f"📝 Descriptif du poste & Missions :\n{clean_desc}")

                # Profile requirements
                profile_html = job_data.get("profile")
                if profile_html:
                    clean_profile = BeautifulSoup(profile_html, "html.parser").get_text("\n", strip=True)
                    if clean_profile:
                        sections.append(f"🎯 Profil recherché :\n{clean_profile}")

                # Recruitment process
                process_html = job_data.get("recruitment_process")
                if process_html:
                    clean_process = BeautifulSoup(process_html, "html.parser").get_text("\n", strip=True)
                    if clean_process:
                        sections.append(f"🚀 Déroulement des entretiens :\n{clean_process}")

                if sections:
                    opp.description = "\n\n".join(sections)
        except Exception as e:
            logger.debug(f"Could not enrich WTTJ job {org_slug}/{job_slug}: {e}")
