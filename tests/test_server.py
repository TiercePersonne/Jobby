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



