"""Glassdoor Provider for Jobby.

Extracts real job offers from Glassdoor (glassdoor.fr / glassdoor.com).
Uses direct Playwright navigation with robust fallback.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List
from urllib.parse import quote, urljoin

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider

logger = logging.getLogger(__name__)


class GlassdoorProvider(JobSourceProvider):
    name: str = "glassdoor"
    display_name: str = "Glassdoor"
    supports_posts: bool = False
    enabled: bool = True

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search Glassdoor job postings."""
        keywords_str = " ".join(params.keywords) if params.keywords else "developer"
        location_str = params.location or "France"

        return await asyncio.to_thread(self._fetch_sync, keywords_str, location_str, params)

    def _fetch_sync(self, keywords: str, location: str, params: SearchParams) -> List[JobOpportunity]:
        results: List[JobOpportunity] = []
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                launch_args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
                try:
                    browser = p.chromium.launch(headless=True, channel="chrome", args=launch_args)
                except Exception:
                    browser = p.chromium.launch(headless=True, args=launch_args)

                # Select appropriate domain based on location
                loc_lower = location.lower()
                is_us = any(term in loc_lower for term in ["usa", "united states", "états-unis", "california", "new york", "san francisco", "austin"])
                base_domain = "https://www.glassdoor.com" if is_us else "https://www.glassdoor.fr"

                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="fr-FR" if not is_us else "en-US",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()

                search_url = f"{base_domain}/Job/jobs.htm?sc.keyword={quote(keywords)}&locKeyword={quote(location)}"
                logger.info(f"Navigating to Glassdoor: {search_url}")
                page.goto(search_url, timeout=25000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                cards = page.query_selector_all(
                    'li[data-test="jobListing"], div[data-test="jobListing"], li.JobsList_jobListItem__wjTHv, div.JobCard_jobCardContainer__vR27w'
                )
                seen_urls = set()

                for card in cards:
                    if len(results) >= params.max_results:
                        break

                    link_el = card.query_selector(
                        'a[data-test="job-title"], a.JobCard_jobTitle___P9L1, a[href*="/partner/jobListing.htm"], a[href*="/job-listing/"]'
                    )
                    if not link_el:
                        continue

                    raw_href = link_el.get_attribute("href") or ""
                    if not raw_href:
                        continue
                    full_url = raw_href if raw_href.startswith("http") else urljoin(base_domain, raw_href)

                    # Deduplicate based on clean URL
                    clean_url = full_url.split("?")[0]
                    if clean_url in seen_urls:
                        continue
                    seen_urls.add(clean_url)

                    title = link_el.inner_text().strip()

                    comp_el = card.query_selector(
                        'span[class*="EmployerName"], div[class*="EmployerName"], [data-test="employer-name"], span[data-test="employer-short-name"]'
                    )
                    company = comp_el.inner_text().strip() if comp_el else "Entreprise (Glassdoor)"
                    # Clean rating e.g. "Google 4.5"
                    company = re.sub(r"\s+\d+(\.\d+)?\s*$", "", company).strip()

                    loc_el = card.query_selector('div[data-test="emp-location"], span[class*="location"], div[class*="location"]')
                    card_location = loc_el.inner_text().strip() if loc_el else location

                    salary_el = card.query_selector('span[data-test="detailSalary"], div[class*="SalaryEstimate"]')
                    salary = salary_el.inner_text().strip() if salary_el else None

                    desc_el = card.query_selector('div[class*="jobDescriptionSnippet"], div.JobCard_jobDescriptionSnippet__qN9Ws')
                    snippet = desc_el.inner_text().strip() if desc_el else ""

                    description = (
                        f"Offre d'emploi Glassdoor : {title}\n"
                        f"Entreprise : {company}\n"
                        f"Localisation : {card_location}\n"
                    )
                    if salary:
                        description += f"Fourchette de salaire : {salary}\n"
                    description += f"\nDescriptif :\n{snippet}\n\nConsultez l'offre sur Glassdoor pour les avis et postuler."

                    # Remote policy detection
                    remote_policy = None
                    full_text = f"{title} {card_location} {snippet}".lower()
                    if "télétravail" in full_text or "remote" in full_text:
                        remote_policy = "full" if ("100%" in full_text or "total" in full_text) else "hybrid"

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
                        tags=["glassdoor", "avis-salaires"],
                    )
                    results.append(opp)

                browser.close()
        except Exception as e:
            logger.warning(f"Error searching Glassdoor: {e}")

        return results
