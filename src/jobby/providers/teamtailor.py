"""Teamtailor & Scaleups ATS Provider for Jobby.

Extracts job offers directly from company career sites hosted on Teamtailor ATS
and European tech scaleups via public feeds (RSS/JSON) and ATS aggregation.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET
from typing import List
from urllib.parse import quote

import httpx

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider
from jobby.providers.relevance import (
    expand_creative_queries,
    is_location_compatible,
    is_relevant_job_offer,
)

logger = logging.getLogger(__name__)

# Curated list of prominent companies, scaleups, and agencies using Teamtailor ATS
TEAMTAILOR_SCALEUPS = [
    ("deezer", "Deezer"),
    ("payfit", "PayFit"),
    ("polestar", "Polestar"),
    ("spendesk", "Spendesk"),
    ("swile", "Swile"),
    ("contentsquare", "Contentsquare"),
    ("backmarket", "Back Market"),
    ("openclassrooms", "OpenClassrooms"),
    ("dailymotion", "Dailymotion"),
    ("lifen", "Lifen"),
    ("klarna", "Klarna"),
    ("bolt", "Bolt"),
    ("deliveryhero", "Delivery Hero"),
    ("wolt", "Wolt"),
    ("kaufman", "Kaufman & Broad"),
    ("clue", "Clue"),
    ("storytel", "Storytel"),
    ("mentimeter", "Mentimeter"),
    ("zenjob", "Zenjob"),
    ("greenpeacefrance", "Greenpeace France"),
    ("fiveguysfrance", "Five Guys France"),
    ("alven", "Alven"),
    ("mirakl", "Mirakl"),
]


class TeamtailorProvider(JobSourceProvider):
    name: str = "teamtailor"
    display_name: str = "Teamtailor (ATS & Scaleups)"
    supports_posts: bool = False
    enabled: bool = True

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search Teamtailor jobs across company boards with ATS fallback."""
        if not params.include_offers:
            return []

        results: List[JobOpportunity] = []
        seen_urls: set[str] = set()

        keywords = params.keywords or ["Brand Designer"]
        location = params.location or "France"

        # 1. Fetch direct Teamtailor boards via official public XML/RSS feeds
        async with httpx.AsyncClient(timeout=4.5, follow_redirects=True) as client:
            tasks = [client.get(f"https://{slug}.teamtailor.com/jobs.rss") for slug, _ in TEAMTAILOR_SCALEUPS]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            for (slug, comp_name), res in zip(TEAMTAILOR_SCALEUPS, responses):
                if len(results) >= params.max_results:
                    break

                if not isinstance(res, httpx.Response) or res.status_code != 200 or "<rss" not in res.text.lower():
                    continue

                try:
                    root = ET.fromstring(res.content)
                    for item in root.findall(".//item"):
                        if len(results) >= params.max_results:
                            break

                        link = item.findtext("link") or ""
                        if not link or link in seen_urls:
                            continue

                        title = (item.findtext("title") or "").strip()
                        desc_raw = item.findtext("description") or ""
                        desc_text = html.unescape(re.sub(r"<[^>]+>", "\n", desc_raw)).strip()
                        desc_text = re.sub(r"\n{3,}", "\n\n", desc_text)

                        if not is_relevant_job_offer(title, desc_text, keywords):
                            continue

                        locations_found = []
                        for loc_el in item.findall(".//{https://teamtailor.com/locations}location"):
                            city = loc_el.findtext("{https://teamtailor.com/locations}city")
                            country = loc_el.findtext("{https://teamtailor.com/locations}country")
                            loc_str = ", ".join(filter(None, [city, country]))
                            if loc_str:
                                locations_found.append(loc_str)

                        job_location = "; ".join(locations_found) if locations_found else location
                        remote_status = (item.findtext("{https://teamtailor.com/locations}remoteStatus") or "").lower()

                        remote_policy = None
                        if "fully" in remote_status or "remote" in remote_status:
                            remote_policy = "full"
                        elif "hybrid" in remote_status:
                            remote_policy = "hybrid"

                        if params.remote_only and remote_policy != "full":
                            continue
                        if not is_location_compatible(job_location, remote_policy, location):
                            continue

                        seen_urls.add(link)
                        clean_desc = (
                            f"🏢 Entreprise : {comp_name}\n"
                            f"📍 Localisation : {job_location}\n\n"
                            f"📝 Descriptif du poste :\n{desc_text[:4000]}"
                        )

                        opp = JobOpportunity(
                            source=self.name,
                            opportunity_type=OpportunityType.OFFER,
                            title=title,
                            company=comp_name,
                            location=job_location,
                            remote_policy=remote_policy,
                            url=link,
                            description=clean_desc,
                            tags=["Teamtailor", "Scaleup"],
                        )
                        results.append(opp)
                except Exception as e:
                    logger.debug(f"Error parsing Teamtailor feed for {slug}: {e}")

        # 2. Query European tech scaleup ATS feed (Arbeitnow) per keyword
        if len(results) < params.max_results:
            queries = expand_creative_queries(keywords, max_queries=2)
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    for query in queries:
                        if len(results) >= params.max_results:
                            break
                        api_url = f"https://www.arbeitnow.com/api/job-board-api?search={quote(query)}"
                        res = await client.get(api_url)
                        if res.status_code != 200:
                            continue
                        data = res.json().get("data", [])
                        for job in data:
                            if len(results) >= params.max_results:
                                break

                            job_url = job.get("url")
                            if not job_url or job_url in seen_urls:
                                continue

                            title = (job.get("title") or "").strip()
                            comp_name = (job.get("company_name") or "Scaleup Tech").strip()
                            job_loc = (job.get("location") or location).strip()
                            raw_desc = job.get("description") or ""
                            desc_text = html.unescape(re.sub(r"<[^>]+>", "\n", raw_desc)).strip()
                            desc_text = re.sub(r"\n{3,}", "\n\n", desc_text)

                            if not is_relevant_job_offer(title, desc_text, keywords):
                                continue

                            is_remote = bool(job.get("remote", False))
                            rem_policy = "full" if is_remote else None

                            if params.remote_only and rem_policy != "full":
                                continue
                            if not is_location_compatible(job_loc, rem_policy, location):
                                continue

                            seen_urls.add(job_url)
                            clean_desc = (
                                f"🏢 Entreprise : {comp_name}\n"
                                f"📍 Localisation : {job_loc}\n\n"
                                f"📝 Descriptif du poste :\n{desc_text[:4000]}"
                            )

                            opp = JobOpportunity(
                                source=self.name,
                                opportunity_type=OpportunityType.OFFER,
                                title=title,
                                company=comp_name,
                                location=job_loc,
                                remote_policy=rem_policy,
                                url=job_url,
                                description=clean_desc,
                                tags=["Teamtailor", "Scaleup"],
                            )
                            results.append(opp)
            except Exception as e:
                logger.debug(f"Error fetching ATS scaleup fallback: {e}")

        return results
