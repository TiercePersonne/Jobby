import tempfile
from pathlib import Path
from jobby.models import JobOpportunity, OpportunityType
from jobby.reporter import Reporter


def test_markdown_digest_generation():
    with tempfile.TemporaryDirectory() as tmpdir:
        output_file = Path(tmpdir) / "digest.md"
        opps = [
            JobOpportunity(
                source="linkedin",
                title="Lead AI Engineer",
                company="TechCorp",
                location="Paris",
                url="https://linkedin.com/jobs/view/1",
                opportunity_type=OpportunityType.OFFER,
                score=85.0,
                score_reasons=["Matching Target Role", "Location match"],
            ),
            JobOpportunity(
                source="linkedin",
                title="Post Recruteur: Recherche Dev Python",
                url="https://linkedin.com/feed/update/1",
                opportunity_type=OpportunityType.POST,
                author_name="Jean Dupont",
                author_title="Lead Tech Recruiter",
                score=75.0,
                description="Nous recrutons activement un développeur senior !",
            ),
        ]

        content = Reporter.generate_markdown_digest(opps, output_path=output_file)
        assert output_file.exists()
        assert "Jobby Digest" in content
        assert "Opportunités Recommandées" in content
        assert "Jean Dupont" in content
        assert "Lead AI Engineer" in content
