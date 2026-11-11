"""Welcome to the Jungle (WTTJ) Job Provider for Jobby."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider

logger = logging.getLogger(__name__)


class WTTJProvider(JobSourceProvider):
    name: str = "wttj"
    display_name: str = "Welcome to the Jungle"
    supports_posts: bool = False
    enabled: bool = True

    # Algolia public endpoint used by WTTJ frontend
    ALGOLIA_APP_ID = "WBWNEACJ5A"
    ALGOLIA_API_KEY = "c8969f6ef21f64f43c3a72664d4b1a4a"
    INDEX_NAME = "wttj_jobs_production"

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        if not params.include_offers:
            return []

        keywords_str = " ".join(params.keywords) if params.keywords else "developer"
        url = f"https://{self.ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/{self.INDEX_NAME}/query"
        
        headers = {
            "x-algolia-application-id": self.ALGOLIA_APP_ID,
            "x-algolia-api-key": self.ALGOLIA_API_KEY,
            "Content-Type": "application/json",
        }

        # Build Algolia search query
        query_body: Dict[str, Any] = {
            "query": keywords_str,
            "hitsPerPage": min(params.max_results, 50),
        }

        filters = []
        if params.remote_only:
            filters.append("remote:fulltime")
        if filters:
            query_body["filters"] = " AND ".join(filters)

        results: List[JobOpportunity] = []
        try:
            async with httpx.AsyncClient(headers=headers, timeout=12.0) as client:
                resp = await client.post(url, json=query_body)
                if resp.status_code == 200:
                    data = resp.json()
                    hits = data.get("hits", [])
                    results = self._parse_hits(hits)
                else:
                    logger.warning(f"WTTJ Algolia search failed with status {resp.status_code}")
        except Exception as e:
            logger.error(f"Error querying WTTJ Algolia API: {e}")

        return results

    def _parse_hits(self, hits: List[Dict[str, Any]]) -> List[JobOpportunity]:
        results: List[JobOpportunity] = []
        for hit in hits:
            name = hit.get("name") or hit.get("title") or "Poste"
            organization = hit.get("organization", {})
            company = organization.get("name") if isinstance(organization, dict) else None
            
            slug = hit.get("slug", "")
            org_slug = organization.get("slug", "") if isinstance(organization, dict) else ""
            job_url = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{slug}" if org_slug and slug else "https://www.welcometothejungle.com"

            offices = hit.get("offices", [])
            location = None
            if offices and isinstance(offices, list):
                first_office = offices[0]
                if isinstance(first_office, dict):
                    location = f"{first_office.get('city', '')}, {first_office.get('country', '')}".strip(", ")

            salary_min = hit.get("salary_minimum")
            salary_max = hit.get("salary_maximum")
            salary_currency = hit.get("salary_currency", "€")
            salary_raw = None
            if salary_min and salary_max:
                salary_raw = f"{salary_min}k - {salary_max}k {salary_currency}"

            remote = hit.get("remote")
            remote_policy = "full" if remote == "fulltime" else ("hybrid" if remote == "partial" else None)

            tags: List[str] = []
            contract_type = hit.get("contract_type")
            if contract_type:
                tags.append(str(contract_type).upper())
            skills = hit.get("skills", [])
            if isinstance(skills, list):
                tags.extend([s.get("name") for s in skills if isinstance(s, dict) and "name" in s][:5])

            opp = JobOpportunity(
                source=self.name,
                opportunity_type=OpportunityType.OFFER,
                title=name,
                company=company,
                location=location,
                remote_policy=remote_policy,
                salary_raw=salary_raw,
                url=job_url,
                tags=tags,
                description=hit.get("summary") or hit.get("profile") or "",
            )
            results.append(opp)
        return results
