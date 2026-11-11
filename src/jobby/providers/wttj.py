"""Welcome to the Jungle (WTTJ) Job Provider for Jobby."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional

import httpx
from dateutil import parser as date_parser

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider

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

        search_query = " ".join(params.keywords) if params.keywords else "developer"

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
            "Referer": "https://www.welcometothejungle.com/",
            "Origin": "https://www.welcometothejungle.com",
        }

        # If user connected their WTTJ account
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if self.session_cookie:
            headers["Cookie"] = self.session_cookie

        results: List[JobOpportunity] = []
        max_to_fetch = min(params.max_results, 60)
        page = 1

        async with httpx.AsyncClient(headers=headers, timeout=12.0) as client:
            while len(results) < max_to_fetch:
                query_params: Dict[str, Any] = {
                    "job_title": search_query,
                    "page": page,
                }

                try:
                    resp = await client.get(self.API_BASE, params=query_params)
                    if resp.status_code != 200:
                        logger.warning(f"WTTJ API responded with status {resp.status_code} at page {page}")
                        break

                    data = resp.json()
                    jobs_data = data.get("data", [])
                    if not jobs_data:
                        break

                    parsed_page = self._parse_jobs(jobs_data)
                    for opp in parsed_page:
                        # Optional client-side remote filter if needed
                        if params.remote_only and opp.remote_policy != "full":
                            continue
                        results.append(opp)
                        if len(results) >= max_to_fetch:
                            break

                    metadata = data.get("metadata", {})
                    page_count = metadata.get("page_count", 1)
                    if page >= page_count:
                        break

                    page += 1
                    await asyncio.sleep(random.uniform(0.3, 0.7))

                except Exception as e:
                    logger.error(f"Error querying WTTJ API page {page}: {e}")
                    break

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
            location = office.get("city") if isinstance(office, dict) else None

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
                }
            )
            opportunities.append(opp)

        return opportunities
