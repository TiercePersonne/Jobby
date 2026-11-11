"""JobbyGather & Generic Feed Aggregator Provider for Jobby."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

import httpx

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider

logger = logging.getLogger(__name__)


class JobbyGatherProvider(JobSourceProvider):
    """
    Extensible provider that ingests standardized job JSON payloads
    from local feeds, webhooks, or aggregator APIs (like JobbyGather).
    """

    name: str = "jobbygather"
    display_name: str = "JobbyGather & Feed Aggregator"
    supports_posts: bool = True
    enabled: bool = True

    def __init__(self, endpoint_url: Optional[str] = None, local_feed_path: Optional[str] = None):
        self.endpoint_url = endpoint_url
        self.local_feed_path = local_feed_path

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        results: List[JobOpportunity] = []

        # 1. Ingest local feed file if present
        if self.local_feed_path:
            path = Path(self.local_feed_path)
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        results.extend(self._parse_items(data))
                except Exception as e:
                    logger.error(f"Error reading local feed: {e}")

        # 2. Query remote endpoint if configured
        if self.endpoint_url:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(self.endpoint_url, params={"q": " ".join(params.keywords)})
                    if resp.status_code == 200:
                        results.extend(self._parse_items(resp.json()))
            except Exception as e:
                logger.error(f"Error querying JobbyGather endpoint: {e}")

        return results

    def _parse_items(self, raw_data: Any) -> List[JobOpportunity]:
        items: List[JobOpportunity] = []
        raw_list = raw_data if isinstance(raw_data, list) else raw_data.get("items", [])
        
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            try:
                opp_type = OpportunityType(item.get("opportunity_type", "offer"))
            except Exception:
                opp_type = OpportunityType.OFFER

            opp = JobOpportunity(
                source=item.get("source", self.name),
                opportunity_type=opp_type,
                title=item.get("title", "Untitled Opportunity"),
                company=item.get("company"),
                location=item.get("location"),
                remote_policy=item.get("remote_policy"),
                salary_raw=item.get("salary_raw"),
                url=item.get("url", ""),
                author_name=item.get("author_name"),
                author_title=item.get("author_title"),
                author_profile_url=item.get("author_profile_url"),
                description=item.get("description", ""),
                tags=item.get("tags", []),
            )
            items.append(opp)
        return items
