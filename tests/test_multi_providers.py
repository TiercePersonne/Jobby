import pytest
from jobby.models import OpportunityType, SearchParams
from jobby.providers.hellowork import HelloWorkProvider
from jobby.providers.wttj import WTTJProvider
from jobby.providers.jobbygather import JobbyGatherProvider

SAMPLE_HELLOWORK_HTML = """
<div>
  <ul>
    <li>
      <article data-cy="job-card">
        <h3><a href="/fr-fr/emplois/999888.html">Développeur Python Senior H/F</a></h3>
        <span class="company">TechInnov</span>
        <span class="location">Lyon - 69</span>
        <p>CDI | Télétravail partiel</p>
      </article>
    </li>
  </ul>
</div>
"""

SAMPLE_WTTJ_HITS = [
    {
        "name": "Senior Backend Engineer Python",
        "organization": {"name": "Doctolib", "slug": "doctolib"},
        "slug": "senior-backend-engineer-python",
        "offices": [{"city": "Nantes", "country": "France"}],
        "remote": "fulltime",
        "contract_type": "full_time",
        "salary_minimum": 60,
        "salary_maximum": 75,
        "summary": "Join our platform team to scale our healthcare services.",
    }
]


def test_hellowork_parser():
    provider = HelloWorkProvider()
    jobs = provider._parse_html(SAMPLE_HELLOWORK_HTML)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "hellowork"
    assert "Développeur Python" in job.title
    assert "TechInnov" in job.company
    assert "Lyon" in job.location
    assert "CDI" in job.tags
    assert "https://www.hellowork.com/fr-fr/emplois/999888.html" == job.url


def test_wttj_parser():
    provider = WTTJProvider()
    jobs = provider._parse_hits(SAMPLE_WTTJ_HITS)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "wttj"
    assert job.title == "Senior Backend Engineer Python"
    assert job.company == "Doctolib"
    assert job.remote_policy == "full"
    assert job.salary_raw == "60k - 75k €"
    assert "https://www.welcometothejungle.com/fr/companies/doctolib/jobs/senior-backend-engineer-python" == job.url


def test_jobbygather_parser():
    provider = JobbyGatherProvider()
    raw = [
        {
            "source": "jobbygather",
            "opportunity_type": "post",
            "title": "Recrutement Dev IA",
            "author_name": "Marc",
            "description": "On recherche un dev IA freelance",
            "url": "https://example.com/post/1",
        }
    ]
    items = provider._parse_items(raw)
    assert len(items) == 1
    assert items[0].opportunity_type == OpportunityType.POST
    assert items[0].author_name == "Marc"
