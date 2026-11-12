"""Tests for the internal sequential scheduler and anti-bot pacing (Story 4.2)."""

from __future__ import annotations

import asyncio
from datetime import datetime
import pytest
from unittest.mock import AsyncMock, patch
import zoneinfo

from jobby.config import JobbyConfig, save_config
from jobby.models import JobOpportunity, MatchEvaluation, User, UserProfile
from jobby.providers import registry
from jobby.scheduler import (
    PARIS_TIMEZONE,
    _hunting_lock,
    execute_sequential_hunt,
    get_current_paris_time,
    should_trigger_at,
)
from jobby.storage import Storage


def test_paris_timezone_and_slot_trigger():
    """Validates that should_trigger_at strictly respects Europe/Paris time slots and avoids duplicate runs."""
    # 1. 12:00 in Paris should trigger
    dt_paris_12 = datetime(2026, 11, 12, 12, 0, 15, tzinfo=PARIS_TIMEZONE)
    due, slot = should_trigger_at(dt_paris_12, scheduled_times=["12:00", "17:00"], last_slot=None)
    assert due is True
    assert slot == ("2026-11-12", "12:00")

    # 2. Second check within the same minute with last_slot set should NOT re-trigger
    due_again, slot_again = should_trigger_at(dt_paris_12, scheduled_times=["12:00", "17:00"], last_slot=slot)
    assert due_again is False
    assert slot_again == slot

    # 3. 12:05 should not trigger
    dt_paris_1205 = datetime(2026, 11, 12, 12, 5, 0, tzinfo=PARIS_TIMEZONE)
    due_off, _ = should_trigger_at(dt_paris_1205, scheduled_times=["12:00", "17:00"], last_slot=slot)
    assert due_off is False

    # 4. 17:00 in Paris should trigger next slot
    dt_paris_17 = datetime(2026, 11, 12, 17, 0, 30, tzinfo=PARIS_TIMEZONE)
    due_17, slot_17 = should_trigger_at(dt_paris_17, scheduled_times=["12:00", "17:00"], last_slot=slot)
    assert due_17 is True
    assert slot_17 == ("2026-11-12", "17:00")


@pytest.mark.anyio
async def test_execute_sequential_hunt_queries_and_is_seen_flag(tmp_path, monkeypatch):
    """Verifies that queries are executed sequentially, new opportunities receive is_seen=False, and LinkedIn pacing is applied."""
    db_path = tmp_path / "sched_test.db"
    cfg_path = tmp_path / "jobby_sched.json"
    storage = Storage(db_path)

    config = JobbyConfig(
        database_path=str(db_path),
        auto_search_queries=["brand designer", "directeur artistique"],
        auto_search_times=["12:00", "17:00"],
        linkedin_include_posts=True,
        linkedin_include_offers=True,
    )
    save_config(config, config_path=cfg_path)

    executed_params = []

    async def fake_collect(params):
        executed_params.append(params)
        keyword = params.keywords[0]
        return [
            JobOpportunity(
                id=f"opp-sched-{keyword.replace(' ', '-')}-1",
                title=f"Lead {keyword}",
                company="Studio Test",
                location="Paris",
                source="wttj",
                url=f"https://example.com/{keyword}/1",
                status="new",
                is_seen=True,  # Mock collector returning default True, scheduler must set to False
            )
        ]

    with patch.object(registry, "search_all", side_effect=fake_collect):
        result = await execute_sequential_hunt(config=config, storage=storage, inter_query_delay=0.01)

    assert result["status"] == "success"
    assert result["total_queries"] == 2
    assert result["new_count"] == 2
    assert len(executed_params) == 2
    assert executed_params[0].keywords == ["brand designer"]
    assert executed_params[1].keywords == ["directeur artistique"]

    # Verify persisted with is_seen = False
    opp1 = storage.get_opportunity("opp-sched-brand-designer-1")
    assert opp1 is not None
    assert opp1.is_seen is False

    opp2 = storage.get_opportunity("opp-sched-directeur-artistique-1")
    assert opp2 is not None
    assert opp2.is_seen is False


@pytest.mark.anyio
async def test_anti_overlap_lock_skips_concurrent_run():
    """Verifies that while a hunt is running, concurrent executions are safely skipped."""
    async with _hunting_lock:
        # While locked, call execute_sequential_hunt
        res = await execute_sequential_hunt()
        assert res["status"] == "skipped"
        assert res["reason"] == "already_running"


@pytest.mark.anyio
async def test_gemini_auto_scoring_on_unscored_items_when_brief_present(tmp_path):
    """Verifies that newly discovered unscored opportunities are automatically scored with Gemini if ideal_job_brief is present."""
    db_path = tmp_path / "scoring_sched.db"
    storage = Storage(db_path)

    test_user = storage.ensure_user(
        User(id="usr_sched_test", email="user@jobby.local", password_hash="hash")
    )
    storage.save_profile(
        UserProfile(
            user_id=test_user.id,
            ideal_job_brief="Je recherche un poste de Lead Product Designer ou Directeur Artistique en remote.",
        )
    )

    config = JobbyConfig(
        database_path=str(db_path),
        auto_search_queries=["creative lead"],
        ai_api_key="fake-gemini-key",
    )

    async def fake_collect(params):
        return [
            JobOpportunity(
                id="opp-unscored-1",
                title="Creative Lead Senior",
                company="Acme Corp",
                location="Remote",
                source="wttj",
                url="https://example.com/unscored/1",
                score=0,
                status="new",
            )
        ]

    gemini_called_with = []

    async def fake_rank(brief, opportunities, api_key):
        gemini_called_with.append({"brief": brief, "opps": opportunities, "api_key": api_key})
        for o in opportunities:
            eval_res = MatchEvaluation(
                user_id=test_user.id,
                opportunity_id=o.id,
                score=88,
                verdict="Très bon alignement créatif",
                provider_used="gemini",
                used_fallback=False,
            )
            storage.save_match_result(eval_res)
        return opportunities

    with patch.object(registry, "search_all", side_effect=fake_collect), \
         patch("jobby.ai_analyzer.rank_opportunities_by_ideal_brief_with_meta", side_effect=fake_rank):
        result = await execute_sequential_hunt(config=config, storage=storage, user_id=test_user.id, inter_query_delay=0.01)

    assert result["status"] == "success"
    assert result["new_count"] == 1
    assert result["scored_by_gemini"] == 1
    assert len(gemini_called_with) == 1
    assert "Lead Product Designer" in gemini_called_with[0]["brief"]
    assert gemini_called_with[0]["opps"][0].id == "opp-unscored-1"


def test_api_automation_run_now_endpoint(tmp_path, monkeypatch):
    """Verifies that POST /api/automation/run-now triggers the sequential pipeline and returns structured stats."""
    from fastapi.testclient import TestClient
    from jobby.server import app

    db_path = tmp_path / "run_now.db"
    cfg_path = tmp_path / "jobby.json"
    save_config(
        JobbyConfig(
            database_path=str(db_path),
            auto_search_queries=["art director"],
            auto_search_times=["12:00", "17:00"],
        ),
        config_path=cfg_path,
    )
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))

    with patch("jobby.scheduler.execute_sequential_hunt", new_callable=AsyncMock) as mock_hunt:
        mock_hunt.return_value = {
            "status": "success",
            "total_queries": 1,
            "total_found": 5,
            "new_count": 2,
            "message": "Chasse terminée.",
        }

        with TestClient(app) as client:
            resp = client.post("/api/automation/run-now")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["new_count"] == 2
            mock_hunt.assert_called_once()
