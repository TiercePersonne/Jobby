import sqlite3
import tempfile
from pathlib import Path
from jobby.models import JobOpportunity, OpportunityType
from jobby.storage import Storage


def test_storage_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_jobby.db"
        storage = Storage(db_path=db_path)

        opp = JobOpportunity(
            source="linkedin",
            title="AI Engineer",
            company="Startup Lab",
            url="https://linkedin.com/jobs/view/999",
            opportunity_type=OpportunityType.OFFER,
            score=85.0,
            score_reasons=["Matching Python & LLM stack"],
            tags=["Python", "LLM", "Agent"],
        )

        is_new, saved = storage.upsert_opportunity(opp)
        assert is_new is True
        assert saved.id == opp.id

        # Re-upsert (update)
        opp.description = "Updated description"
        is_new, updated = storage.upsert_opportunity(opp)
        assert is_new is False

        # Retrieval
        retrieved = storage.get_opportunity(opp.id)
        assert retrieved is not None
        assert retrieved.title == "AI Engineer"
        assert retrieved.score == 85.0
        assert "LLM" in retrieved.tags

        # Update status
        assert storage.update_status(opp.id, "applied") is True
        assert storage.get_opportunity(opp.id).status == "applied"

        # List opportunities
        items = storage.list_opportunities(status="applied")
        assert len(items) == 1
        assert items[0].id == opp.id

        # Stats
        stats = storage.get_stats()
        assert stats["total"] == 1
        assert stats["by_source"]["linkedin"] == 1
        assert stats["by_status"]["applied"] == 1


