import re
import pytest
from fastapi.testclient import TestClient
from jobby.models import JobOpportunity
from jobby.server import app
from jobby.storage import Storage


@pytest.fixture
def client():
    return TestClient(app)


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Jobby" in response.text
    assert "/manifest.json" in response.text
    assert "/sw.js" in response.text


def test_manifest_endpoint(client):
    response = client.get("/manifest.json")
    assert response.status_code == 200
    data = response.json()
    assert data["short_name"] == "Jobby"
    assert data["display"] == "standalone"
    assert len(data["icons"]) >= 2


def test_service_worker_endpoint(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "addEventListener" in response.text
    assert "CACHE_NAME" in response.text


def test_api_config(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "target_roles" in data
    assert "skills_keywords" in data


def test_api_opportunities(client):
    response = client.get("/api/opportunities")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_api_stats(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data


def test_basic_auth_middleware(monkeypatch):
    monkeypatch.setenv("JOBBY_AUTH_USER", "admin")
    monkeypatch.setenv("JOBBY_AUTH_PASSWORD", "secret123")
    
    with TestClient(app) as auth_client:
        # Without credentials -> 401
        res = auth_client.get("/")
        assert res.status_code == 401
        assert "Basic" in res.headers.get("www-authenticate", "")

        # With wrong credentials -> 401
        res = auth_client.get("/", auth=("admin", "wrong"))
        assert res.status_code == 401

        # With valid credentials -> 200
        res = auth_client.get("/", auth=("admin", "secret123"))
        assert res.status_code == 200


def test_index_shell_warm_porcelain_structure(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Warm Porcelain stylesheet & ES6 module entrypoint
    assert '/static/css/tokens.css' in html
    assert '<script type="module" src="/static/js/app.js"></script>' in html

    # 4 views & centered modals included from partials
    for element_id in [
        'id="view-feed"',
        'id="view-kanban"',
        'id="view-profile"',
        'id="view-settings"',
        'id="modal-hunt"',
        'id="modal-letter"',
        'id="toast-container"',
    ]:
        assert element_id in html

    # Exclusive header items
    for header_marker in [
        'id="nav-feed"',
        'id="nav-kanban"',
        'id="kanban-badge"',
        'id="nav-profile"',
        'id="btn-open-hunt-modal"',
        'id="nav-settings"',
        "Actualiser la chasse",
        "Suivi Kanban",
        "Mon Profil",
    ]:
        assert header_marker in html

    # Exclusion of legacy buttons, mockup switcher & forbidden layout artifacts
    for forbidden in [
        "open-user-auth-btn",
        "header-stats",
        "pwa-install-btn",
        "open-profile-modal-btn",
        "open-auth-modal-btn",
        "theme-switcher",
        "gstatic",
        "max-w-5xl",
        "hidden md:flex",
        "onclick=",
        "text-[9px]",
        "text-[10px]",
        "text-[11px]",
    ]:
        assert forbidden not in html

    # No inline <script> blocks (every <script tag must reference external src)
    script_tags = re.findall(r"<script\b[^>]*>", html, flags=re.IGNORECASE)
    assert len(script_tags) == 2
    assert all('src="' in tag for tag in script_tags)


def test_static_modular_assets_served(client):
    css_resp = client.get("/static/css/tokens.css")
    assert css_resp.status_code == 200
    for token in ["#F6F5F2", "#FDFCFB", "#1C1917", "#15803D", "Inter"]:
        assert token in css_resp.text

    js_modules = [
        "/static/js/app.js",
        "/static/js/api.js",
        "/static/js/state.js",
        "/static/js/utils.js",
        "/static/js/views/feed.js",
        "/static/js/views/kanban.js",
        "/static/js/views/profile.js",
        "/static/js/views/settings.js",
    ]
    for path in js_modules:
        resp = client.get(path)
        assert resp.status_code == 200, f"Expected 200 for {path}"
        assert "javascript" in resp.headers.get("content-type", "").lower()


def test_manifest_and_sw_warm_porcelain(client):
    manifest_resp = client.get("/manifest.json")
    assert manifest_resp.status_code == 200
    data = manifest_resp.json()
    assert data["background_color"] == "#F6F5F2"
    assert data["theme_color"] == "#FDFCFB"

    sw_resp = client.get("/sw.js")
    assert sw_resp.status_code == 200
    assert "jobby-v4" in sw_resp.text


def test_opportunity_seen_and_mark_all_seen_endpoints(client, monkeypatch, tmp_path):
    db_path = tmp_path / "server_seen.db"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))

    storage = Storage(db_path=db_path)
    storage.upsert_opportunity(
        JobOpportunity(
            id="srv-opp-1",
            source="linkedin",
            title="Senior Python",
            url="https://linkedin.com/jobs/view/1",
        )
    )
    storage.upsert_opportunity(
        JobOpportunity(
            id="srv-opp-2",
            source="wttj",
            title="Staff Designer",
            url="https://wttj.co/jobs/2",
        )
    )

    list_resp = client.get("/api/opportunities")
    assert list_resp.status_code == 200
    by_id = {item["id"]: item for item in list_resp.json()}
    assert by_id["srv-opp-1"]["is_seen"] is False
    assert by_id["srv-opp-2"]["is_seen"] is False

    # Missing opp_id -> 404
    missing_resp = client.patch("/api/opportunities/does-not-exist/seen")
    assert missing_resp.status_code == 404

    # PATCH without body defaults to is_seen=True
    patch_resp = client.patch("/api/opportunities/srv-opp-1/seen")
    assert patch_resp.status_code == 200
    assert patch_resp.json() == {"status": "updated", "id": "srv-opp-1", "is_seen": True}

    by_id_after_patch = {item["id"]: item for item in client.get("/api/opportunities").json()}
    assert by_id_after_patch["srv-opp-1"]["is_seen"] is True
    assert by_id_after_patch["srv-opp-2"]["is_seen"] is False

    # PATCH with explicit body can toggle is_seen=False
    unsee_resp = client.patch("/api/opportunities/srv-opp-1/seen", json={"is_seen": False})
    assert unsee_resp.status_code == 200
    assert unsee_resp.json()["is_seen"] is False

    # PATCH with empty object or invalid payload types returns 422
    assert client.patch("/api/opportunities/srv-opp-1/seen", json={}).status_code == 422
    assert client.patch("/api/opportunities/srv-opp-1/seen", json={"is_seen": "not-a-bool"}).status_code == 422
    assert client.patch("/api/opportunities/srv-opp-1/seen", json={"is_seen": []}).status_code == 422

    # POST mark-all-seen marks all remaining opportunities seen
    mark_all_resp = client.post("/api/opportunities/mark-all-seen")
    assert mark_all_resp.status_code == 200
    assert mark_all_resp.json() == {"status": "updated", "updated_count": 2}

    by_id_final = {item["id"]: item for item in client.get("/api/opportunities").json()}
    assert by_id_final["srv-opp-1"]["is_seen"] is True
    assert by_id_final["srv-opp-2"]["is_seen"] is True


def test_feed_filter_bar_warm_porcelain_markup(client):
    html = client.get("/").text
    for marker in [
        'id="feed-filter-bar"',
        'id="filter-only-new"',
        'id="btn-mark-all-seen"',
        'id="btn-sources-popover"',
        'id="popover-sources"',
        'id="filter-sort"',
        'id="filter-hide-alternance"',
        "Nouvelles",
        "Tout marquer comme vu",
        "100% Remote",
        "Hybride",
        "Sur site",
        "EU hors FR",
        "Posts Réseau",
        "Plus récentes",
        "Masquer alternances",
        "Tiroir Brief IA (Epic 2)",
    ]:
        assert marker in html

    # No free-text search input inside the filter bar
    filter_section = html.split('id="feed-filter-bar"', 1)[1].split('id="feed-list"', 1)[0]
    assert 'type="text"' not in filter_section
    assert 'type="search"' not in filter_section


def test_brief_drawer_warm_porcelain_markup(client):
    html = client.get("/").text
    assert "Tiroir Brief IA (Epic 2)" in html

    feed_section = html.split('id="view-feed"', 1)[1].split('id="view-kanban"', 1)[0]
    assert 'id="brief-box"' in feed_section
    assert feed_section.index('id="brief-box"') < feed_section.index('id="feed-filter-bar"')

    brief_section = feed_section.split('id="brief-box"', 1)[1].split('id="feed-filter-bar"', 1)[0]
    for marker in [
        'id="brief-toggle-btn"',
        'aria-labelledby="brief-label brief-toggle-text"',
        'aria-describedby="brief-preview-text"',
        'id="brief-dot"',
        'id="brief-preview-text"',
        'id="brief-drawer"',
        'role="region" aria-labelledby="brief-label"',
        'id="brief-input"',
        'id="btn-prefill"',
        'id="btn-run-gemini"',
        "Déplier &amp; affiner",
        "Pré-remplir depuis Mon Profil",
        "Re-calculer le classement Gemini",
    ]:
        assert marker in brief_section

    assert "onclick=" not in brief_section
    assert "<script" not in brief_section.lower()

    drawer_section = brief_section.split('id="brief-drawer"', 1)[1]
    assert "hidden" in brief_section.split('id="brief-drawer"', 1)[1].split(">", 1)[0]
    assert len(re.findall(r"<button\b", drawer_section, flags=re.IGNORECASE)) == 2

    css_text = client.get("/static/css/tokens.css").text
    for css_marker in [
        ".jp-brief-box",
        ".jp-brief-header",
        ".jp-brief-header:focus-visible",
        "#brief-drawer.hidden",
        ".jp-btn-secondary:disabled",
    ]:
        assert css_marker in css_text

    api_js = client.get("/static/js/api.js").text
    assert "export function rankOpportunitiesByBrief" in api_js


def test_profile_view_warm_porcelain_markup(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="view-profile"' in html
    profile_section = html.split('id="view-profile"', 1)[1].split('id="view-settings"', 1)[0]

    # Warm Porcelain constraints
    assert "jp-container-narrow" in html.split('id="view-profile"')[0].split("<section")[-1] or "jp-container-narrow" in profile_section[:200]
    assert "onclick=" not in profile_section
    assert "<script" not in profile_section.lower()
    assert "text-[9px]" not in profile_section
    assert "text-[10px]" not in profile_section
    assert "text-[11px]" not in profile_section

    # Key panels and IDs
    for marker in [
        'id="profile-form"',
        'id="profile-cv-dropzone"',
        'id="profile-cv-file"',
        'id="profile-cv-status"',
        'id="profile-cv-filename"',
        'id="profile-cv-characters"',
        'id="profile-cv-preview"',
        'id="btn-profile-browse-cv"',
        'id="profile-portfolio-url"',
        'id="profile-github-url"',
        'id="profile-linkedin-url"',
        'id="profile-target-roles"',
        'id="profile-skills"',
        'id="profile-remote-preference"',
        'id="profile-min-salary"',
        'id="profile-bio"',
        'id="profile-writing-sample"',
        'id="profile-writing-tone"',
        'id="btn-save-profile"',
        'id="profile-save-status"',
        'tabindex="-1"',
        "CV &amp; Documents",
        "Liens professionnels",
        "Positionnement &amp; Compétences",
        "Ma Voix",
    ]:
        assert marker in profile_section

    # CSS check for dropzone
    css_text = client.get("/static/css/tokens.css").text
    assert ".jp-dropzone" in css_text

    # JS export checks
    api_js = client.get("/static/js/api.js").text
    assert "export function saveProfile" in api_js
    assert "export function uploadProfileCv" in api_js


def test_profile_put_and_cv_upload_endpoints(client, monkeypatch, tmp_path):
    from jobby.config import JobbyConfig, save_config

    db_path = tmp_path / "profile_test.db"
    cfg_path = tmp_path / "jobby_config.yaml"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    save_config(JobbyConfig(database_path=str(db_path), target_roles=["Designer"], skills_keywords=["Figma"]), cfg_path)

    # 1. Cold start GET /api/profile
    get_res = client.get("/api/profile")
    assert get_res.status_code == 200
    assert get_res.json()["has_cv"] is False
    assert get_res.json()["profile"]["target_roles"] == ["Designer"]

    # 2. PUT /api/profile updates profile and trims values
    put_res = client.put(
        "/api/profile",
        json={
            "portfolio_url": "  https://example.com/portfolio  ",
            "github_url": "https://github.com/test",
            "linkedin_url": "https://linkedin.com/in/test",
            "target_roles": ["  Lead Designer  ", "Principal"],
            "skills": ["Figma", "  Tokens "],
            "remote_preference": "full",
            "min_salary": 72000,
            "bio": "  Bio courte  ",
            "writing_sample": "  Exemple de style  ",
            "writing_tone": "  sobre et direct  ",
        },
    )
    assert put_res.status_code == 200
    prof = put_res.json()["profile"]
    assert prof["portfolio_url"] == "https://example.com/portfolio"
    assert prof["target_roles"] == ["Lead Designer", "Principal"]
    assert prof["skills"] == ["Figma", "Tokens"]
    assert prof["bio"] == "Bio courte"
    assert prof["writing_sample"] == "Exemple de style"
    assert prof["writing_tone"] == "sobre et direct"
    assert prof["min_salary"] == 72000
    assert prof["remote_preference"] == "full"

    # 3. Upload CV via POST /api/profile/upload-cv
    cv_content = b"Candidate CV Text\nExperience in Figma and UI design."
    up_res = client.post(
        "/api/profile/upload-cv",
        files={"file": ("mon_cv.txt", cv_content, "text/plain")},
    )
    assert up_res.status_code == 200
    up_data = up_res.json()
    assert up_data["status"] == "success"
    assert up_data["filename"] == "mon_cv.txt"
    assert up_data["characters_extracted"] > 0
    assert "Candidate CV Text" in up_data["preview"]
    assert "profile" in up_data

    # Verify profile preserved links and skills after CV upload
    re_get = client.get("/api/profile").json()
    assert re_get["has_cv"] is True
    assert re_get["profile"]["cv_filename"] == "mon_cv.txt"
    assert re_get["profile"]["portfolio_url"] == "https://example.com/portfolio"
    assert re_get["profile"]["target_roles"] == ["Lead Designer", "Principal"]

    # 4. Error handling on upload: empty file -> 400; missing filename -> 400; > 10MB -> 400
    empty_res = client.post(
        "/api/profile/upload-cv",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert empty_res.status_code == 400
    assert "Fichier vide" in empty_res.json()["detail"]

    missing_name_res = client.post(
        "/api/profile/upload-cv",
        files={"file": ("   ", b"Valid content", "text/plain")},
    )
    assert missing_name_res.status_code == 400
    assert "Nom de fichier manquant" in missing_name_res.json()["detail"]

    huge_content = b"X" * (10 * 1024 * 1024 + 1)
    huge_res = client.post(
        "/api/profile/upload-cv",
        files={"file": ("huge.txt", huge_content, "text/plain")},
    )
    assert huge_res.status_code == 400
    assert "10 Mo" in huge_res.json()["detail"]

    # 5. Negative salary clamped to 0
    neg_salary_res = client.put("/api/profile", json={"min_salary": -1000})
    assert neg_salary_res.status_code == 200
    assert neg_salary_res.json()["profile"]["min_salary"] == 0

    # 6. Preserves empty lists [] rather than resurrecting config defaults on partial updates
    empty_lists_res = client.put("/api/profile", json={"target_roles": [], "skills": []})
    assert empty_lists_res.status_code == 200
    assert empty_lists_res.json()["profile"]["target_roles"] == []
    assert empty_lists_res.json()["profile"]["skills"] == []

    partial_res = client.put("/api/profile", json={"bio": "New bio"})
    assert partial_res.status_code == 200
    assert partial_res.json()["profile"]["target_roles"] == []
    assert partial_res.json()["profile"]["skills"] == []
    assert partial_res.json()["profile"]["bio"] == "New bio"


def test_opportunity_status_canonicalization_and_seen_flag(client, monkeypatch, tmp_path):
    db_path = tmp_path / "server_status.db"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))

    storage = Storage(db_path=db_path)
    storage.upsert_opportunity(
        JobOpportunity(
            id="st-1",
            source="wttj",
            title="Lead Designer",
            url="https://wttj.co/jobs/st-1",
            status="new",
            is_seen=False,
        )
    )
    storage.upsert_opportunity(
        JobOpportunity(
            id="st-2",
            source="linkedin",
            title="Staff Engineer",
            url="https://linkedin.com/jobs/view/st-2",
            status="new",
            is_seen=False,
        )
    )

    # Seed legacy SQLite rows with 'favorite' and 'saved' directly
    with storage._connection() as conn:
        conn.execute(
            """
            INSERT INTO opportunities (
                id, source, opportunity_type, title, url, discovered_at, status, is_seen, dedup_key
            ) VALUES
                ('legacy-fav', 'indeed', 'offer', 'Legacy Fav', 'https://example.com/fav', '2026-11-11T10:00:00+00:00', 'favorite', 1, 'a:fav'),
                ('legacy-sav', 'indeed', 'offer', 'Legacy Sav', 'https://example.com/sav', '2026-11-11T10:00:00+00:00', 'saved', 1, 'a:sav')
            """
        )
        conn.commit()

    # Reading back from Storage / API normalizes favorite/saved -> kept
    by_id = {item["id"]: item for item in client.get("/api/opportunities").json()}
    assert by_id["legacy-fav"]["status"] == "kept"
    assert by_id["legacy-sav"]["status"] == "kept"
    kept_list = storage.list_opportunities(status="kept")
    assert {o.id for o in kept_list} == {"legacy-fav", "legacy-sav"}

    # Invalid status -> 400; missing opp -> 404
    assert client.patch("/api/opportunities/st-1/status", json={"status": "invalid"}).status_code == 400
    assert client.patch("/api/opportunities/missing/status", json={"status": "kept"}).status_code == 404

    # 'favorite' and 'saved' on PATCH /status canonicalize to 'kept' and set is_seen=True
    fav_resp = client.patch("/api/opportunities/st-1/status", json={"status": "favorite"})
    assert fav_resp.status_code == 200
    assert fav_resp.json() == {
        "status": "updated",
        "id": "st-1",
        "new_status": "kept",
        "is_seen": True,
    }
    assert storage.get_opportunity("st-1").status == "kept"
    assert storage.get_opportunity("st-1").is_seen is True

    sav_resp = client.patch("/api/opportunities/st-2/status", json={"status": "saved"})
    assert sav_resp.status_code == 200
    assert sav_resp.json() == {
        "status": "updated",
        "id": "st-2",
        "new_status": "kept",
        "is_seen": True,
    }

    # Resetting to 'new' returns new_status='new' and preserves is_seen=True
    reset_resp = client.patch("/api/opportunities/st-1/status", json={"status": "new"})
    assert reset_resp.status_code == 200
    assert reset_resp.json() == {
        "status": "updated",
        "id": "st-1",
        "new_status": "new",
        "is_seen": True,
    }


def test_search_endpoint_validation_and_scoring(client, monkeypatch, tmp_path):
    from jobby.config import JobbyConfig, save_config
    from jobby.providers import registry

    db_path = tmp_path / "search.db"
    cfg_path = tmp_path / "jobby_config.yaml"
    digest_path = tmp_path / "digest.md"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    save_config(
        JobbyConfig(
            target_roles=["Product Designer"],
            preferred_locations=["Paris"],
            database_path=str(db_path),
            digest_output_path=str(digest_path),
        ),
        config_path=cfg_path,
    )

    # Validation bounds [1, 200] for max_results -> 422
    assert client.post("/api/search", json={"max_results": 0}).status_code == 422
    assert client.post("/api/search", json={"max_results": 201}).status_code == 422
    assert client.post("/api/search", json={"max_results": -1}).status_code == 422

    captured = {}

    async def fake_search_all(params, source_names=None, fail_silently=True):
        captured["params"] = params
        captured["source_names"] = source_names
        return [
            JobOpportunity(
                id="search-opp-1",
                source="wttj",
                title="Lead Product Designer",
                company="Porcelain Studio",
                location="Paris, France",
                remote_policy="full",
                url="https://wttj.co/jobs/search-1",
                description="Design System Figma en full remote.",
            )
        ]

    monkeypatch.setattr(registry, "search_all", fake_search_all)

    resp = client.post(
        "/api/search",
        json={
            "keywords": ["Product Designer"],
            "location": "Paris",
            "remote_only": False,
            "max_results": 25,
            "sources": ["wttj", "linkedin"],
            "include_posts": False,
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total_found"] == 1
    assert payload["new_count"] == 1
    assert captured["source_names"] == ["wttj", "linkedin"]
    assert captured["params"].include_posts is False
    assert payload["opportunities"][0]["score"] is not None
    assert payload["opportunities"][0]["score"] > 0
    assert len(payload["opportunities"][0]["score_reasons"]) >= 1

    # Persisted with assigned score and is_seen=False in SQLite
    stored = client.get("/api/opportunities").json()
    assert len(stored) == 1
    assert stored[0]["id"] == "search-opp-1"
    assert stored[0]["score"] == payload["opportunities"][0]["score"]
    assert stored[0]["is_seen"] is False

    # Repeat scrape returns new_count == 0
    repeat_resp = client.post(
        "/api/search",
        json={"keywords": ["Product Designer"], "location": "Paris", "sources": ["wttj"]},
    )
    assert repeat_resp.status_code == 200
    assert repeat_resp.json()["total_found"] == 1
    assert repeat_resp.json()["new_count"] == 0

    # Whitespace keywords/location fall back to config defaults on SearchParams
    ws_resp = client.post(
        "/api/search",
        json={"keywords": ["   ", ""], "location": "   ", "sources": ["wttj"]},
    )
    assert ws_resp.status_code == 200
    assert captured["params"].keywords == ["Product Designer"]
    assert captured["params"].location == "Paris"

    # remote_only=True applies remote_only scoring rule (+30 on full remote, -30 on non-remote)
    async def fake_search_remote(params, source_names=None, fail_silently=True):
        captured["params"] = params
        return [
            JobOpportunity(
                id="search-onsite-1",
                source="wttj",
                title="Lead Product Designer",
                company="OnSite Co",
                location="Paris, France",
                remote_policy="onsite",
                url="https://wttj.co/jobs/onsite-1",
                description="Présentiel 5j/5.",
            )
        ]

    monkeypatch.setattr(registry, "search_all", fake_search_remote)
    remote_resp = client.post(
        "/api/search",
        json={
            "keywords": ["Product Designer"],
            "location": "Paris",
            "remote_only": True,
            "sources": ["wttj"],
        },
    )
    assert remote_resp.status_code == 200
    assert remote_resp.json()["opportunities"][0]["score"] == 25.0  # 55 (role) - 30 (not remote)

    # sources: [] returns empty immediately without calling registry.search_all
    called_with_empty = {"called": False}

    async def fail_if_called(*args, **kwargs):
        called_with_empty["called"] = True
        return []

    monkeypatch.setattr(registry, "search_all", fail_if_called)
    empty_src_resp = client.post("/api/search", json={"sources": []})
    assert empty_src_resp.status_code == 200
    assert empty_src_resp.json() == {"total_found": 0, "new_count": 0, "opportunities": []}
    assert called_with_empty["called"] is False


def test_webhook_trigger_search_persists_score_and_stats_merges_kept(client, monkeypatch, tmp_path):
    from jobby.config import JobbyConfig, save_config
    from jobby.providers import registry

    db_path = tmp_path / "webhook_stats.db"
    cfg_path = tmp_path / "webhook_config.yaml"
    digest_path = tmp_path / "webhook_digest.md"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    monkeypatch.delenv("JOBBY_WEBHOOK_SECRET", raising=False)
    save_config(
        JobbyConfig(
            target_roles=["Product Designer"],
            preferred_locations=["Paris"],
            database_path=str(db_path),
            digest_output_path=str(digest_path),
        ),
        config_path=cfg_path,
    )

    async def fake_webhook_search(params, source_names=None, fail_silently=True):
        return [
            JobOpportunity(
                id="wh-opp-1",
                source="wttj",
                title="Lead Product Designer",
                company="Studio Hook",
                location="Paris, France",
                url="https://wttj.co/jobs/wh-1",
                description="Design System en Python & Figma.",
            )
        ]

    monkeypatch.setattr(registry, "search_all", fake_webhook_search)

    wh_resp = client.post("/api/webhooks/trigger-search")
    assert wh_resp.status_code == 200
    assert wh_resp.json()["total_scraped"] == 1
    assert wh_resp.json()["new_opportunities"] == 1

    storage = Storage(db_path=db_path)
    saved_opp = storage.get_opportunity("wh-opp-1")
    assert saved_opp is not None
    assert saved_opp.score is not None and saved_opp.score > 0
    assert len(saved_opp.score_reasons) >= 1

    # Seed legacy favorite/saved rows and verify GET /api/stats merges them into 'kept'
    with storage._connection() as conn:
        conn.execute(
            """
            INSERT INTO opportunities (
                id, source, opportunity_type, title, url, discovered_at, status, is_seen, dedup_key
            ) VALUES
                ('st-fav', 'linkedin', 'offer', 'Fav', 'https://example.com/1', '2026-11-11T10:00:00+00:00', 'favorite', 1, 'a:1'),
                ('st-sav', 'linkedin', 'offer', 'Sav', 'https://example.com/2', '2026-11-11T10:00:00+00:00', 'saved', 1, 'a:2')
            """
        )
        conn.commit()
    storage.update_status("wh-opp-1", "kept")

    stats = client.get("/api/stats").json()
    assert stats["by_status"]["kept"] == 3
    assert "favorite" not in stats["by_status"]
    assert "saved" not in stats["by_status"]


def test_api_ai_test_observability_gemini_gemma_and_heuristic(client, monkeypatch, tmp_path):
    import json
    from jobby.config import JobbyConfig, save_config

    db_path = tmp_path / "ai_test.db"
    cfg_path = tmp_path / "ai_test_config.yaml"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    save_config(
        JobbyConfig(
            ai_provider="gemini",
            ai_api_key="cfg-gemini-key",
            ai_model="gemini-2.5-flash",
            database_path=str(db_path),
            digest_output_path=str(tmp_path / "digest.md"),
        ),
        config_path=cfg_path,
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

            async def post(self, url, json=None, headers=None, **kwargs):
                call = {"url": url, "json": json, "headers": headers, "timeout": self.timeout}
                recorded.append(call)
                return await handler(call)

        monkeypatch.setattr("jobby.ai_analyzer.httpx.AsyncClient", FakeClient)
        return recorded

    # 1. Gemini OK -> passes config.ai_model, thinkingBudget: 0, timeout >= 35.0
    async def gemini_ok(call):
        return FakeResp(
            200,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps({
                                        "intent": "candidate_seeking_alternance",
                                        "is_candidate": True,
                                        "is_alternance": True,
                                        "summary": "Candidat Data Python",
                                        "remote_policy": "unknown",
                                    })
                                }
                            ]
                        }
                    }
                ]
            },
        )

    ok_calls = install_httpx(gemini_ok)
    ok_resp = client.post("/api/ai/test", json={})
    assert ok_resp.status_code == 200
    ok_data = ok_resp.json()
    assert ok_data["provider"] == "gemini"
    assert ok_data["provider_used"] == "gemini"
    assert ok_data["used_fallback"] is False
    assert ok_data["fallback_reason"] is None
    assert len(ok_calls) == 1
    assert "gemini-2.5-flash:generateContent" in ok_calls[0]["url"]
    assert "?key=" not in ok_calls[0]["url"]
    assert ok_calls[0]["headers"] == {"x-goog-api-key": "cfg-gemini-key"}
    assert ok_calls[0]["timeout"] >= 35.0
    assert ok_calls[0]["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}

    # 2. Gemini HTTP 404 -> Gemma fallback (untouched payload, used_fallback=True, not labeled "gemini")
    async def gemma_fallback(call):
        if "gemini-2.5-flash" in call["url"]:
            return FakeResp(404, {"error": "model not found"})
        return FakeResp(
            200,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps({
                                        "intent": "candidate_seeking_alternance",
                                        "is_candidate": True,
                                        "is_alternance": True,
                                        "summary": "Gemma fallback",
                                        "remote_policy": "unknown",
                                    })
                                }
                            ]
                        }
                    }
                ]
            },
        )

    gemma_calls = install_httpx(gemma_fallback)
    gemma_resp = client.post("/api/ai/test", json={})
    assert gemma_resp.status_code == 200
    gemma_data = gemma_resp.json()
    assert gemma_data["provider"] == "gemini-gemma"
    assert gemma_data["provider_used"] == "gemini-gemma"
    assert gemma_data["used_fallback"] is True
    assert "404" in gemma_data["fallback_reason"]
    assert len(gemma_calls) == 2
    assert "gemma-4-26b-a4b-it:generateContent" in gemma_calls[1]["url"]
    assert gemma_calls[1]["headers"] == {"x-goog-api-key": "cfg-gemini-key"}
    assert "?key=" not in gemma_calls[1]["url"]
    assert "thinkingConfig" not in json.dumps(gemma_calls[1]["json"])

    # 3. Missing key -> heuristic fallback with honest provenance
    save_config(
        JobbyConfig(
            ai_provider="gemini",
            ai_api_key=None,
            ai_model="gemini-2.5-flash",
            database_path=str(db_path),
            digest_output_path=str(tmp_path / "digest.md"),
        ),
        config_path=cfg_path,
    )
    heur_resp = client.post("/api/ai/test", json={})
    assert heur_resp.status_code == 200
    heur_data = heur_resp.json()
    assert heur_data["provider"] == "heuristic"
    assert heur_data["provider_used"] == "heuristic"
    assert heur_data["used_fallback"] is True
    assert heur_data["fallback_reason"]

    # 4. Config ai_provider="ollama" + custom ai_ollama_url respected on {} and whitespace body
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-should-be-ignored")
    save_config(
        JobbyConfig(
            ai_provider="ollama",
            ai_ollama_url="http://ollama.internal:11434",
            ai_model="llama3",
            database_path=str(db_path),
            digest_output_path=str(tmp_path / "digest.md"),
        ),
        config_path=cfg_path,
    )

    async def ollama_ok(call):
        return FakeResp(
            200,
            {"response": json.dumps({"intent": "hiring_job", "is_candidate": False, "is_alternance": False, "summary": "Ollama OK", "remote_policy": "full"})},
        )

    ollama_calls = install_httpx(ollama_ok)
    for body in ({}, {"provider": "   ", "ollama_url": "  "}):
        ol_resp = client.post("/api/ai/test", json=body)
        assert ol_resp.status_code == 200
        ol_data = ol_resp.json()
        assert ol_data["provider"] == "ollama"
        assert ol_data["provider_used"] == "ollama"
        assert ol_data["used_fallback"] is False
        assert ol_data["fallback_reason"] is None
    assert len(ollama_calls) == 2
    assert all(c["url"] == "http://ollama.internal:11434/api/generate" for c in ollama_calls)

    # 5. Config ai_provider="openai" + sk- key respected on {} (does NOT call Gemini)
    save_config(
        JobbyConfig(
            ai_provider="openai",
            ai_api_key="sk-config-openai",
            ai_model="gpt-4o-mini",
            database_path=str(db_path),
            digest_output_path=str(tmp_path / "digest.md"),
        ),
        config_path=cfg_path,
    )

    async def openai_ok(call):
        return FakeResp(
            200,
            {"choices": [{"message": {"content": json.dumps({"intent": "hiring_job", "is_candidate": False, "is_alternance": False, "summary": "OpenAI OK", "remote_policy": "full"})}}]},
        )

    oa_calls = install_httpx(openai_ok)
    oa_resp = client.post("/api/ai/test", json={})
    assert oa_resp.status_code == 200
    oa_data = oa_resp.json()
    assert oa_data["provider"] == "openai"
    assert oa_data["provider_used"] == "openai"
    assert oa_data["used_fallback"] is False
    assert oa_calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert oa_calls[0]["headers"]["Authorization"] == "Bearer sk-config-openai"

    # 6. Config ai_provider="heuristic" runs local heuristic with used_fallback=False even if GEMINI_API_KEY exists
    save_config(
        JobbyConfig(
            ai_provider="heuristic",
            ai_api_key="some-key",
            database_path=str(db_path),
            digest_output_path=str(tmp_path / "digest.md"),
        ),
        config_path=cfg_path,
    )
    no_calls = install_httpx(gemini_ok)
    h_cfg_resp = client.post("/api/ai/test", json={})
    assert h_cfg_resp.status_code == 200
    h_cfg_data = h_cfg_resp.json()
    assert h_cfg_data["provider"] == "heuristic"
    assert h_cfg_data["provider_used"] == "heuristic"
    assert h_cfg_data["used_fallback"] is False
    assert h_cfg_data["fallback_reason"] is None
    assert len(no_calls) == 0

    # 7. Whitespace ai_api_key="   " with ai_provider="gemini" falls back cleanly to GEMINI_API_KEY env var
    save_config(
        JobbyConfig(
            ai_provider="gemini",
            ai_api_key="   ",
            ai_model="gemini-2.5-flash",
            database_path=str(db_path),
            digest_output_path=str(tmp_path / "digest.md"),
        ),
        config_path=cfg_path,
    )
    ws_calls = install_httpx(gemini_ok)
    ws_resp = client.post("/api/ai/test", json={})
    assert ws_resp.status_code == 200
    assert ws_resp.json()["provider_used"] == "gemini"
    assert ws_calls[0]["headers"] == {"x-goog-api-key": "env-gemini-should-be-ignored"}


