"""Indeed Provider for Jobby.

Extracts real job offers from Indeed (indeed.fr / indeed.com).
Uses direct Playwright navigation with robust fallback to avoid anti-bot shielding.
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


class IndeedProvider(JobSourceProvider):
    name: str = "indeed"
    display_name: str = "Indeed"
    supports_posts: bool = False
    enabled: bool = True

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search Indeed job postings."""
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
                base_domain = "https://www.indeed.com" if is_us else "https://fr.indeed.com"

                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="fr-FR" if not is_us else "en-US",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()

                search_url = f"{base_domain}/jobs?q={quote(keywords)}&l={quote(location)}"
                logger.info(f"Navigating to Indeed: {search_url}")
                page.goto(search_url, timeout=25000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                # Look for Indeed job cards
                cards = page.query_selector_all("div.job_seen_beacon, div.cardOutline, tr.jobCard")
                seen_urls = set()

                for card in cards:
                    if len(results) >= params.max_results:
                        break

                    link_el = card.query_selector("h2.jobTitle a, a.jcs-JobTitle, a[href*='/viewjob'], a[href*='/rc/clk']")
                    if not link_el:
                        continue

                    raw_href = link_el.get_attribute("href") or ""
                    if not raw_href:
                        continue
                    full_url = raw_href if raw_href.startswith("http") else urljoin(base_domain, raw_href)

                    # Deduplicate based on URL
                    job_key = full_url.split("?")[0]
                    if "jk=" in full_url:
                        jk_match = re.search(r"jk=([a-zA-Z0-9]+)", full_url)
                        if jk_match:
                            job_key = jk_match.group(1)

                    if job_key in seen_urls:
                        continue
                    seen_urls.add(job_key)

                    title = link_el.inner_text().strip()
                    title = re.sub(r"^new\s+", "", title, flags=re.I).strip()

                    comp_el = card.query_selector('[data-testid="company-name"], span.companyName, [data-testid="companyName"]')
                    company = comp_el.inner_text().strip() if comp_el else "Entreprise (Indeed)"

                    loc_el = card.query_selector('[data-testid="text-location"], div.companyLocation')
                    card_location = loc_el.inner_text().strip() if loc_el else location

                    salary_el = card.query_selector("div.salary-snippet-container, div.metadata.salary-snippet-container")
                    salary = salary_el.inner_text().strip() if salary_el else None

                    snip_el = card.query_selector('div.job-snippet, [data-testid="job-snippet"]')
                    snippet = snip_el.inner_text().strip() if snip_el else ""

                    card_raw_text = card.inner_text().strip()
                    card_lines = [l.strip() for l in card_raw_text.split("\n") if l.strip()]
                    meaningful_lines = [
                        l for l in card_lines
                        if l not in [title, company, card_location, salary]
                        and len(l) > 3
                        and not any(x in l.lower() for x in ["rechercher les salaires", "consulter les questions", "voir toutes les offres", "postulez via", "sauvegarder l'offre"])
                    ]
                    clean_snippet = "\n• ".join(dict.fromkeys(meaningful_lines)) if meaningful_lines else snippet

                    description = (
                        f"Offre d'emploi Indeed : {title}\n"
                        f"Entreprise : {company}\n"
                        f"Localisation : {card_location}\n"
                    )
                    if salary:
                        description += f"💰 Rémunération : {salary}\n"
                    if clean_snippet:
                        description += f"\n📝 Détails de l'offre :\n• {clean_snippet}\n"
                    description += "\nConsultez l'annonce sur Indeed pour la fiche de poste détaillée et postuler."

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
                        tags=["indeed", "emploi"],
                    )
                    results.append(opp)

                browser.close()
        except Exception as e:
            logger.warning(f"Error searching Indeed: {e}")

        return results