def test_is_seen_migration_backfill_and_idempotency(tmp_path):
    db_path = tmp_path / "legacy_jobby.db"

    # Seed legacy SQLite schema without is_seen column
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE opportunities (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            opportunity_type TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT,
            location TEXT,
            remote_policy TEXT,
            salary_raw TEXT,
            url TEXT NOT NULL,
            author_name TEXT,
            author_profile_url TEXT,
            author_title TEXT,
            description TEXT,
            published_at TEXT,
            discovered_at TEXT NOT NULL,
            score REAL,
            score_reasons TEXT,
            tags TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            dedup_key TEXT NOT NULL,
            raw_metadata TEXT,
            post_intent TEXT,
            ai_summary TEXT
        )
    """)
    for opp_id, status in [
        ("legacy-new", "new"),
        ("legacy-kept", "kept"),
        ("legacy-saved", "saved"),
        ("legacy-archived", "archived"),
    ]:
        conn.execute(
            """
            INSERT INTO opportunities (
                id, source, opportunity_type, title, url, discovered_at, status, dedup_key
            ) VALUES (?, 'wttj', 'offer', ?, ?, '2026-11-10T10:00:00+00:00', ?, ?)
            """,
            (opp_id, f"Title {opp_id}", f"https://example.com/{opp_id}", status, f"comp:{opp_id}"),
        )
    conn.commit()
    conn.close()

    storage = Storage(db_path=db_path)
    with storage._connection() as verify_conn:
        cols = {r["name"] for r in verify_conn.execute("PRAGMA table_info(opportunities)").fetchall()}
        assert "is_seen" in cols

    assert storage.get_opportunity("legacy-new").is_seen is False
    assert storage.get_opportunity("legacy-kept").is_seen is True
    assert storage.get_opportunity("legacy-saved").is_seen is True
    assert storage.get_opportunity("legacy-archived").is_seen is True

    # Mark legacy-new as seen while keeping status == 'new', then re-run Storage() init (no-op)
    assert storage.mark_opportunity_seen("legacy-new", True) is True
    storage_again = Storage(db_path=db_path)
    assert storage_again.get_opportunity("legacy-new").is_seen is True
    assert storage_again.get_opportunity("legacy-new").status == "new"


def test_upsert_preserves_is_seen_and_mark_all_seen(tmp_path):
    db_path = tmp_path / "seen_upsert.db"
    storage = Storage(db_path=db_path)

    opp1 = JobOpportunity(
        id="opp-seen-1",
        source="linkedin",
        title="Backend Python",
        company="Acme",
        url="https://linkedin.com/jobs/view/1001",
    )
    opp2 = JobOpportunity(
        id="opp-seen-2",
        source="wttj",
        title="Product Designer",
        company="Studio",
        url="https://wttj.co/jobs/1002",
    )

    is_new1, saved1 = storage.upsert_opportunity(opp1)
    assert is_new1 is True
    assert saved1.is_seen is False
    storage.upsert_opportunity(opp2)

    assert storage.mark_opportunity_seen("missing-id") is False
    assert storage.mark_opportunity_seen("opp-seen-1", True) is True
    assert storage.get_opportunity("opp-seen-1").is_seen is True

    # Re-scrape same offer with fresh JobOpportunity(is_seen=False) -> preserves is_seen=True
    rescrape = JobOpportunity(
        id="opp-seen-1",
        source="linkedin",
        title="Backend Python Updated",
        company="Acme",
        url="https://linkedin.com/jobs/view/1001",
        description="Updated description",
        is_seen=False,
    )
    is_new_rescrape, updated_rescrape = storage.upsert_opportunity(rescrape)
    assert is_new_rescrape is False
    assert updated_rescrape.is_seen is True
    assert storage.get_opportunity("opp-seen-1").is_seen is True
    assert storage.get_opportunity("opp-seen-1").title == "Backend Python Updated"

    # Mark all remaining unseen opportunities as seen
    updated_count = storage.mark_all_opportunities_seen()
    assert updated_count == 1
    assert storage.get_opportunity("opp-seen-2").is_seen is True
    assert storage.mark_all_opportunities_seen() == 0


def test_update_status_sets_is_seen_when_leaving_new(tmp_path):
    db_path = tmp_path / "status_seen.db"
    storage = Storage(db_path=db_path)

    opp = JobOpportunity(
        id="opp-triage-1",
        source="wttj",
        title="Backend Developer",
        url="https://wttj.co/jobs/triage-1",
    )
    storage.upsert_opportunity(opp)
    assert storage.get_opportunity(opp.id).is_seen is False

    assert storage.update_status(opp.id, "kept") is True
    updated = storage.get_opportunity(opp.id)
    assert updated.status == "kept"
    assert updated.is_seen is True

    # Resetting back to 'new' does not crash and updates status
    assert storage.update_status(opp.id, "new") is True
    assert storage.get_opportunity(opp.id).status == "new"


def test_list_opportunities_status_whitespace_stats_and_applied_at(tmp_path):
    from jobby.models import ApplicationStatus, User

    db_path = tmp_path / "storage_fixes.db"
    storage = Storage(db_path=db_path)

    storage.upsert_opportunity(
        JobOpportunity(
            id="st-new",
            source="wttj",
            title="New Role",
            url="https://wttj.co/jobs/st-new",
            status="new",
        )
    )
    with storage._connection() as conn:
        conn.execute(
            """
            INSERT INTO opportunities (
                id, source, opportunity_type, title, url, discovered_at, status, is_seen, dedup_key
            ) VALUES
                ('st-fav', 'wttj', 'offer', 'Fav Role', 'https://wttj.co/jobs/fav', '2026-11-11T10:00:00+00:00', 'favorite', 1, 'c:fav'),
                ('st-sav', 'wttj', 'offer', 'Sav Role', 'https://wttj.co/jobs/sav', '2026-11-11T10:00:00+00:00', 'saved', 1, 'c:sav'),
                ('st-arch', 'wttj', 'offer', 'Arch Role', 'https://wttj.co/jobs/arch', '2026-11-11T10:00:00+00:00', '  ARCHIVED  ', 1, 'c:arch')
            """
        )
        conn.commit()

    # Whitespace-only status filter should NOT be coerced into 'new'
    all_items = storage.list_opportunities(status="   ")
    assert {o.id for o in all_items} == {"st-new", "st-fav", "st-sav", "st-arch"}

    # Non-kept statuses still match case/whitespace-insensitively
    archived_items = storage.list_opportunities(status="archived")
    assert [o.id for o in archived_items] == ["st-arch"]

    # get_stats merges favorite + saved -> kept
    stats = storage.get_stats()
    assert stats["by_status"]["kept"] == 2
    assert "favorite" not in stats["by_status"]
    assert "saved" not in stats["by_status"]

    # sync_opportunity_to_application preserves existing applied_at & uses ISO UTC timestamps
    user = User(id="u1", email="u1@example.com", password_hash="x")
    storage.create_user(user)
    app1 = storage.sync_opportunity_to_application("st-fav", user.id, ApplicationStatus.APPLIED)
    assert app1 is not None and app1.applied_at is not None
    first_applied_at = app1.applied_at

    storage.sync_opportunity_to_application("st-fav", user.id, ApplicationStatus.INTERVIEW)
    app2 = storage.sync_opportunity_to_application("st-fav", user.id, ApplicationStatus.APPLIED)
    assert app2 is not None
    assert app2.applied_at == first_applied_at
    assert "T" in app2.updated_at.isoformat()


def test_upsert_merges_raw_metadata_and_ignores_corrupted_json(tmp_path):
    db_path = tmp_path / "bh1_raw_metadata.db"
    storage = Storage(db_path=db_path)

    # 1. Insert opportunity with initial provider metadata
    opp = JobOpportunity(
        id="opp-bh1",
        source="wttj",
        title="Lead Product Designer",
        company="Studio Warm",
        url="https://wttj.co/jobs/bh1",
        raw_metadata={"contract_type": "CDI", "source_id": "w1"},
    )
    is_new, _ = storage.upsert_opportunity(opp)
    assert is_new is True

    # 2. Simulate rank-by-brief saving ai_match via upsert_opportunity (ON CONFLICT)
    opp.score = 92.0
    opp.score_reasons = ["Match idéal"]
    opp.raw_metadata["ai_match"] = {"id": "opp-bh1", "score": 92, "verdict": "Match idéal"}
    is_new2, updated = storage.upsert_opportunity(opp)
    assert is_new2 is False
    assert updated.score == 92.0
    assert updated.score_reasons == ["Match idéal"]
    assert updated.raw_metadata["ai_match"]["score"] == 92

    persisted = storage.get_opportunity("opp-bh1")
    assert persisted is not None
    assert persisted.score == 92.0
    assert persisted.score_reasons == ["Match idéal"]
    assert persisted.raw_metadata["contract_type"] == "CDI"
    assert persisted.raw_metadata["ai_match"] == {"id": "opp-bh1", "score": 92, "verdict": "Match idéal"}

    # 3. Simulate re-hunt upserting with empty raw_metadata and rule-based score -> ai_match, score & score_reasons preserved (BH-2)
    rehunt_empty = JobOpportunity(
        id="opp-bh1",
        source="wttj",
        title="Lead Product Designer (Updated)",
        company="Studio Warm",
        url="https://wttj.co/jobs/bh1",
        score=35.0,
        score_reasons=["Keyword rule"],
        raw_metadata={},
    )
    _, after_empty = storage.upsert_opportunity(rehunt_empty)
    assert after_empty.score == 92.0
    assert after_empty.score_reasons == ["Match idéal"]
    assert after_empty.raw_metadata["ai_match"]["score"] == 92
    assert after_empty.raw_metadata["contract_type"] == "CDI"
    persisted_after_empty = storage.get_opportunity("opp-bh1")
    assert persisted_after_empty.score == 92.0
    assert persisted_after_empty.score_reasons == ["Match idéal"]

    # 4. Re-hunt merging new keys without ai_match -> preserves ai_match, score & score_reasons and adds new keys
    rehunt_new_keys = JobOpportunity(
        id="opp-bh1",
        source="wttj",
        title="Lead Product Designer (Updated 2)",
        company="Studio Warm",
        url="https://wttj.co/jobs/bh1",
        score=42.0,
        score_reasons=["Rule 2"],
        raw_metadata={"salary_max": 75000},
    )
    _, after_new_keys = storage.upsert_opportunity(rehunt_new_keys)
    assert after_new_keys.score == 92.0
    assert after_new_keys.score_reasons == ["Match idéal"]
    persisted_merged = storage.get_opportunity("opp-bh1")
    assert persisted_merged.score == 92.0
    assert persisted_merged.score_reasons == ["Match idéal"]
    assert persisted_merged.raw_metadata["ai_match"]["score"] == 92
    assert persisted_merged.raw_metadata["contract_type"] == "CDI"
    assert persisted_merged.raw_metadata["salary_max"] == 75000

    # 4b. Re-scoring with incoming ai_match DOES update score, score_reasons & ai_match
    rescore_ai = JobOpportunity(
        id="opp-bh1",
        source="wttj",
        title="Lead Product Designer (Updated 2)",
        company="Studio Warm",
        url="https://wttj.co/jobs/bh1",
        score=88.0,
        score_reasons=["Updated AI verdict"],
        raw_metadata={"ai_match": {"id": "opp-bh1", "score": 88, "verdict": "Updated AI verdict"}},
    )
    _, after_rescore = storage.upsert_opportunity(rescore_ai)
    assert after_rescore.score == 88.0
    assert after_rescore.score_reasons == ["Updated AI verdict"]
    assert after_rescore.raw_metadata["ai_match"]["score"] == 88
    assert after_rescore.raw_metadata["contract_type"] == "CDI"

    # 5. Corrupted & non-dict raw_metadata JSON in SQLite are safely ignored (BH-10)
    assert Storage._parse_raw_metadata("[]") == {}
    assert Storage._parse_raw_metadata("123") == {}
    assert Storage._parse_raw_metadata("null") == {}
    assert Storage._parse_raw_metadata("{broken-json") == {}

    for invalid_raw in ("{broken-json", "[]", "123", "null"):
        with storage._connection() as conn:
            conn.execute("UPDATE opportunities SET raw_metadata = ? WHERE id = ?", (invalid_raw, "opp-bh1"))
            conn.commit()
        assert storage.get_opportunity("opp-bh1").raw_metadata == {}

    rehunt_recovery = JobOpportunity(
        id="opp-bh1",
        source="wttj",
        title="Lead Product Designer Recovered",
        company="Studio Warm",
        url="https://wttj.co/jobs/bh1",
        raw_metadata={"recovered": True},
    )
    _, recovered = storage.upsert_opportunity(rehunt_recovery)
    assert recovered.raw_metadata == {"recovered": True}
    assert storage.get_opportunity("opp-bh1").raw_metadata == {"recovered": True}


def test_user_profiles_ideal_job_brief_migration_and_rollback(tmp_path, monkeypatch):
    from jobby.models import User, UserProfile

    db_path = tmp_path / "legacy_profiles.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE user_profiles (
            user_id TEXT PRIMARY KEY,
            cv_filename TEXT,
            cv_text TEXT,
            portfolio_url TEXT,
            github_url TEXT,
            linkedin_url TEXT,
            bio TEXT,
            writing_sample TEXT,
            writing_tone TEXT,
            target_roles TEXT,
            skills TEXT,
            min_salary INTEGER,
            remote_preference TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "INSERT INTO users (id, email, full_name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
        ("u-legacy", "legacy@example.com", "Legacy User", "hash", "2026-11-10T10:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO user_profiles (user_id, bio, updated_at) VALUES (?, ?, ?)",
        ("u-legacy", "Existing bio", "2026-11-10T10:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    # Migration adds ideal_job_brief idempotently without losing existing rows
    storage = Storage(db_path=db_path)
    with storage._connection() as verify_conn:
        cols = {r["name"] for r in verify_conn.execute("PRAGMA table_info(user_profiles)").fetchall()}
        assert "ideal_job_brief" in cols

    loaded = storage.get_profile("u-legacy")
    assert loaded is not None
    assert loaded.bio == "Existing bio"
    assert loaded.ideal_job_brief is None

    loaded.ideal_job_brief = "Lead Product Designer Full Remote"
    prev_updated_at = loaded.updated_at
    storage.save_profile(loaded)
    assert loaded.updated_at >= prev_updated_at

    # Atomic ensure_user / ensure_profile do not raise IntegrityError or overwrite existing data
    ensured_user = storage.ensure_user(
        User(id="u-other-id", email="legacy@example.com", full_name="Other", password_hash="y")
    )
    assert ensured_user.id == "u-legacy"
    ensured_profile = storage.ensure_profile(
        UserProfile(user_id="u-legacy", bio="Overwrite attempt", ideal_job_brief="Overwrite brief")
    )
    assert ensured_profile.bio == "Existing bio"
    assert ensured_profile.ideal_job_brief == "Lead Product Designer Full Remote"

    # Re-initializing Storage on migrated DB is an idempotent no-op
    storage_reinit = Storage(db_path=db_path)
    reloaded = storage_reinit.get_profile("u-legacy")
    assert reloaded is not None
    assert reloaded.ideal_job_brief == "Lead Product Designer Full Remote"

    # DDL failure triggers rollback and propagates error
    rollback_db = tmp_path / "legacy_rollback.db"
    rconn = sqlite3.connect(rollback_db)
    rconn.execute("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    rconn.execute("""
        CREATE TABLE user_profiles (
            user_id TEXT PRIMARY KEY,
            bio TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    rconn.commit()
    rconn.close()

    rollback_called = {"count": 0}
    real_connect = sqlite3.connect

    class ConnProxy:
        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)

        def execute(self, sql, *args, **kwargs):
            if "ALTER TABLE user_profiles ADD COLUMN ideal_job_brief" in sql:
                raise sqlite3.OperationalError("simulated DDL failure")
            return self._inner.execute(sql, *args, **kwargs)

        def rollback(self):
            rollback_called["count"] += 1
            return self._inner.rollback()

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __setattr__(self, name, value):
            return setattr(self._inner, name, value)

    monkeypatch.setattr("jobby.storage.sqlite3.connect", lambda *a, **kw: ConnProxy(real_connect(*a, **kw)))
    import pytest
    with pytest.raises(sqlite3.OperationalError, match="simulated DDL failure"):
        Storage(db_path=rollback_db)
    assert rollback_called["count"] >= 1

    verify_rb = real_connect(rollback_db)
    verify_rb.row_factory = sqlite3.Row
    rb_cols = {r["name"] for r in verify_rb.execute("PRAGMA table_info(user_profiles)").fetchall()}
    verify_rb.close()
    assert "ideal_job_brief" not in rb_cols


