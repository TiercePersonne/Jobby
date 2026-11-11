import pytest
from jobby.config import JobbyConfig
from jobby.engine import DeduplicationEngine, ScoringEngine
from jobby.models import JobOpportunity, OpportunityType


def test_deduplication_engine():
    opp_linkedin = JobOpportunity(
        source="linkedin",
        title="Senior Python Developer (H/F)",
        company="Datadog",
        url="https://linkedin.com/jobs/view/1",
        opportunity_type=OpportunityType.OFFER,
        tags=["Python", "Cloud"],
        description="Short description",
    )
    opp_hellowork = JobOpportunity(
        source="hellowork",
        title="Senior Python Developer",
        company="Datadog",
        url="https://hellowork.com/job/2",
        opportunity_type=OpportunityType.OFFER,
        tags=["Datadog", "Backend"],
        description="A much longer detailed job description from HelloWork",
    )

    merged = DeduplicationEngine.deduplicate([opp_linkedin, opp_hellowork])
    assert len(merged) == 1
    item = merged[0]
    assert "Cloud" in item.tags
    assert "Backend" in item.tags
    assert "A much longer" in item.description
    assert "linkedin" in item.raw_metadata["cross_sources"]
    assert "hellowork" in item.raw_metadata["cross_sources"]


def test_scoring_engine_high_match():
    config = JobbyConfig(
        target_roles=["Python", "AI Engineer"],
        skills_keywords=["Python", "Docker", "LLM", "FastAPI"],
        preferred_locations=["Paris"],
        negative_keywords=["Stage", "Junior"],
    )
    engine = ScoringEngine(config)

    opp = JobOpportunity(
        source="linkedin",
        title="Senior AI Engineer Python",
        company="Mistral AI",
        location="Paris, France",
        url="https://linkedin.com/jobs/view/mistral-1",
        description="We are building state of the art LLM agents with Python, FastAPI and Docker.",
        opportunity_type=OpportunityType.OFFER,
    )

    score, reasons = engine.score_opportunity(opp)
    assert score >= 70.0
    assert any("Cible métier" in r for r in reasons)
    assert any("Compétences" in r for r in reasons)


def test_scoring_engine_negative_penalty():
    config = JobbyConfig(
        target_roles=["Python"],
        negative_keywords=["Stage"],
    )
    engine = ScoringEngine(config)

    opp = JobOpportunity(
        source="linkedin",
        title="Stage Python Developer",
        company="Agency",
        url="https://linkedin.com/jobs/view/stage-1",
        opportunity_type=OpportunityType.OFFER,
    )

    score, reasons = engine.score_opportunity(opp)
    assert any("indésirables" in r for r in reasons)
    assert score < 40.0
