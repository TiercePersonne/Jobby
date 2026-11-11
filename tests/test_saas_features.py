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


def test_ai_match_and_cover_letter(client, monkeypatch, tmp_path):
    db_path, _ = _setup_isolated_env(monkeypatch, tmp_path)
    storage = Storage(db_path=db_path)
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
    assert match_data["provider_used"] == "heuristic"
    assert match_data["used_fallback"] is True
    assert match_data["fallback_reason"]

    # Cover letter generation
    letter_resp = client.post(f"/api/ai/cover-letter/{opp.id}")
    assert letter_resp.status_code == 200
    letter_data = letter_resp.json()
    assert "cover_letter" in letter_data
    assert "Startup Studio" in letter_data["cover_letter"] or "Python" in letter_data["cover_letter"]
    assert letter_data["provider_used"] == "heuristic"
    assert letter_data["used_fallback"] is True
    assert letter_data["fallback_reason"]


def test_ideal_brief_ranking_3rd_way(client, monkeypatch, tmp_path):
    """Test the '3ème Voie' : semantic matching and ranking of opportunities against an ideal brief."""
    db_path, _ = _setup_isolated_env(monkeypatch, tmp_path)
    storage = Storage(db_path=db_path)
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
    assert data["provider_used"] == "heuristic"
    assert data["used_fallback"] is True
    assert data["fallback_reason"]

    # opp1 should have a significantly higher score than opp2
    scores = {r["id"]: r["score"] for r in data["results"]}
    assert scores["opp_lead_designer"] > scores["opp_devops"]


def _setup_isolated_env(monkeypatch, tmp_path):
    from jobby.config import JobbyConfig, save_config

    db_path = tmp_path / "brief_persistence.db"
    cfg_path = tmp_path / "brief_config.yaml"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
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
    monkeypatch.setattr("jobby.server.rank_opportunities_by_ideal_brief_with_meta", fail_if_ai_called)

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


