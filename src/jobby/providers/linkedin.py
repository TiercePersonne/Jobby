"""LinkedIn Provider for Jobby.

Extracts both official job listings (via guest API) and network hiring posts
(via social content search & recruiter patterns).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from jobby.ai_analyzer import analyze_post_heuristic, detect_remote_policy
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
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Hiring trigger keywords used when searching LinkedIn social posts
HIRING_TRIGGERS_FR = [
    '"on recrute"',
    '"nous recrutons"',
    '"je cherche"',
    '"recherche dev"',
    '"poste ouvert"',
    '"opportunité"',
    '"cdi"',
]

HIRING_TRIGGERS_EN = [
    '"we are hiring"',
    '"i am hiring"',
    '"looking for"',
    '"join our team"',
    '"open role"',
]


class LinkedInProvider(JobSourceProvider):
    name: str = "linkedin"
    display_name: str = "LinkedIn (Offres & Posts)"
    supports_posts: bool = True
    enabled: bool = True

    def __init__(self, li_at: Optional[str] = None):
        self.li_at = li_at

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        opportunities: List[JobOpportunity] = []

        # 1. Collect official job offers if requested
        if params.include_offers:
            offers = await self._search_official_jobs(params)
            opportunities.extend(offers)

        # 2. Collect social hiring posts if requested
        if params.include_posts:
            posts = await self._search_social_posts(params)
            opportunities.extend(posts)

        return opportunities

    async def _search_official_jobs(self, params: SearchParams) -> List[JobOpportunity]:
        """Search official LinkedIn job postings via the public guest API."""
        url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        queries = expand_creative_queries(params.keywords, max_queries=2)
        location_str = params.location or "France"

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        opportunities: List[JobOpportunity] = []
        seen_urls: set[str] = set()
        max_to_fetch = min(params.max_results, 50)

        async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
            for query in queries:
                if len(opportunities) >= max_to_fetch:
                    break
                start = 0
                while len(opportunities) < max_to_fetch and start < max_to_fetch:
                    query_params: Dict[str, Any] = {
                        "keywords": query,
                        "location": location_str,
                        "f_TPR": f"r{params.posted_within_days * 86400}",
                        "start": start,
                    }
                    if params.remote_only:
                        query_params["f_WT"] = "2"

                    try:
                        resp = await client.get(url, params=query_params)
                        if resp.status_code != 200:
                            logger.warning(f"LinkedIn public jobs responded with status {resp.status_code}")
                            break

                        cards = self._parse_job_cards(resp.text)
                        if not cards:
                            break

                        for card in cards:
                            if len(opportunities) >= max_to_fetch:
                                break
                            if card.url in seen_urls:
                                continue
                            if not is_relevant_job_offer(card.title, card.description, params.keywords):
                                continue
                            if not is_location_compatible(card.location, card.remote_policy, location_str):
                                continue
                            seen_urls.add(card.url)
                            opportunities.append(card)

                        start += len(cards)
                        await asyncio.sleep(random.uniform(0.4, 0.8))

                    except Exception as e:
                        logger.error(f"Error fetching LinkedIn jobs at offset {start}: {e}")
                        break

            # Enrich descriptions for fetched cards with gentle concurrency to avoid HTTP 429
            if opportunities:
                sem = asyncio.Semaphore(2)

                async def sem_enrich(opp: JobOpportunity):
                    async with sem:
                        await self._enrich_job_description(client, opp)
                        await asyncio.sleep(random.uniform(0.3, 0.5))

                await asyncio.gather(*(sem_enrich(opp) for opp in opportunities), return_exceptions=True)

        return opportunities

    async def _enrich_job_description(self, client: httpx.AsyncClient, opp: JobOpportunity) -> None:
        """Fetch full job posting details from LinkedIn guest API or fallback to view page."""
        m = re.search(r"(\d{8,12})", opp.url)
        if not m:
            return
        job_id = m.group(1)
        api_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
        view_url = f"https://www.linkedin.com/jobs/view/{job_id}/"

        for target_url in [api_url, view_url]:
            try:
                resp = await client.get(target_url, timeout=12.0)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    desc_elem = soup.find(class_=re.compile(r"show-more-less-html__markup|description__text", re.I))

                    parts: List[str] = []
                    criteria = soup.find_all(class_=re.compile(r"description__job-criteria-item", re.I))
                    criteria_texts = [c.get_text(" : ", strip=True) for c in criteria if c.get_text(strip=True)]
                    if criteria_texts:
                        parts.append("📌 Informations clés :\n• " + "\n• ".join(criteria_texts))

                    if desc_elem:
                        parts.append("📝 Descriptif complet du poste :\n" + desc_elem.get_text("\n", strip=True))

                    if parts:
                        opp.description = "\n\n".join(parts)
                        return
                elif resp.status_code == 429:
                    await asyncio.sleep(0.8)
                    continue
            except Exception as e:
                logger.debug(f"Could not enrich LinkedIn job {job_id} via {target_url}: {e}")

        # If external fetch was completely unavailable, ensure clean summary
        if not opp.description or len(opp.description) < 60 or "il y a" in opp.description:
            opp.description = (
                f"Offre LinkedIn : {opp.title}"
                + (f" chez {opp.company}" if opp.company else "")
                + (f" ({opp.location})" if opp.location else "")
                + ".\n\nConsultez l'offre directement sur LinkedIn pour afficher le descriptif complet et postuler."
            )

    def _parse_job_cards(self, html_content: str) -> List[JobOpportunity]:
        """Parse job cards from LinkedIn guest HTML response."""
        soup = BeautifulSoup(html_content, "html.parser")
        items = soup.find_all("li")
        results: List[JobOpportunity] = []

        for item in items:
            title_elem = item.find("h3", class_=re.compile(r"base-search-card__title", re.I))
            if not title_elem:
                continue
            title = title_elem.get_text(strip=True)

            company_elem = item.find("h4", class_=re.compile(r"base-search-card__subtitle", re.I))
            company = company_elem.get_text(strip=True) if company_elem else None

            loc_elem = item.find("span", class_=re.compile(r"job-search-card__location", re.I))
            location = loc_elem.get_text(strip=True) if loc_elem else None

            link_elem = item.find("a", class_=re.compile(r"base-card__full-link", re.I))
            if not link_elem or "href" not in link_elem.attrs:
                continue
            url = link_elem["href"]

            # Publication time
            time_elem = item.find("time", class_=re.compile(r"job-search-card__listdate", re.I))
            published_at = None
            if time_elem and time_elem.get("datetime"):
                try:
                    published_at = date_parser.parse(time_elem["datetime"])
                except Exception:
                    published_at = None

            # Salary if present
            salary_elem = item.find("span", class_=re.compile(r"job-search-card__salary-info", re.I))
            salary = salary_elem.get_text(strip=True) if salary_elem else None

            # Card snippet (only genuine job snippet, ignore date/city badges)
            snippet_elem = item.find(class_=re.compile(r"job-search-card__snippet|job-snippet", re.I))
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else None
            if snippet and (len(snippet) < 30 or any(x in snippet.lower() for x in ["il y a", "recrutement actif"])):
                snippet = None

            description = snippet or (
                f"Offre LinkedIn : {title}"
                + (f" chez {company}" if company else "")
                + (f" ({location})" if location else "")
                + ".\n\nCliquez sur le lien pour afficher la fiche de poste complète sur LinkedIn et postuler."
            )

            remote_policy = detect_remote_policy(title=title, description=description, location=location)

            opp = JobOpportunity(
                source=self.name,
                opportunity_type=OpportunityType.OFFER,
                title=title,
                company=company,
                location=location,
                remote_policy=remote_policy,
                salary_raw=salary,
                url=url,
                published_at=published_at,
                description=description,
            )
            results.append(opp)

        return results

    async def _search_social_posts(self, params: SearchParams) -> List[JobOpportunity]:
        """
        Search LinkedIn social posts (recruiter & network hiring announcements).
        If user has connected their LinkedIn session (li_at or browser profile),
        extracts individual posts card by card.
        """
        li_at = self.li_at or load_config().linkedin_li_at or os.environ.get("LINKEDIN_LI_AT")
        profile_dir = Path("_bmad-output/browser_profiles/linkedin")

        # 1. Try Playwright browser session if persistent profile exists or li_at is set
        if profile_dir.exists() or li_at:
            browser_posts = await asyncio.to_thread(self._scrape_posts_with_browser_sync, params, li_at)
            if browser_posts:
                return browser_posts

        # 2. Try Voyager API fallback if li_at cookie is configured
        if li_at:
            voyager_posts = await self._search_posts_with_voyager(params, li_at=li_at)
            if voyager_posts:
                return voyager_posts

        logger.info("LinkedIn post search: unauthenticated. Connect in 'Comptes' to enable post-by-post extraction.")
        return []

    async def _search_posts_with_voyager(self, params: SearchParams, li_at: str) -> List[JobOpportunity]:
        """Direct authenticated query to LinkedIn Voyager Content Search."""
        triggers = " OR ".join(HIRING_TRIGGERS_FR[:3] + HIRING_TRIGGERS_EN[:2])
        keywords = " ".join([f'"{k}"' for k in params.keywords]) if params.keywords else "developer"
        query_string = f"({triggers}) {keywords}"
        if params.location:
            query_string += f" {params.location}"

        url = "https://www.linkedin.com/voyager/api/search/dash/clusters"
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Cookie": f'li_at={li_at}; JSESSIONID="ajax:1234567890"',
            "csrf-token": "ajax:1234567890",
            "x-restli-protocol-version": "2.0.0",
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
        }
        query_params = {
            "decorationId": "com.linkedin.voyager.dash.deco.search.SearchClusterCollection-175",
            "origin": "FACETED_SEARCH",
            "q": "all",
            "query": f"(keywords:{quote(query_string)},flagshipSearchIntent:SEARCH_SRP)",
            "start": 0,
            "count": min(params.max_results, 30),
        }

        results: List[JobOpportunity] = []
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
                resp = await client.get(url, params=query_params)
                if resp.status_code == 200:
                    data = resp.json()
                    results = self._parse_voyager_posts(data, params=params)
                else:
                    logger.warning(f"Voyager API returned status {resp.status_code}")
        except Exception as e:
            logger.error(f"Voyager query failed: {e}")

        return results

    def _parse_voyager_posts(self, data: Dict[str, Any], params: Optional[SearchParams] = None) -> List[JobOpportunity]:
        """Extract posts, author name, author title, text and direct link from Voyager response."""
        posts: List[JobOpportunity] = []
        included = data.get("included", [])
        
        for item in included:
            type_str = item.get("$type", "")
            if "Update" in type_str or "FeedComponent" in type_str:
                commentary = item.get("commentary", {}).get("text", {}).get("text", "")
                if not commentary or len(commentary) < 25:
                    continue

                # STRICT USER REQUIREMENT:
                # Exclude author's title / position / headline from matching!
                # Target keywords MUST appear directly inside the post commentary!
                if params and params.keywords:
                    comm_lower = commentary.lower()
                    if not any(k.lower() in comm_lower for k in params.keywords):
                        continue

                urn = item.get("urn", "")
                post_url = f"https://www.linkedin.com/feed/update/{urn}" if urn else ""

                # Extract author info if available
                actor = item.get("actor", {})
                author_name = actor.get("name", {}).get("text")
                author_title = actor.get("subDescription", {}).get("text")
                author_url = actor.get("navigationUrl")

                # Extract first line for title
                first_line = commentary.strip().split("\n")[0][:100]
                title = f"Post de {author_name or 'Recruteur'}: {first_line}"
                analysis = analyze_post_heuristic(commentary, author_name or "", author_title or "")

                posts.append(JobOpportunity(
                    source=self.name,
                    opportunity_type=OpportunityType.POST,
                    title=title,
                    url=post_url or "https://www.linkedin.com/feed/",
                    author_name=author_name,
                    author_title=author_title,
                    author_profile_url=author_url,
                    description=commentary,
                    remote_policy=analysis.get("remote_policy"),
                    post_intent=analysis.get("intent"),
                    ai_summary=analysis.get("summary"),
                    tags=["linkedin-post", analysis.get("intent", "post")],
                ))
        return posts

    def _scrape_posts_with_browser_sync(self, params: SearchParams, li_at: Optional[str] = None) -> List[JobOpportunity]:
        """Scrapes individual LinkedIn posts using persistent Playwright browser profile."""
        from playwright.sync_api import sync_playwright

        profile_dir = Path("_bmad-output/browser_profiles/linkedin")
        triggers = '("on recrute" OR "nous recrutons" OR "cherche" OR "hiring")'
        keywords_str = " ".join([f'"{k}"' for k in params.keywords]) if params.keywords else "developer"
        location_str = params.location or "France"
        query_string = f"{triggers} {keywords_str} {location_str}"
        search_url = f"https://www.linkedin.com/search/results/content/?keywords={quote(query_string)}&sortBy=%22date_posted%22"

        posts: List[JobOpportunity] = []
        try:
            with sync_playwright() as p:
                launch_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                if profile_dir.exists():
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir=str(profile_dir),
                            headless=True,
                            channel="chrome",
                            args=launch_args,
                            permissions=["clipboard-read", "clipboard-write"],
                        )
                    except Exception:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir=str(profile_dir),
                            headless=True,
                            args=launch_args,
                            permissions=["clipboard-read", "clipboard-write"],
                        )
                else:
                    try:
                        browser = p.chromium.launch(headless=True, channel="chrome", args=launch_args)
                    except Exception:
                        browser = p.chromium.launch(headless=True, args=launch_args)
                    context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
                    if li_at:
                        context.add_cookies([{"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"}])

                page = context.new_page()
                page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
                time.sleep(3.5)

                if "login" in page.url or "checkpoint" in page.url:
                    logger.info("LinkedIn requires login for post search; skipping automated scrape.")
                    context.close()
                    return []

                # Scroll once or twice to trigger feed loading
                page.evaluate("window.scrollBy(0, 1000)")
                time.sleep(1.5)

                # Expand truncated texts
                see_more_btns = page.query_selector_all('button:has-text("… plus"), button:has-text("... plus"), button:has-text("see more")')
                for b in see_more_btns:
                    try:
                        b.click(timeout=400)
                    except Exception:
                        pass

                # Find post cards with modern LinkedIn data-view-name
                cards = page.query_selector_all('div[data-view-name="feed-full-update"], [role="listitem"]')
                seen_commentaries = set()

                for card in cards[:min(params.max_results * 2, 25)]:
                    author_link_el = card.query_selector('a[href*="/in/"]')
                    author_url = author_link_el.get_attribute('href') if author_link_el else ""

                    all_lines = [l.strip() for l in card.inner_text().split('\n') if l.strip()]
                    if not all_lines or len(all_lines) < 4:
                        continue

                    # Skip header badges like "Post du fil d’actualité"
                    start_idx = 0
                    if "fil d" in all_lines[0].lower():
                        start_idx = 1

                    author_name = all_lines[start_idx] if len(all_lines) > start_idx else "Recruteur LinkedIn"

                    # Author headline line
                    headline_idx = start_idx + 1
                    while headline_idx < len(all_lines) and any(x in all_lines[headline_idx].lower() for x in ["1er", "2e", "3e", "suivre", "•"]):
                        headline_idx += 1
                    author_title = all_lines[headline_idx] if headline_idx < len(all_lines) else "Réseau LinkedIn"

                    # Relative time line (e.g. 12 min, 1 h, 2 j)
                    time_idx = headline_idx + 1
                    while time_idx < len(all_lines) and not any(unit in all_lines[time_idx].lower() for unit in ["min", " h", " j", " s", "sem", "mo"]):
                        time_idx += 1

                    body_start = time_idx + 1
                    while body_start < len(all_lines) and all_lines[body_start].lower() in ["suivre", "follow", "+ suivre"]:
                        body_start += 1

                    # Filter out trailing action button texts ("J’aime", "Commenter", etc.)
                    body_lines = []
                    for line in all_lines[body_start:]:
                        if any(action in line.lower() for action in ["j’aime", "jaime", "commenter", "republier", "envoyer"]):
                            if len(body_lines) > 2:
                                break
                        body_lines.append(line)

                    commentary = "\n\n".join(body_lines) if body_lines else "\n".join(all_lines[body_start:])
                    if not commentary or len(commentary) < 25:
                        continue

                    # STRICT USER REQUIREMENT: Ignore author's headline / profile title!
                    # Only check the post text itself for the searched keywords!
                    comm_lower = commentary.lower()
                    if params.keywords:
                        if not any(k.lower() in comm_lower for k in params.keywords):
                            continue

                    # Avoid duplicate cards on same page
                    if commentary[:80] in seen_commentaries:
                        continue
                    seen_commentaries.add(commentary[:80])

                    # Try copying exact permalink via clipboard
                    post_url = ""
                    try:
                        menu_btn = card.query_selector('button[data-view-name="feed-control-menu"]')
                        if menu_btn:
                            menu_btn.click(timeout=800)
                            time.sleep(0.3)
                            copy_btn = page.query_selector('div[role="menuitem"]:has-text("Copier le lien"), div[role="menuitem"]:has-text("Copy link")')
                            if copy_btn:
                                copy_btn.click(timeout=800)
                                time.sleep(0.3)
                                post_url = page.evaluate("navigator.clipboard.readText()")
                    except Exception:
                        pass

                    if not post_url or not post_url.startswith("http"):
                        post_url = author_url or "https://www.linkedin.com/feed/"

                    first_line = commentary.split("\n")[0][:90]
                    analysis = analyze_post_heuristic(commentary, author_name or "", author_title or "")
                    posts.append(JobOpportunity(
                        source=self.name,
                        opportunity_type=OpportunityType.POST,
                        title=f"Post de {author_name}: {first_line}",
                        company="Réseau LinkedIn",
                        location=params.location or "France",
                        url=post_url,
                        author_name=author_name,
                        author_title=author_title,
                        description=commentary,
                        remote_policy=analysis.get("remote_policy"),
                        post_intent=analysis.get("intent"),
                        ai_summary=analysis.get("summary"),
                        tags=["linkedin-post", analysis.get("intent", "post")],
                    ))
                    if len(posts) >= params.max_results:
                        break

                context.close()
        except Exception as e:
            logger.warning(f"Error scraping LinkedIn posts with browser: {e}")

        return posts