def test_match_evaluations_provenance_migration_and_rollback(tmp_path, monkeypatch):
    import json
    import pytest
    from jobby.models import MatchEvaluation

    db_path = tmp_path / "legacy_matches.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE match_evaluations (
            user_id TEXT NOT NULL,
            opportunity_id TEXT NOT NULL,
            score REAL NOT NULL,
            verdict TEXT NOT NULL,
            strengths TEXT,
            weaknesses TEXT,
            pitch TEXT,
            cover_letter TEXT,
            PRIMARY KEY(user_id, opportunity_id)
        )
    """)
    conn.execute(
        """
        INSERT INTO match_evaluations (
            user_id, opportunity_id, score, verdict, strengths, weaknesses, pitch, cover_letter
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "u-1",
            "opp-legacy",
            82.5,
            "Bon alignement legacy",
            json.dumps(["Python"]),
            json.dumps(["Docker"]),
            "Pitch legacy",
            "Lettre legacy",
        ),
    )
    conn.commit()
    conn.close()

    storage = Storage(db_path=db_path)
    with storage._connection() as verify_conn:
        cols = {r["name"] for r in verify_conn.execute("PRAGMA table_info(match_evaluations)").fetchall()}
        assert {
            "cover_letter",
            "provider_used",
            "used_fallback",
            "fallback_reason",
            "cover_letter_provider_used",
            "cover_letter_used_fallback",
            "cover_letter_fallback_reason",
        }.issubset(cols)

    legacy = storage.get_match_evaluation("u-1", "opp-legacy")
    assert legacy is not None
    assert legacy.score == 82.5
    assert legacy.verdict == "Bon alignement legacy"
    assert legacy.strengths == ["Python"]
    assert legacy.weaknesses == ["Docker"]
    assert legacy.pitch == "Pitch legacy"
    assert legacy.cover_letter == "Lettre legacy"
    assert legacy.provider_used is None
    assert legacy.used_fallback is False
    assert legacy.fallback_reason is None

    # Save Gemini and heuristic evaluations & round-trip via get_match_evaluation and get_user_matches
    gemini_eval = MatchEvaluation(
        user_id="u-1",
        opportunity_id="opp-gemini",
        score=94.0,
        verdict="Match idéal Gemini",
        strengths=["FastAPI", "LLM"],
        weaknesses=[],
        pitch="Pitch Gemini",
        cover_letter="Lettre Gemini",
        provider_used="gemini",
        used_fallback=False,
        fallback_reason=None,
    )
    heuristic_eval = MatchEvaluation(
        user_id="u-1",
        opportunity_id="opp-heur",
        score=64.0,
        verdict="Match heuristique",
        pitch="Pitch heuristique",
        provider_used="heuristic",
        used_fallback=True,
        fallback_reason="Timeout Gemini",
    )
    storage.save_match_evaluation(gemini_eval)
    storage.save_match_evaluation(heuristic_eval)

    all_matches = storage.get_user_matches("u-1")
    assert set(all_matches.keys()) == {"opp-legacy", "opp-gemini", "opp-heur"}
    assert all_matches["opp-gemini"].provider_used == "gemini"
    assert all_matches["opp-gemini"].used_fallback is False
    assert all_matches["opp-gemini"].fallback_reason is None
    assert all_matches["opp-heur"].provider_used == "heuristic"
    assert all_matches["opp-heur"].used_fallback is True
    assert all_matches["opp-heur"].fallback_reason == "Timeout Gemini"

    # Re-initializing Storage on migrated DB is an idempotent no-op
    storage_again = Storage(db_path=db_path)
    assert storage_again.get_match_evaluation("u-1", "opp-gemini").provider_used == "gemini"
    assert storage_again.get_match_evaluation("u-1", "opp-legacy").provider_used is None

    # Rollback on DDL failure inside BEGIN IMMEDIATE
    rollback_db = tmp_path / "legacy_matches_rollback.db"
    rconn = sqlite3.connect(rollback_db)
    rconn.execute("""
        CREATE TABLE match_evaluations (
            user_id TEXT NOT NULL,
            opportunity_id TEXT NOT NULL,
            score REAL NOT NULL,
            verdict TEXT NOT NULL,
            strengths TEXT,
            weaknesses TEXT,
            pitch TEXT,
            cover_letter TEXT,
            PRIMARY KEY(user_id, opportunity_id)
        )
    """)
    rconn.commit()
    rconn.close()

    rollback_called = {"count": 0}
    real_connect = sqlite3.connect

    class MatchConnProxy:
        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)

        def execute(self, sql, *args, **kwargs):
            if "ALTER TABLE match_evaluations ADD COLUMN fallback_reason" in sql:
                raise sqlite3.OperationalError("simulated match_evaluations DDL failure")
            return self._inner.execute(sql, *args, **kwargs)

        def rollback(self):
            rollback_called["count"] += 1
            return self._inner.rollback()

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __setattr__(self, name, value):
            return setattr(self._inner, name, value)

    monkeypatch.setattr("jobby.storage.sqlite3.connect", lambda *a, **kw: MatchConnProxy(real_connect(*a, **kw)))
    with pytest.raises(sqlite3.OperationalError, match="simulated match_evaluations DDL failure"):
        Storage(db_path=rollback_db)
    assert rollback_called["count"] >= 1

    verify_rb = real_connect(rollback_db)
    verify_rb.row_factory = sqlite3.Row
    rb_cols = {r["name"] for r in verify_rb.execute("PRAGMA table_info(match_evaluations)").fetchall()}
    verify_rb.close()
    assert not {"provider_used", "used_fallback", "fallback_reason"}.intersection(rb_cols)


