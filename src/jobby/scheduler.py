"""Sequential internal scheduler for Jobby daily job hunting.

Executes sequential queries at configured times (default 12:00 and 17:00 Europe/Paris),
applies anti-bot pacing on LinkedIn Posts, marks new items as unseen (is_seen=False),
and runs targeted Gemini auto-scoring on newly discovered opportunities.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
import zoneinfo

from jobby.config import JobbyConfig, load_config
from jobby.engine import DeduplicationEngine, ScoringEngine
from jobby.models import JobOpportunity, SearchParams
from jobby.providers import registry
from jobby.storage import Storage

logger = logging.getLogger(__name__)

PARIS_TIMEZONE = zoneinfo.ZoneInfo("Europe/Paris")

_hunting_lock = asyncio.Lock()
_last_executed_slot: Optional[Tuple[str, str]] = None  # (YYYY-MM-DD, HH:MM)


def get_current_paris_time() -> datetime:
    """Returns the current wall-clock datetime in Europe/Paris."""
    return datetime.now(PARIS_TIMEZONE)


def should_trigger_at(
    now: datetime,
    scheduled_times: List[str],
    last_slot: Optional[Tuple[str, str]] = None,
) -> Tuple[bool, Optional[Tuple[str, str]]]:
    """Determines whether the scheduler should execute a hunt at the given Paris time.
    
    A slot is defined by (current_date_string, HH:MM). It fires once within that minute.
    """
    date_str = now.strftime("%Y-%m-%d")
    current_hh_mm = now.strftime("%H:%M")

    # Normalize scheduled times (e.g. "12:0" -> "12:00")
    normalized_scheduled = []
    for t in scheduled_times:
        clean = t.strip()
        parts = clean.split(":")
        if len(parts) == 2:
            try:
                hh = int(parts[0])
                mm = int(parts[1])
                normalized_scheduled.append(f"{hh:02d}:{mm:02d}")
            except ValueError:
                continue

    if current_hh_mm in normalized_scheduled:
        slot = (date_str, current_hh_mm)
        if last_slot != slot:
            return True, slot

    return False, last_slot


async def execute_sequential_hunt(
    config: Optional[JobbyConfig] = None,
    storage: Optional[Storage] = None,
    user_id: Optional[str] = None,
    inter_query_delay: float = 1.0,
) -> Dict[str, Any]:
    """Runs sequential searches for each query in auto_search_queries.
    
    Protects against overlapping runs via an asyncio.Lock.
    Applies anti-bot pacing to authenticated LinkedIn queries.
    """
    if _hunting_lock.locked():
        logger.info("Chasse séquentielle déjà en cours d'exécution. Passage ignoré.")
        return {
            "status": "skipped",
            "reason": "already_running",
            "total_queries": 0,
            "total_found": 0,
            "new_count": 0,
        }

    async with _hunting_lock:
        config = config or load_config()
        storage = storage or Storage(config.database_path)

        queries = [
            q.strip()
            for q in (config.auto_search_queries or config.target_roles or ["brand designer"])
            if q and q.strip()
        ]
        if not queries:
            queries = ["brand designer"]

        location = config.preferred_locations[0] if config.preferred_locations else "France"
        scoring = ScoringEngine(config=config)

        total_found = 0
        new_count = 0
        all_collected_opps: List[JobOpportunity] = []
        linkedin_posts_attempted = 0
        max_linkedin_posts_per_session = 2  # Pacing: limit authenticated post queries per run

        for idx, query in enumerate(queries):
            # LinkedIn Posts pacing: include posts only for up to max_linkedin_posts_per_session queries
            include_posts = config.linkedin_include_posts and (linkedin_posts_attempted < max_linkedin_posts_per_session)
            if include_posts:
                linkedin_posts_attempted += 1

            params = SearchParams(
                keywords=[query],
                location=location,
                remote_only=config.remote_only,
                include_posts=include_posts,
                include_offers=config.linkedin_include_offers,
            )

            try:
                raw_results = await registry.search_all(params)
                deduped = DeduplicationEngine.deduplicate(raw_results)

                for opp in deduped:
                    score, reasons = scoring.score_opportunity(opp, active_keywords=params.keywords)
                    opp.score = score
                    opp.score_reasons = reasons
                    existing = storage.get_opportunity(opp.id)
                    if not existing:
                        opp.is_seen = False
                        new_count += 1
                    storage.upsert_opportunity(opp)
                    total_found += 1
                    all_collected_opps.append(opp)
            except Exception as e:
                logger.error(f"Erreur lors de la requête séquentielle '{query}' : {e}")

            # Anti-bot pacing between sequential searches
            if idx < len(queries) - 1 and inter_query_delay > 0:
                await asyncio.sleep(inter_query_delay)

        # Auto-scoring Gemini on unscored opportunities if candidate has an ideal_job_brief
        resolved_user_id = user_id or "usr_user_jobby_local"
        profile = storage.get_profile(resolved_user_id)
        if not profile:
            # Check default candidate profile
            u = storage.get_user_by_email("user@jobby.local")
            if u:
                profile = storage.get_profile(u.id)

        scored_by_gemini = 0
        if profile and profile.ideal_job_brief:
            brief_text = profile.ideal_job_brief.strip()
            # Find unscored opportunities among recently added
            unscored = [
                o for o in storage.list_opportunities(limit=50)
                if not (o.raw_metadata and o.raw_metadata.get("ai_match"))
            ]
            if unscored and brief_text:
                try:
                    from jobby.ai_analyzer import rank_opportunities_by_ideal_brief_with_meta
                    from jobby.server import _resolve_server_gemini_key

                    api_key = _resolve_server_gemini_key(config)
                    batch_to_score = unscored[:15]
                    await rank_opportunities_by_ideal_brief_with_meta(
                        brief=brief_text,
                        opportunities=batch_to_score,
                        api_key=api_key,
                    )
                    scored_by_gemini = len(batch_to_score)
                except Exception as e:
                    logger.warning(f"Auto-scoring Gemini fallback during scheduled hunt: {e}")

        return {
            "status": "success",
            "total_queries": len(queries),
            "total_found": total_found,
            "new_count": new_count,
            "scored_by_gemini": scored_by_gemini,
            "message": f"Chasse terminée : {len(queries)} requêtes, {new_count} nouvelles opportunités.",
        }


async def check_and_trigger_scheduled_hunt() -> Optional[Dict[str, Any]]:
    """Checks the Paris wall clock against configuration and runs hunting if due."""
    global _last_executed_slot
    config = load_config()
    if not config.auto_search_enabled:
        return None

    now = get_current_paris_time()
    due, new_slot = should_trigger_at(now, config.auto_search_times, _last_executed_slot)
    if due:
        _last_executed_slot = new_slot
        logger.info(f"[PLANIFICATEUR] Déclenchement de la veille programmée ({now.strftime('%H:%M')} Europe/Paris).")
        return await execute_sequential_hunt(config=config)

    return None


async def scheduler_loop():
    """Background polling loop started during server lifespan."""
    logger.info("[PLANIFICATEUR] Démarrage de la boucle asynchrone (Europe/Paris).")
    while True:
        try:
            await check_and_trigger_scheduled_hunt()
        except asyncio.CancelledError:
            logger.info("[PLANIFICATEUR] Arrêt de la boucle du planificateur.")
            break
        except Exception as e:
            logger.error(f"[PLANIFICATEUR] Erreur inattendue dans la boucle : {e}")

        # Check every 25 seconds
        try:
            await asyncio.sleep(25)
        except asyncio.CancelledError:
            break