def test_kanban_view_warm_porcelain_markup(client):
    """Verifies that SSR template renders the 4-column Warm Porcelain Kanban board and archived drawer."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert 'id="view-kanban"' in html
    assert 'jp-container-kanban' in html
    assert 'id="view-kanban-heading"' in html
    assert 'id="kanban-shell-summary"' in html

    # 4 active columns and counts
    for stage in ["saved", "applied", "interview", "offer"]:
        assert f'data-kanban-column="{stage}"' in html
        assert f'data-kanban-col-count="{stage}"' in html
        assert f'data-kanban-dropzone="{stage}"' in html
        assert f'data-kanban-cards="{stage}"' in html
        assert f'data-kanban-empty="{stage}"' in html

    # Dashed empty dropzone placeholder
    assert "Glissez une carte ici" in html

    # Archived / Rejected collapsible drawer
    assert 'id="kanban-archived-section"' in html
    assert 'id="kanban-archived-toggle"' in html
    assert 'id="kanban-archived-drawer"' in html
    assert 'data-kanban-col-count="rejected"' in html
    assert 'data-kanban-dropzone="rejected"' in html
    assert 'data-kanban-cards="rejected"' in html


def test_kanban_application_endpoints_and_two_way_sync(tmp_path, monkeypatch):
    """Validates /api/applications status and notes endpoints including two-way sync with opportunities."""
    from jobby.config import JobbyConfig, save_config
    from jobby.models import JobOpportunity

    db_path = tmp_path / "kanban_test.db"
    cfg_path = tmp_path / "jobby.json"
    save_config(JobbyConfig(database_path=str(db_path)), config_path=cfg_path)
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))

    storage = Storage(db_path)
    opp = JobOpportunity(
        id="opp-kanban-1",
        title="Lead UX Engineer",
        company="Warm Studio",
        location="Paris (Full Remote)",
        source="wttj",
        url="https://example.com/job/1",
        status="kept",
        is_seen=True,
    )
    storage.upsert_opportunity(opp)

    with TestClient(app) as test_client:
        # 1. Listing applications auto-syncs kept opportunity into a saved application
        apps_res = test_client.get("/api/applications")
        assert apps_res.status_code == 200
        apps_data = apps_res.json()
        assert len(apps_data) == 1
        app_entry = apps_data[0]
        app_id = app_entry["id"]
        assert app_entry["status"] == "saved"
        assert app_entry["opportunity_id"] == "opp-kanban-1"

        # 2. Update status to 'applied' -> syncs opportunity to 'applied'
        status_res = test_client.patch(f"/api/applications/{app_id}/status", json={"status": "applied"})
        assert status_res.status_code == 200
        assert status_res.json()["new_status"] == "applied"
        assert storage.get_opportunity("opp-kanban-1").status == "applied"

        # 3. Update status to 'interview' -> syncs opportunity to 'interview'
        status_res = test_client.patch(f"/api/applications/{app_id}/status", json={"status": "interview"})
        assert status_res.status_code == 200
        assert status_res.json()["new_status"] == "interview"
        assert storage.get_opportunity("opp-kanban-1").status == "interview"

        # 4. Update status to 'offer' -> syncs opportunity to 'offer'
        status_res = test_client.patch(f"/api/applications/{app_id}/status", json={"status": "offer"})
        assert status_res.status_code == 200
        assert status_res.json()["new_status"] == "offer"
        assert storage.get_opportunity("opp-kanban-1").status == "offer"

        # 5. Update status to 'rejected' -> syncs opportunity to 'archived'
        status_res = test_client.patch(f"/api/applications/{app_id}/status", json={"status": "rejected"})
        assert status_res.status_code == 200
        assert status_res.json()["new_status"] == "rejected"
        assert storage.get_opportunity("opp-kanban-1").status == "archived"

        # 6. Update status back to 'saved' -> syncs opportunity to 'kept'
        status_res = test_client.patch(f"/api/applications/{app_id}/status", json={"status": "saved"})
        assert status_res.status_code == 200
        assert status_res.json()["new_status"] == "saved"
        assert storage.get_opportunity("opp-kanban-1").status == "kept"

        # 7. Update notes -> persists in DB
        notes_res = test_client.patch(f"/api/applications/{app_id}/notes", json={"notes": "Relance effectuée auprès de Julie RH."})
        assert notes_res.status_code == 200
        saved_app = storage.get_application(app_id, "usr_user_jobby_local") or storage.get_applications("usr_user_jobby_local") or storage.get_applications(storage.get_user_by_email("user@jobby.local").id)
        current_apps = test_client.get("/api/applications").json()
        assert current_apps[0]["notes"] == "Relance effectuée auprès de Julie RH."

        # 8. Invalid status returns 400
        bad_status = test_client.patch(f"/api/applications/{app_id}/status", json={"status": "non_existent"})
        assert bad_status.status_code == 400

        # 9. Non-existent app returns 404
        not_found = test_client.patch("/api/applications/non-existent-id/status", json={"status": "applied"})
        assert not_found.status_code == 404

        # 10. Optimistic application ID (app_optimistic_{opp_id}) for PATCH status & notes
        opp2 = JobOpportunity(
            id="opp-kanban-2",
            title="Design System Lead",
            company="Figma",
            location="Remote",
            source="wttj",
            url="https://example.com/job/2",
            status="kept",
            is_seen=True,
        )
        storage.upsert_opportunity(opp2)

        opt_status = test_client.patch("/api/applications/app_optimistic_opp-kanban-2/status", json={"status": "interview"})
        assert opt_status.status_code == 200
        assert storage.get_opportunity("opp-kanban-2").status == "interview"

        opt_notes = test_client.patch("/api/applications/app_optimistic_opp-kanban-2/notes", json={"notes": "Note optimiste enregistrée"})
        assert opt_notes.status_code == 200
        current_apps = test_client.get("/api/applications").json()
        app2_entry = next(a for a in current_apps if a["opportunity_id"] == "opp-kanban-2")
        assert app2_entry["notes"] == "Note optimiste enregistrée"

        # 11. DELETE on optimistic app ID resets opportunity status to 'new'
        opt_del = test_client.delete("/api/applications/app_optimistic_opp-kanban-2")
        assert opt_del.status_code == 200
        assert storage.get_opportunity("opp-kanban-2").status == "new"

        # 12. DELETE on optimistic app ID when no DB row existed resets opportunity to 'new'
        opp3 = JobOpportunity(
            id="opp-kanban-3",
            title="Staff Product Designer",
            company="Notion",
            location="Remote",
            source="wttj",
            url="https://example.com/job/3",
            status="kept",
            is_seen=True,
        )
        storage.upsert_opportunity(opp3)
        opt_del_norow = test_client.delete("/api/applications/app_optimistic_opp-kanban-3")
        assert opt_del_norow.status_code == 200
        assert storage.get_opportunity("opp-kanban-3").status == "new"

        # 13. PATCH /api/opportunities/{id}/status with 'offer' syncs to applications
        opp4 = JobOpportunity(
            id="opp-kanban-4",
            title="VP of Product",
            company="Datadog",
            location="Paris",
            source="wttj",
            url="https://example.com/job/4",
            status="new",
            is_seen=True,
        )
        storage.upsert_opportunity(opp4)
        opp_offer_res = test_client.patch("/api/opportunities/opp-kanban-4/status", json={"status": "offer"})
        assert opp_offer_res.status_code == 200
        assert opp_offer_res.json()["new_status"] == "offer"
        assert storage.get_opportunity("opp-kanban-4").status == "offer"
        current_apps = test_client.get("/api/applications").json()
        app4_entry = next(a for a in current_apps if a["opportunity_id"] == "opp-kanban-4")
        assert app4_entry["status"] == "offer"


def test_cover_letter_force_regenerate_and_cache(tmp_path, monkeypatch):
    """Verifies that POST /api/ai/cover-letter returns cached by default and recalculates with force=true."""
    from jobby.config import JobbyConfig, save_config
    from jobby.models import JobOpportunity, MatchEvaluation, User, UserProfile
    import jobby.server as server_module

    db_path = tmp_path / "cl_test.db"
    cfg_path = tmp_path / "jobby.json"
    save_config(JobbyConfig(database_path=str(db_path)), config_path=cfg_path)
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))

    storage = Storage(db_path)
    test_user = storage.ensure_user(
        User(
            id="usr_user_jobby_local",
            email="user@jobby.local",
            full_name="Mon Profil Candidat",
            password_hash="fake",
        )
    )
    user_id = test_user.id

    opp = JobOpportunity(
        id="opp-cl-1",
        title="Directeur Artistique Senior",
        company="Studio Graphique",
        location="Paris (Remote)",
        source="wttj",
        url="https://example.com/job/cl-1",
        status="kept",
        is_seen=True,
    )
    storage.upsert_opportunity(opp)

    profile = UserProfile(
        user_id=user_id,
        writing_sample="Mon style est direct, sobre, percutant et sans jargon.",
        writing_tone="naturel et percutant",
        target_roles=["Directeur Artistique"],
        skills=["Figma", "Branding"],
    )
    storage.save_profile(profile)

    # Seed cached evaluation with initial letter
    cached = MatchEvaluation(
        user_id=user_id,
        opportunity_id="opp-cl-1",
        score=95,
        verdict="Excellent match",
        cover_letter="Version 1 initiale en cache",
        provider_used="gemini",
        used_fallback=False,
    )
    storage.save_match_result(cached)
    storage.save_cover_letter_result(
        user_id=user_id,
        opportunity_id="opp-cl-1",
        cover_letter="Version 1 initiale en cache",
        provider_used="gemini",
        used_fallback=False,
    )

    call_count = {"count": 0}

    async def fake_generator(*args, **kwargs):
        call_count["count"] += 1
        return {
            "cover_letter": f"Version régénérée {call_count['count']}",
            "provider_used": "gemini",
            "used_fallback": False,
            "fallback_reason": None,
        }

    monkeypatch.setattr(server_module, "generate_tailored_cover_letter_with_meta", fake_generator)

    with TestClient(app) as test_client:
        # 1. First call without force: returns cached version 1 without calling generator
        res1 = test_client.post("/api/ai/cover-letter/opp-cl-1")
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["cover_letter"] == "Version 1 initiale en cache"
        assert call_count["count"] == 0

        # 2. Second call with force=true: ignores cache and regenerates
        res2 = test_client.post("/api/ai/cover-letter/opp-cl-1?force=true")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["cover_letter"] == "Version régénérée 1"
        assert call_count["count"] == 1

        # 3. Third call without force: returns newly persisted cover letter
        res3 = test_client.post("/api/ai/cover-letter/opp-cl-1")
        assert res3.status_code == 200
        data3 = res3.json()
        assert data3["cover_letter"] == "Version régénérée 1"
        assert call_count["count"] == 1