def test_rank_by_brief_20_opps_nominal_and_mixed_batches_and_empty_feed(client, monkeypatch, tmp_path):
    import json
    import httpx

    db_path, _ = _setup_isolated_env(monkeypatch, tmp_path)
    storage = Storage(db_path=db_path)

    # 1. Empty active feed (0 active opps) -> persists brief & honest metadata
    empty_resp = client.post("/api/ai/rank-by-brief", json={"brief": "Staff Product Designer Figma"})
    assert empty_resp.status_code == 200
    empty_data = empty_resp.json()
    assert empty_data["total_evaluated"] == 0
    assert empty_data["provider_used"] == "heuristic"
    assert empty_data["used_fallback"] is False
    assert empty_data["fallback_reason"] is None

    # Seed 20 active opportunities
    for i in range(20):
        storage.upsert_opportunity(
            JobOpportunity(
                id=f"batch-opp-{i:02d}",
                source="wttj",
                title=f"Product Designer {i}",
                company=f"Studio {i}",
                location="Paris",
                remote_policy="full",
                description="Design System Figma remote.",
                url=f"https://wttj.co/jobs/batch-{i:02d}",
            )
        )

    class FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def install_httpx(handler):
        recorded = []

        class FakeClient:
            def __init__(self, *args, timeout=None, **kwargs):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json=None, **kwargs):
                call = {"url": url, "json": json, "timeout": self.timeout}
                recorded.append(call)
                return await handler(call)

        monkeypatch.setattr("jobby.ai_analyzer.httpx.AsyncClient", FakeClient)
        return recorded

    # 2. Nominal 20 offers with mocked GEMINI_API_KEY -> 2 requests (15 + 5)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    async def nominal_handler(call):
        prompt = call["json"]["contents"][0]["parts"][0]["text"]
        batch_items = json.loads(prompt.split("--- LISTE DES OPPORTUNITÉS ---\n", 1)[1].split("\n\nPour CHAQUE élément", 1)[0])
        return FakeResp(
            200,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps([
                                        {"id": it["id"], "score": 91, "verdict": "Gemini nominal", "pitch": "Go"}
                                        for it in batch_items
                                    ])
                                }
                            ]
                        }
                    }
                ]
            },
        )

    nominal_calls = install_httpx(nominal_handler)
    nom_resp = client.post("/api/ai/rank-by-brief", json={"brief": "Product Designer Figma remote", "limit": 60})
    assert nom_resp.status_code == 200
    nom_data = nom_resp.json()
    assert nom_data["total_evaluated"] == 20
    assert nom_data["updated_count"] == 20
    assert nom_data["provider_used"] == "gemini"
    assert nom_data["used_fallback"] is False
    assert nom_data["fallback_reason"] is None
    assert len(nominal_calls) == 2
    batch_counts = []
    for call in nominal_calls:
        assert call["timeout"] >= 35.0
        assert call["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
        prompt = call["json"]["contents"][0]["parts"][0]["text"]
        batch_items = json.loads(prompt.split("--- LISTE DES OPPORTUNITÉS ---\n", 1)[1].split("\n\nPour CHAQUE élément", 1)[0])
        batch_counts.append(len(batch_items))
    assert sorted(batch_counts) == [5, 15]
    assert all(r["provider_used"] == "gemini" and r["used_fallback"] is False and r["fallback_reason"] is None for r in nom_data["results"])
    sample_persisted = storage.get_opportunity("batch-opp-00")
    assert sample_persisted.raw_metadata["ai_match"]["provider_used"] == "gemini"
    assert sample_persisted.raw_metadata["ai_match"]["used_fallback"] is False

    # 3. Mixed batch: Lot 1 (15) HTTP 200, Lot 2 (5) Timeout -> global gemini + used_fallback=True
    async def mixed_handler(call):
        prompt = call["json"]["contents"][0]["parts"][0]["text"]
        batch_items = json.loads(prompt.split("--- LISTE DES OPPORTUNITÉS ---\n", 1)[1].split("\n\nPour CHAQUE élément", 1)[0])
        if len(batch_items) == 5:
            raise httpx.ReadTimeout("lot 2 timeout")
        return FakeResp(
            200,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps([
                                        {"id": it["id"], "score": 89, "verdict": "Gemini batch 1", "pitch": "Go"}
                                        for it in batch_items
                                    ])
                                }
                            ]
                        }
                    }
                ]
            },
        )

    install_httpx(mixed_handler)
    mixed_resp = client.post("/api/ai/rank-by-brief", json={"brief": "Product Designer Figma remote", "limit": 60})
    assert mixed_resp.status_code == 200
    mixed_data = mixed_resp.json()
    assert mixed_data["total_evaluated"] == 20
    assert mixed_data["provider_used"] == "gemini"
    assert mixed_data["used_fallback"] is True
    assert mixed_data["fallback_reason"]

    gemini_items = [r for r in mixed_data["results"] if r["provider_used"] == "gemini"]
    fallback_items = [r for r in mixed_data["results"] if r["provider_used"] == "heuristic"]
    assert len(gemini_items) == 15
    assert len(fallback_items) == 5
    assert all(r["used_fallback"] is False for r in gemini_items)
    assert all(r["used_fallback"] is True and r["fallback_reason"] for r in fallback_items)

    # Check raw_metadata.ai_match reflects per-item provenance in SQLite
    reloaded = [storage.get_opportunity(f"batch-opp-{i:02d}") for i in range(20)]
    persisted_gemini = [o for o in reloaded if o.raw_metadata["ai_match"]["provider_used"] == "gemini"]
    persisted_heur = [o for o in reloaded if o.raw_metadata["ai_match"]["provider_used"] == "heuristic"]
    assert len(persisted_gemini) == 15
    assert len(persisted_heur) == 5

    # 4. Full HTTP 429 and HTTP 503 fallback on /api/ai/rank-by-brief -> 100% heuristic
    for status_code in (429, 503):
        async def full_err_handler(call, code=status_code):
            return FakeResp(code, {"error": "unavailable"})

        install_httpx(full_err_handler)
        err_resp = client.post("/api/ai/rank-by-brief", json={"brief": "Product Designer Figma remote", "limit": 60})
        assert err_resp.status_code == 200
        err_data = err_resp.json()
        assert err_data["provider_used"] == "heuristic"
        assert err_data["used_fallback"] is True
        assert str(status_code) in err_data["fallback_reason"]
        assert all(
            r["provider_used"] == "heuristic" and r["used_fallback"] is True and str(status_code) in r["fallback_reason"]
            for r in err_data["results"]
        )


