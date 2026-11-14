"""Indeed Provider for Jobby.

Extracts real job offers from Indeed (fr.indeed.com / indeed.com).
Uses quoted multi-word queries, mosaic provider JSON + DOM extraction, and strict relevance filtering.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from typing import Any, Dict, List
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider
from jobby.providers.relevance import (
    expand_creative_queries,
    is_location_compatible,
    is_relevant_job_offer,
)

logger = logging.getLogger(__name__)


class IndeedProvider(JobSourceProvider):
    name: str = "indeed"
    display_name: str = "Indeed"
    supports_posts: bool = False
    enabled: bool = True

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search Indeed job postings."""
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
                try:
                    browser = p.chromium.launch(headless=True, channel="chrome", args=launch_args)
                except Exception:
                    browser = p.chromium.launch(headless=True, args=launch_args)

                loc_lower = location.lower()
                is_us = any(
                    term in loc_lower
                    for term in ["usa", "united states", "états-unis", "california", "new york", "san francisco", "austin"]
                )
                base_domain = "https://www.indeed.com" if is_us else "https://fr.indeed.com"

                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="fr-FR" if not is_us else "en-US",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()

                for raw_query in queries:
                    if len(results) >= params.max_results:
                        break

                    # Quote multi-word English phrase "Brand Designer" on fr.indeed.com
                    # to prevent Indeed from expanding "Designer Brand" into luxury retail stores.
                    q_formatted = f'"{raw_query}"' if raw_query.strip().lower() == "brand designer" else raw_query
                    search_url = f"{base_domain}/jobs?q={quote(q_formatted)}&l={quote(location)}"
                    logger.info(f"Navigating to Indeed: {search_url}")
                    try:
                        page.goto(search_url, timeout=25000, wait_until="domcontentloaded")
                        page.wait_for_timeout(2500)
                    except Exception as nav_err:
                        logger.warning(f"Indeed navigation timeout/error for '{raw_query}': {nav_err}")
                        continue

                    page_html = page.content()

                    # 1. Try extracting structured job cards from window.mosaic.providerData
                    m_mosaic = re.search(
                        r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.*?\});',
                        page_html,
                        re.S,
                    )
                    mosaic_items: List[Dict[str, Any]] = []
                    if m_mosaic:
                        try:
                            mosaic_json = json.loads(m_mosaic.group(1))
                            mosaic_items = (
                                mosaic_json.get("metaData", {})
                                .get("mosaicProviderJobCardsModel", {})
                                .get("results", [])
                            )
                        except Exception:
                            mosaic_items = []

                    for item in mosaic_items:
                        if len(results) >= params.max_results:
                            break
                        jk = item.get("jobkey")
                        if not jk or jk in seen_urls:
                            continue

                        title = html.unescape(item.get("title") or item.get("displayTitle") or "").strip()
                        if not title:
                            continue

                        company = html.unescape(item.get("company") or "Entreprise").strip()
                        card_location = html.unescape(item.get("formattedLocation") or location).strip()
                        raw_snippet = item.get("snippet") or ""
                        clean_snippet = BeautifulSoup(raw_snippet, "html.parser").get_text("\n• ", strip=True)

                        full_url = f"{base_domain}/viewjob?jk={jk}"
                        if not is_relevant_job_offer(
                            title,
                            clean_snippet,
                            params.keywords,
                            source=self.name,
                            company=company,
                            location=card_location,
                            url=full_url,
                        ):
                            continue

                        remote_policy = None
                        full_text = f"{title} {card_location} {clean_snippet}".lower()
                        if item.get("remoteLocation") or "télétravail" in full_text or "remote" in full_text:
                            remote_policy = "full" if ("100%" in full_text or "total" in full_text or "complet" in full_text) else "hybrid"

                        if params.remote_only and remote_policy != "full":
                            continue
                        if not is_location_compatible(
                            card_location,
                            remote_policy,
                            location,
                            source=self.name,
                            title=title,
                            company=company,
                            url=full_url,
                            description=clean_snippet,
                        ):
                            continue

                        seen_urls.add(jk)

                        salary_info = (item.get("salarySnippet") or {}).get("text")
                        tags = ["Indeed"]
                        for taxo in item.get("taxonomyAttributes") or []:
                            for attr in taxo.get("attributes") or []:
                                lbl = attr.get("label")
                                if lbl and lbl not in tags and len(tags) < 5:
                                    tags.append(lbl)

                        full_url = f"{base_domain}/viewjob?jk={jk}"
                        desc_lines = [
                            f"🏢 Entreprise : {company}",
                            f"📍 Localisation : {card_location}",
                        ]
                        if len(tags) > 1:
                            desc_lines.append(f"📄 Contrat : {', '.join(tags[1:])}")
                        if salary_info:
                            desc_lines.append(f"💰 Rémunération : {salary_info}")
                        if clean_snippet:
                            desc_lines.append(f"\n📝 Détails de l'offre :\n• {clean_snippet}")

                        opp = JobOpportunity(
                            source=self.name,
                            opportunity_type=OpportunityType.OFFER,
                            title=title,
                            company=company,
                            location=card_location,
                            salary_raw=salary_info,
                            remote_policy=remote_policy,
                            url=full_url,
                            description="\n".join(desc_lines),
                            tags=tags,
                        )
                        results.append(opp)

                    # 2. Fallback to DOM card parsing if mosaic JSON was not present
                    cards = page.query_selector_all("div.job_seen_beacon, div.cardOutline, tr.jobCard")
                    for card in cards:
                        if len(results) >= params.max_results:
                            break

                        link_el = card.query_selector(
                            "h2.jobTitle a, a.jcs-JobTitle, a[href*='/viewjob'], a[href*='/rc/clk']"
                        )
                        if not link_el:
                            continue

                        raw_href = link_el.get_attribute("href") or ""
                        if not raw_href:
                            continue
                        full_url = raw_href if raw_href.startswith("http") else urljoin(base_domain, raw_href)

                        job_key = full_url.split("?")[0]
                        if "jk=" in full_url:
                            jk_match = re.search(r"jk=([a-zA-Z0-9]+)", full_url)
                            if jk_match:
                                job_key = jk_match.group(1)

                        if job_key in seen_urls:
                            continue

                        title = link_el.inner_text().strip()
                        title = re.sub(r"^new\s+", "", title, flags=re.I).strip()

                        comp_el = card.query_selector(
                            '[data-testid="company-name"], span.companyName, [data-testid="companyName"]'
                        )
                        company = comp_el.inner_text().strip() if comp_el else "Entreprise"

                        loc_el = card.query_selector('[data-testid="text-location"], div.companyLocation')
                        card_location = loc_el.inner_text().strip() if loc_el else location

                        salary_el = card.query_selector(
                            "div.salary-snippet-container, div.metadata.salary-snippet-container"
                        )
                        salary = salary_el.inner_text().strip() if salary_el else None

                        snip_el = card.query_selector('div.job-snippet, [data-testid="job-snippet"]')
                        snippet = snip_el.inner_text().strip() if snip_el else ""

                        if not is_relevant_job_offer(
                            title,
                            snippet,
                            params.keywords,
                            source=self.name,
                            company=company,
                            location=card_location,
                            url=full_url,
                        ):
                            continue

                        remote_policy = None
                        full_text = f"{title} {card_location} {snippet}".lower()
                        if "télétravail" in full_text or "remote" in full_text:
                            remote_policy = "full" if ("100%" in full_text or "total" in full_text) else "hybrid"

                        if params.remote_only and remote_policy != "full":
                            continue
                        if not is_location_compatible(
                            card_location,
                            remote_policy,
                            location,
                            source=self.name,
                            title=title,
                            company=company,
                            url=full_url,
                            description=snippet,
                        ):
                            continue

                        seen_urls.add(job_key)

                        description = (
                            f"🏢 Entreprise : {company}\n"
                            f"📍 Localisation : {card_location}\n"
                        )
                        if salary:
                            description += f"💰 Rémunération : {salary}\n"
                        if snippet:
                            description += f"\n📝 Détails de l'offre :\n• {snippet}\n"

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
                            tags=["Indeed"],
                        )
                        results.append(opp)

                browser.close()
        except Exception as e:
            logger.warning(f"Error searching Indeed: {e}")

        return results
