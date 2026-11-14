"""Glassdoor Provider for Jobby.

Extracts real job offers from Glassdoor (glassdoor.fr / glassdoor.com).
Uses canonical location-scoped SRCH URLs, Playwright navigation, and strict relevance filtering.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from typing import Dict, List, Tuple
from urllib.parse import quote, urljoin

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider
from jobby.providers.relevance import (
    expand_creative_queries,
    is_location_compatible,
    is_relevant_job_offer,
)

logger = logging.getLogger(__name__)

# Glassdoor location codes for major French cities and France
GLASSDOOR_FR_LOCATIONS: Dict[str, Tuple[str, str]] = {
    "paris": ("paris", "IC2881970"),
    "lyon": ("lyon", "IC2864079"),
    "bordeaux": ("bordeaux", "IC2826293"),
    "lille": ("lille", "IC2885912"),
    "nantes": ("nantes", "IC2860623"),
    "toulouse": ("toulouse", "IC2854325"),
    "marseille": ("marseille", "IC2815709"),
    "france": ("france", "IN86"),
}


def _build_glassdoor_search_url(base_domain: str, keyword: str, location: str) -> str:
    """Build a canonical Glassdoor SRCH URL scoped to the target city/country when known."""
    loc_clean = (location or "France").strip().lower()
    kw_slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    if base_domain.endswith(".fr") and kw_slug:
        for city_key, (loc_slug, loc_code) in GLASSDOOR_FR_LOCATIONS.items():
            if city_key in loc_clean:
                loc_len = len(loc_slug)
                kw_start = loc_len + 1
                kw_end = kw_start + len(kw_slug)
                return (
                    f"{base_domain}/Job/{loc_slug}-{kw_slug}-jobs-"
                    f"SRCH_IL.0,{loc_len}_{loc_code}_KO{kw_start},{kw_end}.htm"
                )
    return f"{base_domain}/Job/jobs.htm?sc.keyword={quote(keyword)}&locKeyword={quote(location)}"


class GlassdoorProvider(JobSourceProvider):
    name: str = "glassdoor"
    display_name: str = "Glassdoor"
    supports_posts: bool = False
    enabled: bool = True

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search Glassdoor job postings with strict relevance and location filtering."""
        if not params.include_offers:
            return []

        queries = expand_creative_queries(params.keywords, max_queries=2)
        location_str = params.location or "France"

        return await asyncio.to_thread(self._fetch_sync, queries, location_str, params)

    def _fetch_sync(self, queries: List[str], location: str, params: SearchParams) -> List[JobOpportunity]:
        results: List[JobOpportunity] = []
        seen_urls: set[str] = set()
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                launch_args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
                proxy_url = (
                    os.environ.get("GLASSDOOR_PROXY")
                    or os.environ.get("HTTP_PROXY")
                    or os.environ.get("HTTPS_PROXY")
                )
                proxy_dict = {"server": proxy_url} if proxy_url else None

                try:
                    browser = p.chromium.launch(headless=True, channel="chrome", args=launch_args, proxy=proxy_dict)
                except Exception:
                    browser = p.chromium.launch(headless=True, args=launch_args, proxy=proxy_dict)

                loc_lower = location.lower()
                is_us = any(
                    term in loc_lower
                    for term in ["usa", "united states", "états-unis", "california", "new york", "san francisco", "austin"]
                )
                base_domain = "https://www.glassdoor.com" if is_us else "https://www.glassdoor.fr"

                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="fr-FR" if not is_us else "en-US",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()

                for query in queries:
                    if len(results) >= params.max_results:
                        break

                    search_url = _build_glassdoor_search_url(base_domain, query, location)
                    logger.info(f"Navigating to Glassdoor: {search_url}")
                    try:
                        page.goto(search_url, timeout=25000, wait_until="domcontentloaded")
                        page.wait_for_timeout(2500)
                    except Exception as nav_err:
                        logger.warning(f"Glassdoor navigation timeout/error for '{query}': {nav_err}")
                        continue

                    page_title = page.title()
                    if "Security | Glassdoor" in page_title or "Un instant" in page_title:
                        logger.warning(
                            "Glassdoor Cloudflare challenge encountered. Configure GLASSDOOR_PROXY if blocked."
                        )
                        break

                    # Extract full description of the pre-selected first card if rendered in right pane
                    first_full_desc = ""
                    try:
                        jd_el = page.query_selector('[class*="JobDetails_jobDescription"]')
                        if jd_el:
                            first_full_desc = html.unescape(jd_el.inner_text() or "").strip()
                    except Exception:
                        first_full_desc = ""

                    cards = page.query_selector_all(
                        'li[data-test="jobListing"], div[data-test="jobListing"], div[data-test="job-card-wrapper"], li.JobsList_jobListItem__wjTHv, div.JobCard_jobCardContainer__vR27w'
                    )

                    for idx, card in enumerate(cards):
                        if len(results) >= params.max_results:
                            break

                        link_el = card.query_selector(
                            'a[data-test="job-title"], a[class*="JobCard_jobTitle"], a[href*="/partner/jobListing.htm"], a[href*="/job-listing/"]'
                        )
                        if not link_el:
                            continue

                        raw_href = link_el.get_attribute("href") or ""
                        if not raw_href:
                            continue
                        full_url = raw_href if raw_href.startswith("http") else urljoin(base_domain, raw_href)

                        # Deduplicate by jl=... parameter or clean URL path
                        jl_match = re.search(r"[?&]jl=(\d+)", full_url)
                        dedup_key = jl_match.group(1) if jl_match else full_url.split("?")[0]
                        if dedup_key in seen_urls:
                            continue

                        title = html.unescape(link_el.inner_text() or "").strip()
                        if not title:
                            continue

                        comp_el = card.query_selector(
                            '[class*="EmployerProfile_compactEmployerName"], span[class*="EmployerName"], div[class*="EmployerName"], [data-test="employer-name"], span[data-test="employer-short-name"]'
                        )
                        company = html.unescape(comp_el.inner_text() or "").strip() if comp_el else "Entreprise"
                        company = re.sub(r"\s+\d+([.,]\d+)?\s*$", "", company).strip()

                        loc_el = card.query_selector(
                            'div[data-test="emp-location"], span[class*="location"], div[class*="location"]'
                        )
                        card_location = html.unescape(loc_el.inner_text() or "").strip() if loc_el else location

                        salary_el = card.query_selector(
                            'div[data-test="detailSalary"], span[data-test="detailSalary"], div[class*="salaryEstimate"], div[class*="SalaryEstimate"]'
                        )
                        salary = html.unescape(salary_el.inner_text() or "").strip() if salary_el else None
                        if salary:
                            salary = re.sub(r"\s*\(fourni par.*?\)\s*", "", salary, flags=re.I).strip()

                        desc_el = card.query_selector(
                            'div[data-test="descSnippet"], div[class*="jobDescriptionSnippet"]'
                        )
                        snippet = html.unescape(desc_el.inner_text() or "").strip() if desc_el else ""
                        snippet = snippet.replace("&hellip;", "…")

                        # Strict relevance check against search keywords
                        if not is_relevant_job_offer(title, snippet, params.keywords):
                            continue

                        # Remote policy detection
                        remote_policy = None
                        full_text = f"{title} {card_location} {snippet}".lower()
                        if "télétravail" in full_text or "remote" in full_text:
                            remote_policy = "full" if ("100%" in full_text or "total" in full_text or card_location.lower() == "télétravail") else "hybrid"

                        if params.remote_only and remote_policy != "full":
                            continue

                        if not is_location_compatible(card_location, remote_policy, location):
                            continue

                        seen_urls.add(dedup_key)

                        body_text = first_full_desc if (idx == 0 and len(first_full_desc) > len(snippet)) else snippet
                        desc_parts = [
                            f"🏢 Entreprise : {company}",
                            f"📍 Localisation : {card_location}",
                        ]
                        if salary:
                            desc_parts.append(f"💰 Rémunération : {salary}")
                        if body_text:
                            desc_parts.append(f"\n📝 Descriptif du poste :\n{body_text}")

                        description = "\n".join(desc_parts)

                        # Extract skills tags if listed in snippet
                        tags = ["Glassdoor"]
                        skills_match = re.search(r"Compétences\s*:\s*(.+)", snippet, flags=re.I)
                        if skills_match:
                            for sk in skills_match.group(1).split(","):
                                sk_clean = sk.strip()
                                if sk_clean and len(tags) < 6:
                                    tags.append(sk_clean)

                        opp = JobOpportunity(
                            source=self.name,
                            opportunity_type=OpportunityType.OFFER,
                            title=title,
                            company=company,
                            location=card_location,
                            salary_raw=salary,
                            remote_policy=remote_policy,
                            url=full_url,
                            description=description,
                            tags=tags,
                        )
                        results.append(opp)

                browser.close()
        except Exception as e:
            logger.warning(f"Error searching Glassdoor: {e}")

        return results
