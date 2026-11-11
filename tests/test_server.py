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

