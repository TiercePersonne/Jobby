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
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote

import httpx
from bs4 import BeautifulSoup

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider

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
        results: List[JobOpportunity] = []
        seen_urls = set()

        keywords = params.keywords or ["developer"]
        location = params.location or "France"
        kw_lower = [k.lower() for k in keywords]
        loc_lower = location.lower()

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

                        title = item.findtext("title") or ""
                        desc_raw = item.findtext("description") or ""
                        # Strip HTML
                        desc_text = html.unescape(re.sub(r"<[^>]+>", " ", desc_raw)).strip()
                        desc_text = re.sub(r"\s+", " ", desc_text)

                        # Extract location and remote status from XML namespaces
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

                        # Filter by keywords & location
                        full_content = f"{title} {desc_text}".lower()
                        matches_kw = any(k in full_content for k in kw_lower) if kw_lower else True

                        if matches_kw:
                            seen_urls.add(link)
                            clean_desc = (
                                f"Offre d'emploi Teamtailor : {title}\n"
                                f"Entreprise : {comp_name}\n"
                                f"Localisation : {job_location}\n\n"
                                f"Descriptif :\n{desc_text[:600]}...\n\n"
                                f"Postuler directement sur le site carrière Teamtailor de l'entreprise."
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
                                tags=["teamtailor", "ats", "scaleup"],
                            )
                            results.append(opp)
                except Exception as e:
                    logger.debug(f"Error parsing Teamtailor feed for {slug}: {e}")

        # 2. If needed, query European tech scaleup ATS feed (Arbeitnow)
        if len(results) < params.max_results:
            try:
                search_q = "+".join(keywords)
                async with httpx.AsyncClient(timeout=4.0) as client:
                    api_url = f"https://www.arbeitnow.com/api/job-board-api?search={quote(search_q)}"
                    res = await client.get(api_url)
                    if res.status_code == 200:
                        data = res.json().get("data", [])
                        for job in data:
                            if len(results) >= params.max_results:
                                break

                            job_url = job.get("url")
                            if not job_url or job_url in seen_urls:
                                continue
                            seen_urls.add(job_url)

                            title = job.get("title") or "Offre Tech Scaleup"
                            comp_name = job.get("company_name") or "Scaleup Tech"
                            job_loc = job.get("location") or location
                            raw_desc = job.get("description") or ""
                            desc_text = html.unescape(re.sub(r"<[^>]+>", " ", raw_desc)).strip()
                            desc_text = re.sub(r"\s+", " ", desc_text)

                            is_remote = job.get("remote", False)
                            rem_policy = "full" if is_remote else None

                            clean_desc = (
                                f"Offre d'emploi ATS & Scaleup : {title}\n"
                                f"Entreprise : {comp_name}\n"
                                f"Localisation : {job_loc}\n\n"
                                f"Descriptif :\n{desc_text[:600]}...\n\n"
                                f"Consultez l'offre pour postuler."
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
                                tags=["teamtailor", "ats", "scaleup"],
                            )
                            results.append(opp)
            except Exception as e:
                logger.debug(f"Error fetching ATS scaleup fallback: {e}")

        return results