def test_match_and_cover_letter_cache_honesty_and_upgrade(client, monkeypatch, tmp_path):
    import json

    db_path, _ = _setup_isolated_env(monkeypatch, tmp_path)
    storage = Storage(db_path=db_path)
    opp = JobOpportunity(
        id="opp-cache-test",
        source="wttj",
        title="Lead Product Designer",
        company="Porcelain Lab",
        location="Remote",
        remote_policy="full",
        description="Figma Design System",
        url="https://wttj.co/jobs/cache-test",
    )
    storage.upsert_opportunity(opp)

    # 1. Without GEMINI_API_KEY: computes heuristic & caches honestly
    m1 = client.post(f"/api/ai/match/{opp.id}")
    assert m1.status_code == 200
    assert m1.json()["provider_used"] == "heuristic"
    assert m1.json()["used_fallback"] is True
    assert m1.json()["fallback_reason"]

    c1 = client.post(f"/api/ai/cover-letter/{opp.id}")
    assert c1.status_code == 200
    assert c1.json()["provider_used"] == "heuristic"
    assert c1.json()["used_fallback"] is True
    assert c1.json()["fallback_reason"]

    # Seed legacy row (provider_used=NULL) without key -> served without claiming "gemini"
    with storage._connection() as conn:
        conn.execute(
            "UPDATE match_evaluations SET provider_used = NULL, used_fallback = 0, fallback_reason = NULL WHERE opportunity_id = ?",
            (opp.id,),
        )
        conn.commit()
    m_legacy_no_key = client.post(f"/api/ai/match/{opp.id}")
    assert m_legacy_no_key.status_code == 200
    assert m_legacy_no_key.json()["provider_used"] == "heuristic"
    assert m_legacy_no_key.json()["used_fallback"] is True

    c_legacy_no_key = client.post(f"/api/ai/cover-letter/{opp.id}")
    assert c_legacy_no_key.status_code == 200
    assert c_legacy_no_key.json()["provider_used"] == "heuristic"
    assert c_legacy_no_key.json()["used_fallback"] is True

    # 2. Now add GEMINI_API_KEY + mock HTTPX -> legacy/heuristic cache MUST be bypassed and upgraded to Gemini
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    http_calls = []

    class FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, timeout=None, **kwargs):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, **kwargs):
            http_calls.append({"url": url, "json": json, "timeout": self.timeout})
            prompt = json["contents"][0]["parts"][0]["text"]
            if "Évalue la compatibilité" in prompt:
                return FakeResp(
                    200,
                    {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "text": '{"score": 93, "verdict": "Gemini Top Match", "strengths": ["Figma"], "weaknesses": [], "pitch": "Bonjour Gemini"}'
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                )
            return FakeResp(
                200,
                {"candidates": [{"content": {"parts": [{"text": "Lettre sur-mesure Gemini."}]}}]},
            )

    monkeypatch.setattr("jobby.ai_analyzer.httpx.AsyncClient", FakeClient)

    m_up = client.post(f"/api/ai/match/{opp.id}")
    assert m_up.status_code == 200
    assert m_up.json()["score"] == 93.0
    assert m_up.json()["verdict"] == "Gemini Top Match"
    assert m_up.json()["provider_used"] == "gemini"
    assert m_up.json()["used_fallback"] is False
    assert m_up.json()["fallback_reason"] is None

    c_up = client.post(f"/api/ai/cover-letter/{opp.id}")
    assert c_up.status_code == 200
    assert c_up.json()["cover_letter"] == "Lettre sur-mesure Gemini."
    assert c_up.json()["provider_used"] == "gemini"
    assert c_up.json()["used_fallback"] is False
    assert c_up.json()["fallback_reason"] is None

    assert len(http_calls) == 2
    for call in http_calls:
        assert call["timeout"] >= 35.0
        assert call["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}

    # 3. Repeat calls (with AND without key) MUST serve the Gemini cache with 0 extra HTTP calls
    m_cached = client.post(f"/api/ai/match/{opp.id}")
    c_cached = client.post(f"/api/ai/cover-letter/{opp.id}")
    assert m_cached.json()["provider_used"] == "gemini"
    assert m_cached.json()["used_fallback"] is False
    assert c_cached.json()["provider_used"] == "gemini"
    assert c_cached.json()["used_fallback"] is False
    assert len(http_calls) == 2

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    m_cached_nokey = client.post(f"/api/ai/match/{opp.id}")
    c_cached_nokey = client.post(f"/api/ai/cover-letter/{opp.id}")
    assert m_cached_nokey.json()["score"] == 93.0
    assert m_cached_nokey.json()["provider_used"] == "gemini"
    assert m_cached_nokey.json()["used_fallback"] is False
    assert c_cached_nokey.json()["cover_letter"] == "Lettre sur-mesure Gemini."
    assert c_cached_nokey.json()["provider_used"] == "gemini"
    assert c_cached_nokey.json()["used_fallback"] is False
    assert len(http_calls) == 2


def test_match_and_cover_letter_independent_provenance_zero_score_and_provider_isolation(client, monkeypatch, tmp_path):
    import json
    from jobby.config import JobbyConfig, save_config
    from jobby.models import MatchEvaluation

    db_path, cfg_path = _setup_isolated_env(monkeypatch, tmp_path)
    storage = Storage(db_path=db_path)

    for oid in ("opp-indep-a", "opp-indep-b", "opp-zero", "opp-legacy-ph"):
        storage.upsert_opportunity(
            JobOpportunity(
                id=oid,
                source="wttj",
                title="Lead Designer",
                company="Studio",
                location="Remote",
                remote_policy="full",
                description="Figma Design System",
                url=f"https://wttj.co/jobs/{oid}",
            )
        )

    class FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def install_httpx(handler):
        calls = []

        class FakeClient:
            def __init__(self, *args, timeout=None, **kwargs):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json=None, headers=None, **kwargs):
                call = {"url": url, "json": json, "headers": headers, "timeout": self.timeout}
                calls.append(call)
                return await handler(call)

        monkeypatch.setattr("jobby.ai_analyzer.httpx.AsyncClient", FakeClient)
        return calls

    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    # Order A: match (Gemini OK) -> cover-letter (HTTP 503 fallback) -> match MUST stay Gemini;
    #          then cover-letter (Gemini OK) upgrades only cover-letter without touching match!
    state = {"fail_letter": True}

    async def order_a_handler(call):
        prompt = call["json"]["contents"][0]["parts"][0]["text"]
        if "Évalue la compatibilité" in prompt:
            return FakeResp(
                200,
                {"candidates": [{"content": {"parts": [{"text": json.dumps({"score": 92, "verdict": "Gemini Match A", "strengths": ["Figma"], "weaknesses": [], "pitch": "Pitch A"})}]}}]},
            )
        if state["fail_letter"]:
            return FakeResp(503, {"error": "busy"})
        return FakeResp(
            200,
            {"candidates": [{"content": {"parts": [{"text": "Lettre Gemini A"}]}}]},
        )

    calls_a = install_httpx(order_a_handler)
    m_a = client.post("/api/ai/match/opp-indep-a")
    assert m_a.status_code == 200
    m_a_json = m_a.json()
    assert m_a_json["provider_used"] == "gemini"
    assert m_a_json["used_fallback"] is False
    assert "cover_letter_provider_used" not in m_a_json
    assert "cover_letter_used_fallback" not in m_a_json
    assert "cover_letter_fallback_reason" not in m_a_json

    c_a_fb = client.post("/api/ai/cover-letter/opp-indep-a")
    assert c_a_fb.status_code == 200
    assert c_a_fb.json()["provider_used"] == "heuristic"
    assert c_a_fb.json()["used_fallback"] is True
    assert "503" in c_a_fb.json()["fallback_reason"]

    # Match MUST still be served from Gemini cache (0 extra HTTP calls)!
    calls_before = len(calls_a)
    m_a_cached = client.post("/api/ai/match/opp-indep-a")
    assert m_a_cached.json()["provider_used"] == "gemini"
    assert m_a_cached.json()["verdict"] == "Gemini Match A"
    assert len(calls_a) == calls_before

    # Now cover-letter succeeds with Gemini -> upgrades cover_letter only, keeps match intact
    state["fail_letter"] = False
    c_a_ok = client.post("/api/ai/cover-letter/opp-indep-a")
    assert c_a_ok.json()["cover_letter"] == "Lettre Gemini A"
    assert c_a_ok.json()["provider_used"] == "gemini"
    assert c_a_ok.json()["used_fallback"] is False
    user_id = client.get("/api/profile").json()["user"]["id"]
    saved_a = storage.get_match_evaluation(user_id, "opp-indep-a")
    assert saved_a is not None
    assert saved_a.provider_used == "gemini"
    assert saved_a.used_fallback is False
    assert saved_a.cover_letter_provider_used == "gemini"
    assert saved_a.cover_letter_used_fallback is False
    m_a_still = client.post("/api/ai/match/opp-indep-a")
    assert m_a_still.json()["provider_used"] == "gemini"
    assert m_a_still.json()["cover_letter"] == "Lettre Gemini A"

    # Order B: cover-letter-first (Gemini OK) -> match (HTTP 503 fallback) -> cover-letter stays Gemini,
    #          and match did NOT return the "Généré" placeholder!
    state_b = {"fail_match": True}

    async def order_b_handler(call):
        prompt = call["json"]["contents"][0]["parts"][0]["text"]
        if "Rédige une lettre de motivation" in prompt:
            return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "Lettre Gemini B"}]}}]})
        if state_b["fail_match"]:
            return FakeResp(503, {"error": "down"})
        return FakeResp(
            200,
            {"candidates": [{"content": {"parts": [{"text": json.dumps({"score": 84, "verdict": "Gemini Match B", "strengths": ["Figma"], "weaknesses": [], "pitch": "Pitch B"})}]}}]},
        )

    calls_b = install_httpx(order_b_handler)
    c_b = client.post("/api/ai/cover-letter/opp-indep-b")
    assert c_b.status_code == 200
    assert c_b.json()["provider_used"] == "gemini"
    assert c_b.json()["cover_letter"] == "Lettre Gemini B"

    m_b_fb = client.post("/api/ai/match/opp-indep-b")
    assert m_b_fb.status_code == 200
    assert m_b_fb.json()["provider_used"] == "heuristic"
    assert m_b_fb.json()["used_fallback"] is True
    assert m_b_fb.json()["verdict"] != "Généré"

    # Cover-letter cache MUST still serve Gemini with 0 HTTP calls!
    b_calls_before = len(calls_b)
    c_b_cached = client.post("/api/ai/cover-letter/opp-indep-b")
    assert c_b_cached.json()["provider_used"] == "gemini"
    assert c_b_cached.json()["cover_letter"] == "Lettre Gemini B"
    assert len(calls_b) == b_calls_before

    # Legacy placeholder (score=75.0, verdict="Généré", provider_used=NULL, pitch="", strengths=[]) -> match runs evaluation
    user_id = client.get("/api/profile").json()["user"]["id"]
    storage.save_match_evaluation(
        MatchEvaluation(
            user_id=user_id,
            opportunity_id="opp-legacy-ph",
            score=75.0,
            verdict="Généré",
            strengths=[],
            weaknesses=[],
            pitch="",
            cover_letter="Legacy Letter",
            provider_used=None,
            used_fallback=False,
        )
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    ph_resp = client.post("/api/ai/match/opp-legacy-ph")
    assert ph_resp.status_code == 200
    assert ph_resp.json()["verdict"] != "Généré"
    assert ph_resp.json()["provider_used"] == "heuristic"
    assert ph_resp.json()["used_fallback"] is True

    # Valid 0.0 score from Gemini is cached and served honestly on repeat calls (with & without key)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    async def zero_score_handler(call):
        return FakeResp(
            200,
            {"candidates": [{"content": {"parts": [{"text": json.dumps({"score": 0, "verdict": "Hors sujet", "strengths": [], "weaknesses": ["Stack"], "pitch": "Pas aligné"})}]}}]},
        )

    zero_calls = install_httpx(zero_score_handler)
    z1 = client.post("/api/ai/match/opp-zero")
    assert z1.status_code == 200
    assert z1.json()["score"] == 0.0
    assert z1.json()["verdict"] == "Hors sujet"
    assert z1.json()["provider_used"] == "gemini"
    assert z1.json()["used_fallback"] is False
    assert len(zero_calls) == 1

    z2 = client.post("/api/ai/match/opp-zero")
    assert z2.json()["score"] == 0.0
    assert z2.json()["provider_used"] == "gemini"
    assert len(zero_calls) == 1

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    z3 = client.post("/api/ai/match/opp-zero")
    assert z3.json()["score"] == 0.0
    assert z3.json()["provider_used"] == "gemini"
    assert len(zero_calls) == 1

    # Config isolation: non-Gemini ai_provider or auto+sk- key + OPENAI_API_KEY must NOT call Gemini
    monkeypatch.setenv("GEMINI_API_KEY", "env-gem-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")
    for prov, key in (
        ("openai", "sk-cfg-openai"),
        ("ollama", "ollama-tok"),
        ("heuristic", "ignored-key"),
        ("auto", "sk-auto-openai"),
    ):
        save_config(
            JobbyConfig(
                ai_provider=prov,
                ai_api_key=key,
                database_path=str(db_path),
                digest_output_path=str(tmp_path / "digest.md"),
            ),
            config_path=cfg_path,
        )
        iso_calls = install_httpx(zero_score_handler)
        r_m = client.post("/api/ai/match/opp-legacy-ph")
        r_c = client.post("/api/ai/cover-letter/opp-indep-a")
        r_b = client.post("/api/ai/rank-by-brief", json={"brief": "Lead Designer Figma"})
        assert len(iso_calls) == 0
        assert r_m.json()["provider_used"] == "heuristic"
        assert r_m.json()["used_fallback"] is True
        assert r_b.json()["provider_used"] == "heuristic"
        assert r_b.json()["used_fallback"] is True
        # opp-indep-a cover_letter is already cached as Gemini so r_c serves honest Gemini cache with 0 HTTP calls
        assert r_c.json()["provider_used"] == "gemini"

    # config.ai_api_key="   " with ai_provider="gemini" uses GEMINI_API_KEY env var; explicit config.ai_api_key works without env
    save_config(
        JobbyConfig(
            ai_provider="gemini",
            ai_api_key="   ",
            database_path=str(db_path),
            digest_output_path=str(tmp_path / "digest.md"),
        ),
        config_path=cfg_path,
    )
    ws_calls = install_httpx(zero_score_handler)
    client.post("/api/ai/match/opp-legacy-ph")
    assert ws_calls[0]["headers"] == {"x-goog-api-key": "env-gem-key"}

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    save_config(
        JobbyConfig(
            ai_provider="gemini",
            ai_api_key="cfg-only-gem-key",
            database_path=str(db_path),
            digest_output_path=str(tmp_path / "digest.md"),
        ),
        config_path=cfg_path,
    )
    cfg_calls = install_httpx(zero_score_handler)
    client.post("/api/ai/rank-by-brief", json={"brief": "Lead Designer Figma"})
    assert cfg_calls[0]["headers"] == {"x-goog-api-key": "cfg-only-gem-key"}


