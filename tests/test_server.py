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



