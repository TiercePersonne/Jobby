import io
import uuid
import pytest
from fastapi.testclient import TestClient

from jobby.cv_parser import extract_text_from_cv
from jobby.models import JobOpportunity, OpportunityType
from jobby.server import app
from jobby.storage import Storage


@pytest.fixture
def client():
    return TestClient(app)


def test_cv_extraction_text():
    raw = b"Experience: Senior Python Developer at Acme. Skills: FastAPI, Docker, LLM."
    extracted = extract_text_from_cv(raw, "resume.txt")
    assert "Senior Python Developer" in extracted
    assert "FastAPI" in extracted


def test_auth_workflow(client):
    unique_email = f"candidate_{uuid.uuid4().hex[:8]}@example.com"
    # Register
    reg_resp = client.post("/api/auth/register", json={
        "email": unique_email,
        "password": "strongpassword123",
        "full_name": "Alex Dupont",
    })
    assert reg_resp.status_code == 200
    token = reg_resp.json()["token"]
    assert token

    # Check /api/auth/me with Bearer token
    me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["authenticated"] is True
    assert me_resp.json()["user"]["email"] == unique_email

    # Login
    login_resp = client.post("/api/auth/login", json={
        "email": unique_email,
        "password": "strongpassword123",
    })
    assert login_resp.status_code == 200
    assert "token" in login_resp.json()


def test_profile_and_cv_upload(client):
    # Update profile
    prof_resp = client.post("/api/profile", json={
        "portfolio_url": "https://alex-portfolio.dev",
        "github_url": "https://github.com/alexdupont",
        "bio": "Développeur passionné par l'IA et les architectures distribuées.",
        "writing_sample": "Bonjour, je développe depuis 7 ans des backends à fort trafic. Ce qui m'anime : la vitesse, le code propre et l'impact direct.",
        "writing_tone": "direct, humble mais percutant",
        "target_roles": ["Lead Python Developer", "AI Engineer"],
        "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "PyTorch"],
    })
    assert prof_resp.status_code == 200
    data = prof_resp.json()
    assert data["profile"]["portfolio_url"] == "https://alex-portfolio.dev"
    assert data["profile"]["writing_tone"] == "direct, humble mais percutant"

    # Upload CV file (simulated TXT)
    file_content = b"Alex Dupont - Developpeur Python & IA. 5 ans d'experience. Projets: RAG, microservices."
    upload_resp = client.post(
        "/api/profile/upload-cv",
        files={"file": ("mon_cv.txt", file_content, "text/plain")},
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["status"] == "success"
    assert upload_resp.json()["characters_extracted"] > 20


def test_kanban_tracking_lifecycle(client):
    storage = Storage()
    opp = JobOpportunity(
        id="test-kanban-opp-1",
        source="wttj",
        title="Senior Python Backend Engineer",
        company="Fintech Innov",
        location="Paris",
        remote_policy="full",
        url="https://wttj.co/jobs/123",
        description="Nous cherchons un Senior Python Engineer pour concevoir nos APIs financières avec FastAPI et PostgreSQL.",
    )
    storage.upsert_opportunity(opp)

    # 1. Add to Kanban (saved)
    create_resp = client.post("/api/applications", json={
        "opportunity_id": opp.id,
        "status": "saved",
        "notes": "Trouvé via Jobby, équipe prometteuse",
    })
    assert create_resp.status_code == 200
    app_id = create_resp.json()["application"]["id"]

    # 2. Get list of applications
    list_resp = client.get("/api/applications")
    assert list_resp.status_code == 200
    apps = list_resp.json()
    assert any(a["id"] == app_id for a in apps)

    # 3. Advance to 'applied'
    status_resp = client.patch(f"/api/applications/{app_id}/status", json={"status": "applied"})
    assert status_resp.status_code == 200
    assert status_resp.json()["new_status"] == "applied"

    # 4. Advance to 'interview'
    status_resp2 = client.patch(f"/api/applications/{app_id}/status", json={"status": "interview"})
    assert status_resp2.status_code == 200
    assert status_resp2.json()["new_status"] == "interview"

    # 5. Update notes
    notes_resp = client.patch(f"/api/applications/{app_id}/notes", json={"notes": "Entretien prévu le 18 à 14h"})
    assert notes_resp.status_code == 200

    # 6. Delete from Kanban
    del_resp = client.delete(f"/api/applications/{app_id}")
    assert del_resp.status_code == 200


def test_kanban_auto_sync_from_triage(client):
    """Test that clicking 'Garder' (kept) or 'Archiver' in hunt view immediately syncs to Kanban."""
    storage = Storage()
    opp = JobOpportunity(
        id="test-sync-opp-42",
        source="wttj",
        title="Fullstack Python Engineer",
        company="SyncLab",
        location="Paris",
        remote_policy="partial",
        url="https://wttj.co/jobs/42",
        description="Python & Vue developer",
    )
    storage.upsert_opportunity(opp)

    # 1. User clicks 'Je garde' -> status becomes 'kept'
    patch_resp = client.patch(f"/api/opportunities/{opp.id}/status", json={"status": "kept"})
    assert patch_resp.status_code == 200

    # Must appear in Kanban list under 'saved'
    kanban_resp = client.get("/api/applications")
    assert kanban_resp.status_code == 200
    apps = kanban_resp.json()
    sync_app = next((a for a in apps if a["opportunity_id"] == opp.id), None)
    assert sync_app is not None
    assert sync_app["status"] == "saved"

    # 2. User clicks 'Archiver' -> status becomes 'archived'
    patch_resp2 = client.patch(f"/api/opportunities/{opp.id}/status", json={"status": "archived"})
    assert patch_resp2.status_code == 200

    # Must appear in Kanban list under 'rejected' (Archivés)
    kanban_resp2 = client.get("/api/applications")
    apps2 = kanban_resp2.json()
    sync_app2 = next((a for a in apps2 if a["opportunity_id"] == opp.id), None)
    assert sync_app2 is not None
    assert sync_app2["status"] == "rejected"

    # 3. User un-marks -> status becomes 'new'
    patch_resp3 = client.patch(f"/api/opportunities/{opp.id}/status", json={"status": "new"})
    assert patch_resp3.status_code == 200

    kanban_resp3 = client.get("/api/applications")
    apps3 = kanban_resp3.json()
    assert not any(a["opportunity_id"] == opp.id for a in apps3)


def test_ai_match_and_cover_letter(client):
    storage = Storage()
    opp = JobOpportunity(
        id="test-ai-opp-2",
        source="linkedin",
        title="Python AI Agent Developer",
        company="Startup Studio",
        location="Remote",
        remote_policy="full",
        url="https://linkedin.com/jobs/view/456",
        author_name="Claire Recruteuse",
        description="Recherche développeur Python maîtrisant FastAPI et les agents LLM pour notre produit SaaS.",
    )
    storage.upsert_opportunity(opp)

    # Match evaluation (uses heuristic fallback in tests)
    match_resp = client.post(f"/api/ai/match/{opp.id}")
    assert match_resp.status_code == 200
    match_data = match_resp.json()
    assert "score" in match_data
    assert "pitch" in match_data
    assert len(match_data["pitch"]) > 10

    # Cover letter generation
    letter_resp = client.post(f"/api/ai/cover-letter/{opp.id}")
    assert letter_resp.status_code == 200
    letter_data = letter_resp.json()
    assert "cover_letter" in letter_data
    assert "Startup Studio" in letter_data["cover_letter"] or "Python" in letter_data["cover_letter"]
