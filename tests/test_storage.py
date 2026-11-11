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
