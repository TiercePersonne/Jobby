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

    # 1. User clicks 'Je garde' -> status becomes 'kept' (legacy 'favorite'/'saved' also canonicalize to 'kept')
    patch_resp = client.patch(f"/api/opportunities/{opp.id}/status", json={"status": "favorite"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["new_status"] == "kept"
    assert patch_resp.json()["is_seen"] is True

    # Must appear in Kanban list under 'saved'
    kanban_resp = client.get("/api/applications")
    assert kanban_resp.status_code == 200
    apps = kanban_resp.json()
    sync_app = next((a for a in apps if a["opportunity_id"] == opp.id), None)
    assert sync_app is not None
    assert sync_app["status"] == "saved"
    assert sync_app["opportunity"]["status"] == "kept"

    # 2. User clicks 'Archiver' -> status becomes 'archived'
    patch_resp2 = client.patch(f"/api/opportunities/{opp.id}/status", json={"status": "archived"})
    assert patch_resp2.status_code == 200
    assert patch_resp2.json()["new_status"] == "archived"
    assert patch_resp2.json()["is_seen"] is True

    # Must appear in Kanban list under 'rejected' (Archivés)
    kanban_resp2 = client.get("/api/applications")
    apps2 = kanban_resp2.json()
    sync_app2 = next((a for a in apps2 if a["opportunity_id"] == opp.id), None)
    assert sync_app2 is not None
    assert sync_app2["status"] == "rejected"

    # 3. User un-marks -> status becomes 'new' and keeps is_seen=True
    patch_resp3 = client.patch(f"/api/opportunities/{opp.id}/status", json={"status": "new"})
    assert patch_resp3.status_code == 200
    assert patch_resp3.json()["new_status"] == "new"
    assert patch_resp3.json()["is_seen"] is True

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


def test_ideal_brief_ranking_3rd_way(client):
    """Test the '3ème Voie' : semantic matching and ranking of opportunities against an ideal brief."""
    storage = Storage()
    opp1 = JobOpportunity(
        id="opp_lead_designer",
        source="wttj",
        title="Lead Product Designer Figma Design System",
        company="Studio Supernova",
        location="Paris (Full Remote)",
        remote_policy="full",
        description="Nous cherchons un Lead Designer autonome pour piloter notre Design System et nos apps Figma en full remote.",
        url="https://wttj.co/jobs/1",
    )
    opp2 = JobOpportunity(
        id="opp_devops",
        source="indeed",
        title="Ingénieur Kubernetes DevOps Sur Site",
        company="BigCorp Infra",
        location="Marseille",
        remote_policy="onsite",
        description="Maintenance des clusters Kubernetes sur site 5j/5.",
        url="https://indeed.com/jobs/2",
    )
    storage.upsert_opportunity(opp1)
    storage.upsert_opportunity(opp2)

    brief = "Je cherche un poste de Lead Product Designer ou Design Ops, full remote, orienté Figma et Design Systems."
    resp = client.post("/api/ai/rank-by-brief", json={"brief": brief, "limit": 20})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["updated_count"] >= 2
    assert len(data["results"]) >= 2

    # opp1 should have a significantly higher score than opp2
    scores = {r["id"]: r["score"] for r in data["results"]}
    assert scores["opp_lead_designer"] > scores["opp_devops"]


def _setup_isolated_env(monkeypatch, tmp_path):
    from jobby.config import JobbyConfig, save_config

    db_path = tmp_path / "brief_persistence.db"
    cfg_path = tmp_path / "brief_config.yaml"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    save_config(
        JobbyConfig(
            target_roles=["Product Designer", "Design Ops"],
            skills_keywords=["Figma", "Design System"],
            min_salary=65000,
            remote_only=True,
            database_path=str(db_path),
            digest_output_path=str(tmp_path / "brief_digest.md"),
        ),
        config_path=cfg_path,
    )
    return db_path, cfg_path


def test_put_profile_brief_creates_profile_normalizes_and_skips_ai(client, monkeypatch, tmp_path):
    db_path, _ = _setup_isolated_env(monkeypatch, tmp_path)

    async def fail_if_ai_called(*args, **kwargs):
        raise AssertionError("AI ranking must never run on PUT /api/profile/brief")

    monkeypatch.setattr("jobby.server.rank_opportunities_by_ideal_brief", fail_if_ai_called)

    # 1. Invalid payloads -> 422
    assert client.put("/api/profile/brief", json={}).status_code == 422
    assert client.put("/api/profile/brief", json={"brief": 123}).status_code == 422
    assert client.put("/api/profile/brief", json={"brief": ["Lead"]}).status_code == 422
    assert client.put(
        "/api/profile/brief",
        content="{not-valid-json",
        headers={"Content-Type": "application/json"},
    ).status_code == 422

    # 2. Save brief when user_profiles table is empty -> auto-creates UserProfile with config defaults
    resp = client.put("/api/profile/brief", json={"brief": "  Lead Designer  "})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "saved"
    assert body["ideal_job_brief"] == "Lead Designer"
    assert body["profile"]["ideal_job_brief"] == "Lead Designer"
    assert body["profile"]["target_roles"] == ["Product Designer", "Design Ops"]
    assert body["profile"]["skills"] == ["Figma", "Design System"]
    assert body["profile"]["min_salary"] == 65000
    assert body["profile"]["remote_preference"] == "full"
    assert body["profile"]["raw_metadata"] == {}

    # Persists in SQLite user_profiles.ideal_job_brief across fresh Storage/GET
    fresh_storage = Storage(db_path=db_path)
    user = fresh_storage.get_user_by_email("user@jobby.local")
    assert user is not None
    persisted = fresh_storage.get_profile(user.id)
    assert persisted is not None
    assert persisted.ideal_job_brief == "Lead Designer"
    assert persisted.raw_metadata == {}

    get_resp = client.get("/api/profile")
    assert get_resp.status_code == 200
    assert get_resp.json()["profile"]["ideal_job_brief"] == "Lead Designer"

    # 3. Whitespace-only or null clears ideal_job_brief to None (HTTP 200)
    clear_ws = client.put("/api/profile/brief", json={"brief": "   "})
    assert clear_ws.status_code == 200
    assert clear_ws.json()["status"] == "saved"
    assert clear_ws.json()["ideal_job_brief"] is None
    assert fresh_storage.get_profile(user.id).ideal_job_brief is None

    client.put("/api/profile/brief", json={"brief": "Second brief"})
    clear_null = client.put("/api/profile/brief", json={"brief": None})
    assert clear_null.status_code == 200
    assert clear_null.json()["status"] == "saved"
    assert clear_null.json()["ideal_job_brief"] is None
    assert fresh_storage.get_profile(user.id).ideal_job_brief is None


def test_rank_by_brief_persists_early_without_profile_or_opps_and_saves_ai_match(client, monkeypatch, tmp_path):
    db_path, _ = _setup_isolated_env(monkeypatch, tmp_path)

    # Empty brief -> HTTP 400
    empty_resp = client.post("/api/ai/rank-by-brief", json={"brief": "   "})
    assert empty_resp.status_code == 400

    # Empty user_profiles AND empty opportunities -> creates UserProfile + persists ideal_job_brief -> 200
    no_opps_resp = client.post("/api/ai/rank-by-brief", json={"brief": "Staff Product Designer remote"})
    assert no_opps_resp.status_code == 200
    no_opps_data = no_opps_resp.json()
    assert no_opps_data["total_evaluated"] == 0
    assert no_opps_data["updated_count"] == 0
    assert no_opps_data["results"] == []

    storage = Storage(db_path=db_path)
    user = storage.get_user_by_email("user@jobby.local")
    assert user is not None
    profile = storage.get_profile(user.id)
    assert profile is not None
    assert profile.ideal_job_brief == "Staff Product Designer remote"
    assert profile.target_roles == ["Product Designer", "Design Ops"]
    assert profile.raw_metadata == {}

    # With active opportunity -> persists brief AND raw_metadata.ai_match (BH-1)
    opp = JobOpportunity(
        id="opp-rank-persist-1",
        source="wttj",
        title="Staff Product Designer",
        company="Warm Studio",
        location="Remote",
        remote_policy="full",
        description="Lead design system and Figma tooling.",
        url="https://wttj.co/jobs/staff-pd",
        raw_metadata={"contract_type": "CDI"},
    )
    storage.upsert_opportunity(opp)

    ranked_resp = client.post("/api/ai/rank-by-brief", json={"brief": "Staff Product Designer Figma remote"})
    assert ranked_resp.status_code == 200
    assert ranked_resp.json()["total_evaluated"] == 1
    assert ranked_resp.json()["updated_count"] == 1

    # Reload via fresh Storage: ideal_job_brief + raw_metadata.ai_match persisted
    fresh_storage = Storage(db_path=db_path)
    assert fresh_storage.get_profile(user.id).ideal_job_brief == "Staff Product Designer Figma remote"
    persisted_opp = fresh_storage.get_opportunity("opp-rank-persist-1")
    assert persisted_opp is not None
    assert persisted_opp.raw_metadata["contract_type"] == "CDI"
    assert "ai_match" in persisted_opp.raw_metadata
    assert persisted_opp.raw_metadata["ai_match"]["id"] == "opp-rank-persist-1"

    # Re-hunt re-upserting with empty raw_metadata keeps ai_match intact (BH-1)
    fresh_storage.upsert_opportunity(
        JobOpportunity(
            id="opp-rank-persist-1",
            source="wttj",
            title="Staff Product Designer",
            company="Warm Studio",
            url="https://wttj.co/jobs/staff-pd",
            raw_metadata={},
        )
    )
    after_rehunt = fresh_storage.get_opportunity("opp-rank-persist-1")
    assert after_rehunt.raw_metadata["contract_type"] == "CDI"
    assert after_rehunt.raw_metadata["ai_match"]["id"] == "opp-rank-persist-1"


def test_profile_save_and_cv_upload_preserve_ideal_job_brief_and_init_defaults(client, monkeypatch, tmp_path):
    db_path, _ = _setup_isolated_env(monkeypatch, tmp_path)

    # 1. upload-cv on empty user_profiles initializes config defaults
    up_resp = client.post(
        "/api/profile/upload-cv",
        files={"file": ("cv.txt", b"Senior Product Designer with 8 years of Figma experience.", "text/plain")},
    )
    assert up_resp.status_code == 200
    storage = Storage(db_path=db_path)
    user = storage.get_user_by_email("user@jobby.local")
    profile_after_cv = storage.get_profile(user.id)
    assert profile_after_cv.target_roles == ["Product Designer", "Design Ops"]
    assert profile_after_cv.skills == ["Figma", "Design System"]
    assert profile_after_cv.cv_filename == "cv.txt"

    # 2. Save ideal_job_brief via PUT /api/profile/brief
    put_resp = client.put("/api/profile/brief", json={"brief": "Design Ops Lead full remote"})
    assert put_resp.status_code == 200

    # 3. POST /api/profile saves non-default custom profile fields and preserves ideal_job_brief (AD-2)
    post_prof = client.post("/api/profile", json={
        "portfolio_url": "https://designer.example.com",
        "github_url": "https://github.com/designer",
        "linkedin_url": "https://linkedin.com/in/designer",
        "bio": "Design Systems architect",
        "writing_sample": "Direct and concise style sample.",
        "writing_tone": "sobre et direct",
        "target_roles": ["Design Ops Lead", "Principal Designer"],
        "skills": ["Figma", "Tokens", "Accessibility"],
        "min_salary": 80000,
        "remote_preference": "hybrid",
    })
    assert post_prof.status_code == 200
    assert post_prof.json()["profile"]["ideal_job_brief"] == "Design Ops Lead full remote"

    # 4. Subsequent PUT /api/profile/brief, POST /api/ai/rank-by-brief & upload-cv preserve ALL non-default profile fields (VG-3)
    put_resp2 = client.put("/api/profile/brief", json={"brief": "Principal Design Ops"})
    assert put_resp2.status_code == 200

    rank_resp = client.post("/api/ai/rank-by-brief", json={"brief": "Principal Design Ops Remote"})
    assert rank_resp.status_code == 200

    up_resp2 = client.post(
        "/api/profile/upload-cv",
        files={"file": ("cv_v2.txt", b"Updated CV text for Design Ops Lead.", "text/plain")},
    )
    assert up_resp2.status_code == 200

    persisted = storage.get_profile(user.id)
    assert persisted is not None
    assert persisted.ideal_job_brief == "Principal Design Ops Remote"
    assert persisted.cv_filename == "cv_v2.txt"
    assert "Updated CV text" in persisted.cv_text
    assert persisted.portfolio_url == "https://designer.example.com"
    assert persisted.github_url == "https://github.com/designer"
    assert persisted.linkedin_url == "https://linkedin.com/in/designer"
    assert persisted.bio == "Design Systems architect"
    assert persisted.writing_sample == "Direct and concise style sample."
    assert persisted.writing_tone == "sobre et direct"
    assert persisted.target_roles == ["Design Ops Lead", "Principal Designer"]
    assert persisted.skills == ["Figma", "Tokens", "Accessibility"]
    assert persisted.min_salary == 80000
    assert persisted.remote_preference == "hybrid"

    get_prof = client.get("/api/profile").json()
    assert get_prof["has_cv"] is True
    assert get_prof["profile"]["ideal_job_brief"] == "Principal Design Ops Remote"
    assert get_prof["profile"]["portfolio_url"] == "https://designer.example.com"
    assert get_prof["profile"]["target_roles"] == ["Design Ops Lead", "Principal Designer"]


def test_profile_cold_start_brief_decoupling_multi_user_and_rehunt(client, monkeypatch, tmp_path):
    from jobby.providers import registry

    db_path, _ = _setup_isolated_env(monkeypatch, tmp_path)

    # 1. Cold start GET /api/profile on empty DB creates UserProfile with config defaults & has_cv=False
    cold = client.get("/api/profile")
    assert cold.status_code == 200
    cold_data = cold.json()
    assert cold_data["has_cv"] is False
    assert cold_data["profile"]["ideal_job_brief"] is None
    assert cold_data["profile"]["raw_metadata"] == {}
    assert cold_data["profile"]["target_roles"] == ["Product Designer", "Design Ops"]
    assert cold_data["profile"]["skills"] == ["Figma", "Design System"]
    assert cold_data["profile"]["min_salary"] == 65000
    assert cold_data["profile"]["remote_preference"] == "full"

    # 2. Seed scored opportunity with ai_match before PUT /api/profile/brief -> untouched by PUT (VG-4)
    storage = Storage(db_path=db_path)
    seeded = JobOpportunity(
        id="opp-vg4-1",
        source="wttj",
        title="Product Designer",
        company="Warm Lab",
        url="https://wttj.co/jobs/vg4",
        score=91.0,
        score_reasons=["Top match brief"],
        raw_metadata={"ai_match": {"id": "opp-vg4-1", "score": 91, "verdict": "Top match brief"}},
    )
    storage.upsert_opportunity(seeded)

    put_new = client.put("/api/profile/brief", json={"brief": "Completely different brief"})
    assert put_new.status_code == 200
    assert put_new.json()["ideal_job_brief"] == "Completely different brief"
    after_put = storage.get_opportunity("opp-vg4-1")
    assert after_put.score == 91.0
    assert after_put.score_reasons == ["Top match brief"]
    assert after_put.raw_metadata["ai_match"] == {"id": "opp-vg4-1", "score": 91, "verdict": "Top match brief"}

    # Empty string "" clears ideal_job_brief to None without touching opportunity scores
    put_empty = client.put("/api/profile/brief", json={"brief": ""})
    assert put_empty.status_code == 200
    assert put_empty.json()["ideal_job_brief"] is None
    assert storage.get_opportunity("opp-vg4-1").score == 91.0

    # 3. Multi-user Bearer token isolation
    reg_a = client.post(
        "/api/auth/register",
        json={"email": f"a_{uuid.uuid4().hex[:6]}@example.com", "password": "password123", "full_name": "User A"},
    )
    reg_b = client.post(
        "/api/auth/register",
        json={"email": f"b_{uuid.uuid4().hex[:6]}@example.com", "password": "password123", "full_name": "User B"},
    )
    client.cookies.clear()
    token_a = reg_a.json()["token"]
    token_b = reg_b.json()["token"]

    assert client.put(
        "/api/profile/brief",
        json={"brief": "Brief User A"},
        headers={"Authorization": f"Bearer {token_a}"},
    ).status_code == 200
    assert client.put(
        "/api/profile/brief",
        json={"brief": "Brief User B"},
        headers={"Authorization": f"Bearer {token_b}"},
    ).status_code == 200

    assert client.get(
        "/api/profile",
        headers={"Authorization": f"Bearer {token_a}"},
    ).json()["profile"]["ideal_job_brief"] == "Brief User A"
    assert client.get(
        "/api/profile",
        headers={"Authorization": f"Bearer {token_b}"},
    ).json()["profile"]["ideal_job_brief"] == "Brief User B"
    assert client.get("/api/profile").json()["profile"]["ideal_job_brief"] is None

    # 4. Real re-hunt via POST /api/search preserves ai_match, score & score_reasons (BH-1 + BH-2)
    async def fake_search_all(params, source_names=None, fail_silently=True):
        return [
            JobOpportunity(
                id="opp-rehunt-search-1",
                source="wttj",
                title="Lead Product Designer Figma",
                company="Studio Search",
                location="Remote",
                remote_policy="full",
                description="Design System Figma full remote.",
                url="https://wttj.co/jobs/rehunt-search-1",
                raw_metadata={"contract_type": "CDI", "hunt_pass": 2},
            )
        ]

    monkeypatch.setattr(registry, "search_all", fake_search_all)

    # First hunt inserts opportunity
    search1 = client.post("/api/search", json={"keywords": ["Product Designer"], "sources": ["wttj"]})
    assert search1.status_code == 200

    # AI ranking assigns AI score, score_reasons & raw_metadata.ai_match
    ranked = client.post("/api/ai/rank-by-brief", json={"brief": "Lead Product Designer Figma full remote"})
    assert ranked.status_code == 200
    ranked_opp = storage.get_opportunity("opp-rehunt-search-1")
    ai_score = ranked_opp.score
    ai_reasons = list(ranked_opp.score_reasons)
    ai_match_payload = dict(ranked_opp.raw_metadata["ai_match"])

    # Second hunt (re-hunt) runs ScoringEngine + upsert_opportunity without ai_match
    search2 = client.post("/api/search", json={"keywords": ["Unrelated Keyword"], "sources": ["wttj"]})
    assert search2.status_code == 200
    after_rehunt = storage.get_opportunity("opp-rehunt-search-1")
    assert after_rehunt.score == ai_score
    assert after_rehunt.score_reasons == ai_reasons
    assert after_rehunt.raw_metadata["ai_match"] == ai_match_payload
    assert after_rehunt.raw_metadata["hunt_pass"] == 2


