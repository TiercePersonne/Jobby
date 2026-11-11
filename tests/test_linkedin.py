import pytest
from jobby.models import OpportunityType, SearchParams
from jobby.providers.linkedin import LinkedInProvider

SAMPLE_JOB_CARD_HTML = """
<ul>
  <li>
    <div class="base-card">
      <a class="base-card__full-link" href="https://fr.linkedin.com/jobs/view/lead-python-developer-12345">
        <h3 class="base-search-card__title">Lead Python Developer</h3>
      </a>
      <h4 class="base-search-card__subtitle">
        <a href="https://fr.linkedin.com/company/acme">Acme Corp</a>
      </h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">Paris, Île-de-France, France</span>
        <time class="job-search-card__listdate" datetime="2026-11-10">Il y a 1 jour</time>
        <span class="job-search-card__salary-info">65 000 € - 80 000 € / an</span>
      </div>
    </div>
  </li>
</ul>
"""


def test_parse_linkedin_job_cards():
    provider = LinkedInProvider()
    cards = provider._parse_job_cards(SAMPLE_JOB_CARD_HTML)

    assert len(cards) == 1
    job = cards[0]
    assert job.source == "linkedin"
    assert job.opportunity_type == OpportunityType.OFFER
    assert job.title == "Lead Python Developer"
    assert job.company == "Acme Corp"
    assert job.location == "Paris, Île-de-France, France"
    assert job.salary_raw == "65 000 € - 80 000 € / an"
    assert "lead-python-developer" in job.url


@pytest.mark.anyio
async def test_linkedin_search_posts_unauthenticated_returns_empty():
    provider = LinkedInProvider()
    params = SearchParams(keywords=["Python", "FastAPI"], location="Paris")
    posts = await provider._search_social_posts(params)

    # Must NOT return fake feed link, returns empty if unauthenticated
    assert len(posts) == 0


def test_parse_voyager_posts_strict_keyword_filtering():
    provider = LinkedInProvider(li_at="test_token")
    mock_payload = {
        "included": [
            # Post 1: Recruiter hiring for Python in commentary -> MATCH
            {
                "$type": "com.linkedin.voyager.feed.Update",
                "urn": "urn:li:activity:789123456",
                "commentary": {
                    "text": {
                        "text": "On recrute un Lead Dev Python pour rejoindre notre équipe à Paris !\nContactez-moi en MP."
                    }
                },
                "actor": {
                    "name": {"text": "Claire Martin"},
                    "subDescription": {"text": "Tech Recruiter @ ScaleUp"},
                    "navigationUrl": "https://www.linkedin.com/in/claire-martin",
                },
            },
            # Post 2: Author title mentions Python, but post is about marketing/coffee -> MUST BE EXCLUDED!
            {
                "$type": "com.linkedin.voyager.feed.Update",
                "urn": "urn:li:activity:999888777",
                "commentary": {
                    "text": {
                        "text": "Superbe conférence sur le design hier avec l'équipe ! Très inspirant."
                    }
                },
                "actor": {
                    "name": {"text": "Jean Dupont"},
                    "subDescription": {"text": "Senior Python Architect @ BigCorp"},
                    "navigationUrl": "https://www.linkedin.com/in/jean-dupont",
                },
            }
        ]
    }
    params = SearchParams(keywords=["Python"])
    posts = provider._parse_voyager_posts(mock_payload, params=params)
    
    # Only Post 1 should be matched, Post 2 must be excluded despite author headline
    assert len(posts) == 1
    post = posts[0]
    assert post.author_name == "Claire Martin"
    assert post.author_title == "Tech Recruiter @ ScaleUp"
    assert post.url == "https://www.linkedin.com/feed/update/urn:li:activity:789123456"
    assert "On recrute un Lead Dev Python" in post.description
