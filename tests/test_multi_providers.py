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

SAMPLE_WTTJ_DATA = [
    {
        "name": "Senior Backend Engineer Python",
        "organization": {"name": "Doctolib", "slug": "doctolib"},
        "slug": "senior-backend-engineer-python",
        "office": {"city": "Nantes"},
        "remote": "fulltime",
        "contract_type": "full_time",
        "salary_min": 60000,
        "salary_max": 75000,
        "salary_currency": "€",
        "salary_period": "an",
        "company_summary": "Join our platform team to scale our healthcare services.",
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
    jobs = provider._parse_jobs(SAMPLE_WTTJ_DATA)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "wttj"
    assert job.title == "Senior Backend Engineer Python"
    assert job.company == "Doctolib"
    assert job.remote_policy == "full"
    assert "60000 - 75000" in job.salary_raw
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


def test_relevance_and_location_filters_for_creative_roles():
    from jobby.providers.glassdoor import _build_glassdoor_search_url
    from jobby.providers.relevance import is_location_compatible, is_relevant_job_offer

    kw = ["Brand Designer"]

    # Genuine creative & design roles must pass
    assert is_relevant_job_offer("Senior Brand Designer (H/F)", "", kw) is True
    assert is_relevant_job_offer("BALENCIAGA - Studio Graphic Designer (F/M)", "", kw) is True
    assert is_relevant_job_offer("CDI - Directeur artistique Senior (H/F)", "", kw) is True
    assert is_relevant_job_offer("DA / GRAPHISTE", "", kw) is True
    assert is_relevant_job_offer("Brand Design Manager", "", kw) is True

    # Off-topic roles and false friends must be rejected
    assert is_relevant_job_offer("Senior Developer", "", kw) is False
    assert is_relevant_job_offer("Développeur(se) Chaussures / Footwear Product Developer", "", kw) is False
    assert is_relevant_job_offer("Facilities Associate", "", kw) is False
    assert is_relevant_job_offer("RESPONSABLE RESSOURCES HUMAINES", "", kw) is False
    assert is_relevant_job_offer("Supply planner f/h", "", kw) is False
    assert is_relevant_job_offer("Stage (6 mois) – Assistant Chef de Marque (H/F)", "", kw) is False
    assert is_relevant_job_offer("Store Director M - F - Luxury Brand", "", kw) is False
    assert is_relevant_job_offer("Devenez Évaluateur de Marques de Luxe à Paris", "", kw) is False
    assert is_relevant_job_offer("Interior Designer | Intermediate", "", kw) is False
    assert is_relevant_job_offer("Freelance Fashion Designer", "", kw) is False
    assert is_relevant_job_offer("Vehicle Motion Development Engineer", "", kw) is False
    assert is_relevant_job_offer("Creative Project Manager", "", kw) is False
    assert is_relevant_job_offer("Executive Assistant du Directeur Artistique H/F", "", kw) is False

    # Location compatibility for French searches
    assert is_location_compatible("Paris (75)", None, "Paris") is True
    assert is_location_compatible("Paris 2e - 75", None, "Paris") is True
    assert is_location_compatible("Neuilly-sur-Seine (92)", None, "Paris") is True
    assert is_location_compatible("Télétravail", "full", "Paris") is True
    assert is_location_compatible("Denver", None, "Paris") is False
    assert is_location_compatible("New York", None, "Paris") is False
    assert is_location_compatible("Göteborg, Sweden", None, "Paris") is False
    assert is_location_compatible("Remote (United States)", "full", "Paris") is False

    # Canonical Glassdoor SRCH URL for Paris
    gd_url = _build_glassdoor_search_url("https://www.glassdoor.fr", "Brand Designer", "Paris")
    assert "paris-brand-designer-jobs-SRCH_IL.0,5_IC2881970_KO6,20.htm" in gd_url


def test_ai_designer_expansion_and_coverage_audit(tmp_path):
    from jobby.providers.relevance import (
        expand_creative_queries,
        get_search_audit_snapshot,
        is_location_compatible,
        is_relevant_job_offer,
        record_provider_audit_result,
        reset_search_audit,
    )
    from jobby.storage import Storage

    expanded = expand_creative_queries(["Brand Designer"], max_queries=5)
    assert "AI Designer" in expanded
    assert is_relevant_job_offer("AI Designer (H/F)", "", ["Brand Designer"]) is True
    assert is_relevant_job_offer("Creative AI Specialist", "", ["Brand Designer"]) is True

    reset_search_audit(["wttj", "glassdoor"])
    assert is_relevant_job_offer(
        "Senior Backend Developer",
        "",
        ["Brand Designer"],
        source="wttj",
        company="Acme",
        location="Paris",
        url="https://example.com/1",
    ) is False
    assert is_location_compatible(
        "Göteborg, Sweden",
        None,
        "Paris",
        source="glassdoor",
        title="Brand Designer",
        company="NordicCo",
        url="https://example.com/2",
    ) is False
    record_provider_audit_result("wttj", 12, status="ok")
    record_provider_audit_result("glassdoor", 0, status="cloudflare", note="Cloudflare VPS")

    snap = get_search_audit_snapshot()
    assert len(snap["filtered_offers"]) == 2

    storage = Storage(str(tmp_path / "audit_test.db"))
    storage.save_search_audit_snapshot(snap)
    cov = storage.get_coverage_audit()
    assert cov["total_filtered"] == 2
    gd_stat = next(s for s in cov["sources"] if s["source"] == "glassdoor")
    assert gd_stat["status"] == "cloudflare"

    first_id = cov["filtered_offers"][0]["id"]
    rescued = storage.rescue_filtered_opportunity(first_id)
    assert rescued is not None
    assert storage.get_coverage_audit()["total_filtered"] == 1


