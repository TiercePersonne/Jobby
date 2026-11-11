import re
import pytest
from fastapi.testclient import TestClient
from jobby.server import app


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


