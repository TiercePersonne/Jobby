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
async def test_linkedin_search_posts_fallback():
    provider = LinkedInProvider()
    params = SearchParams(keywords=["Python", "FastAPI"], location="Paris")
    posts = await provider._search_posts_fallback(params)

    assert len(posts) == 1
    post = posts[0]
    assert post.opportunity_type == OpportunityType.POST
    assert "https://www.linkedin.com/search/results/content/" in post.url
    assert "recruiter-posts" in post.tags


def test_parse_voyager_posts_structure():
    provider = LinkedInProvider(li_at="test_token")
    mock_payload = {
        "included": [
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
            }
        ]
    }
    posts = provider._parse_voyager_posts(mock_payload)
    assert len(posts) == 1
    post = posts[0]
    assert post.author_name == "Claire Martin"
    assert post.author_title == "Tech Recruiter @ ScaleUp"
    assert "https://www.linkedin.com/feed/update/urn:li:activity:789123456" == post.url
    assert "On recrute un Lead Dev Python" in post.title
