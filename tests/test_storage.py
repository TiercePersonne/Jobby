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
