import pytest
from jobby.models import JobOpportunity, OpportunityType, SearchParams


def test_job_opportunity_fingerprint():
    opp1 = JobOpportunity(
        source="linkedin",
        title="Senior Python Engineer",
        company="Antigravity Inc.",
        url="https://www.linkedin.com/jobs/view/123456?trackingId=xyz",
        opportunity_type=OpportunityType.OFFER,
    )
    opp2 = JobOpportunity(
        source="linkedin",
        title="Senior Python Engineer",
        company="Antigravity Inc.",
        url="https://www.linkedin.com/jobs/view/123456?differentParam=abc",
        opportunity_type=OpportunityType.OFFER,
    )
    # The clean URLs match, so fingerprints match
    assert opp1.id == opp2.id
    assert opp1.dedup_key == "antigravityinc:seniorpythonengineer"


def test_social_post_opportunity():
    post = JobOpportunity(
        source="linkedin",
        title="Recherche Développeur Backend Python Freelance",
        url="https://www.linkedin.com/feed/update/urn:li:activity:9876543210",
        opportunity_type=OpportunityType.POST,
        author_name="Alice Recruiter",
        author_title="Talent Acquisition Lead",
        description="On recrute un dev Python senior pour un projet de 6 mois !",
    )
    assert post.opportunity_type == OpportunityType.POST
    assert post.author_name == "Alice Recruiter"
    assert post.id != ""


def test_search_params_defaults():
    params = SearchParams(keywords=["python", "ai"])
    assert params.max_results == 25
    assert params.posted_within_days == 7
    assert params.include_posts is True