def test_pre_cover_letter_migration_and_concurrent_non_clobbering_saves(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from jobby.models import MatchEvaluation

    # 1. Pre-cover_letter schema (lacks cover_letter AND all 6 provenance columns)
    db_path = tmp_path / "pre_cover_letter.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE match_evaluations (
            user_id TEXT NOT NULL,
            opportunity_id TEXT NOT NULL,
            score REAL NOT NULL,
            verdict TEXT NOT NULL,
            strengths TEXT,
            weaknesses TEXT,
            pitch TEXT,
            PRIMARY KEY(user_id, opportunity_id)
        )
    """)
    conn.execute(
        "INSERT INTO match_evaluations (user_id, opportunity_id, score, verdict, pitch) VALUES (?, ?, ?, ?, ?)",
        ("u-pre", "opp-pre", 76.0, "Legacy Pre", "Pitch pre"),
    )
    conn.commit()
    conn.close()

    storage = Storage(db_path=db_path)
    with storage._connection() as verify_conn:
        cols = {r["name"] for r in verify_conn.execute("PRAGMA table_info(match_evaluations)").fetchall()}
        assert {
            "cover_letter",
            "provider_used",
            "used_fallback",
            "fallback_reason",
            "cover_letter_provider_used",
            "cover_letter_used_fallback",
            "cover_letter_fallback_reason",
        }.issubset(cols)

    pre_row = storage.get_match_evaluation("u-pre", "opp-pre")
    assert pre_row is not None
    assert pre_row.score == 76.0
    assert pre_row.cover_letter == ""
    assert pre_row.provider_used is None
    assert pre_row.cover_letter_provider_used is None

    # 2. Non-clobbering sequential saves: match -> cover_letter -> match fallback
    saved_m = storage.save_match_result(
        MatchEvaluation(
            user_id="u-1",
            opportunity_id="opp-1",
            score=91.0,
            verdict="Gemini Match",
            strengths=["Python"],
            weaknesses=[],
            pitch="Pitch Gemini",
            provider_used="gemini",
            used_fallback=False,
            fallback_reason=None,
        )
    )
    assert saved_m.score == 91.0
    assert saved_m.provider_used == "gemini"

    saved_cl = storage.save_cover_letter_result(
        user_id="u-1",
        opportunity_id="opp-1",
        cover_letter="Lettre heuristique",
        provider_used="heuristic",
        used_fallback=True,
        fallback_reason="Timeout Gemini",
    )
    # Match fields & match provenance MUST remain intact!
    assert saved_cl.score == 91.0
    assert saved_cl.verdict == "Gemini Match"
    assert saved_cl.provider_used == "gemini"
    assert saved_cl.used_fallback is False
    assert saved_cl.fallback_reason is None
    assert saved_cl.cover_letter == "Lettre heuristique"
    assert saved_cl.cover_letter_provider_used == "heuristic"
    assert saved_cl.cover_letter_used_fallback is True
    assert saved_cl.cover_letter_fallback_reason == "Timeout Gemini"

    # Upgrading cover_letter to Gemini does NOT touch match; updating match does NOT touch cover_letter!
    storage.save_cover_letter_result(
        user_id="u-1",
        opportunity_id="opp-1",
        cover_letter="Lettre Gemini",
        provider_used="gemini",
        used_fallback=False,
        fallback_reason=None,
    )
    after_match_fb = storage.save_match_result(
        MatchEvaluation(
            user_id="u-1",
            opportunity_id="opp-1",
            score=62.0,
            verdict="Fallback Match",
            pitch="Pitch fb",
            provider_used="heuristic",
            used_fallback=True,
            fallback_reason="HTTP 503",
        )
    )
    assert after_match_fb.score == 62.0
    assert after_match_fb.provider_used == "heuristic"
    assert after_match_fb.cover_letter == "Lettre Gemini"
    assert after_match_fb.cover_letter_provider_used == "gemini"
    assert after_match_fb.cover_letter_used_fallback is False

    # 3. Concurrent saves via ThreadPoolExecutor preserve both match and cover_letter cleanly
    def write_match():
        return storage.save_match_result(
            MatchEvaluation(
                user_id="u-conc",
                opportunity_id="opp-conc",
                score=88.0,
                verdict="Concurrent Match",
                strengths=["FastAPI"],
                pitch="Pitch conc",
                provider_used="gemini",
                used_fallback=False,
            )
        )

    def write_letter():
        return storage.save_cover_letter_result(
            user_id="u-conc",
            opportunity_id="opp-conc",
            cover_letter="Concurrent Letter",
            provider_used="gemini",
            used_fallback=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(write_match)
        f2 = pool.submit(write_letter)
        f1.result()
        f2.result()

    final_conc = storage.get_match_evaluation("u-conc", "opp-conc")
    assert final_conc is not None
    assert final_conc.score == 88.0
    assert final_conc.verdict == "Concurrent Match"
    assert final_conc.provider_used == "gemini"
    assert final_conc.cover_letter == "Concurrent Letter"
    assert final_conc.cover_letter_provider_used == "gemini"
    assert final_conc.cover_letter_used_fallback is False
