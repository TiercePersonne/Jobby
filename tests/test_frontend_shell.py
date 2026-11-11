"""End-to-end Playwright browser tests for Story 1.1 Warm Porcelain shell & ES6 modules."""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from jobby.models import JobOpportunity, OpportunityType
from jobby.server import app
from jobby.storage import Storage


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_server_url():
    port = _find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    ready = False
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                ready = True
                break
        except OSError:
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError(f"Test server failed to start on 127.0.0.1:{port}")

    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=2)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        launched = pw.chromium.launch(headless=True)
        yield launched
        launched.close()


def _new_isolated_page(browser, *, width: int = 1440, height: int = 900):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        service_workers="block",
    )
    page = context.new_page()
    # Eviter les latences reseau externes sur Google Fonts tout en testant le shell local
    page.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(status=200, body="", content_type="text/css"))
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    return context, page


def _mock_base_apis(page, *, opps=None, apps=None, config=None):
    page.route(
        "**/api/opportunities*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([] if opps is None else opps),
        ),
    )
    page.route(
        "**/api/applications*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([] if apps is None else apps),
        ),
    )
    page.route(
        "**/api/config",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                config
                if config is not None
                else {"target_roles": ["Product Designer"], "ai_provider": "auto"}
            ),
        ),
    )


def test_navigation_four_views_and_unknown_hash_fallback(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    _mock_base_apis(page)

    try:
        page.goto(live_server_url, wait_until="networkidle")
        assert page.locator("#view-feed").is_visible()
        assert not page.locator("#view-kanban").is_visible()
        assert not page.locator("#view-profile").is_visible()
        assert not page.locator("#view-settings").is_visible()

        for nav_id, view_id, expected_hash in [
            ("#nav-kanban", "#view-kanban", "#kanban"),
            ("#nav-profile", "#view-profile", "#profile"),
            ("#nav-settings", "#view-settings", "#settings"),
            ("#nav-feed", "#view-feed", "#feed"),
        ]:
            page.click(nav_id)
            assert page.locator(view_id).is_visible()
            assert page.evaluate("() => window.location.hash") == expected_hash
            assert page.locator(nav_id).get_attribute("aria-current") == "page"

        # Unknown hash falls back to feed via replaceState
        page.goto(f"{live_server_url}/#unknown-route", wait_until="networkidle")
        assert page.locator("#view-feed").is_visible()
        assert page.evaluate("() => window.location.hash") == "#feed"

        # Deep-link startup on /#kanban and browser Back (hashchange to non-feed view)
        page.goto(f"{live_server_url}/#kanban", wait_until="networkidle")
        assert page.locator("#view-kanban").is_visible()
        assert page.locator("#nav-kanban").get_attribute("aria-current") == "page"
        page.click("#nav-profile")
        assert page.locator("#view-profile").is_visible()
        page.click("#nav-settings")
        assert page.locator("#view-settings").is_visible()
        page.go_back()
        page.wait_for_function("() => window.location.hash === '#profile'")
        assert page.locator("#view-profile").is_visible()
        assert not page.locator("#view-settings").is_visible()
        assert errors == []
    finally:
        context.close()


def test_kanban_badge_live_count_and_api_failure_fallback(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    apps_payload = [
        {"id": "a1", "opportunity_id": "o1", "status": "saved"},
        {"id": "a2", "opportunity_id": "o2", "status": "interview"},
        {"id": "a3", "opportunity_id": "o3", "status": "rejected"},
        {"id": "a4", "opportunity_id": "o4", "status": "archived"},
    ]
    _mock_base_apis(page, apps=apps_payload)

    try:
        page.goto(live_server_url, wait_until="networkidle")
        assert page.locator("#kanban-badge").inner_text() == "2"
        assert page.locator("#kanban-shell-summary").inner_text() == "2 candidatures actives"
        assert page.locator('[data-kanban-col-count="saved"]').inner_text() == "1"
        assert page.locator('[data-kanban-col-count="applied"]').inner_text() == "0"
        assert page.locator('[data-kanban-col-count="interview"]').inner_text() == "1"
        assert page.locator('[data-kanban-col-count="offer"]').inner_text() == "0"
        assert page.locator("#settings-shell-summary").inner_text() == "1 rôle configuré • Fournisseur IA : auto"
        assert page.locator("#modal-hunt-preview").inner_text() == "Rôles configurés : Product Designer"

        # Dynamic state.js mutator updates header badge & kanban shell live
        page.evaluate(
            """async () => {
                const stateMod = await import('/static/js/state.js');
                stateMod.syncOpportunityStatus('o5', 'kept');
            }"""
        )
        assert page.locator("#kanban-badge").inner_text() == "3"
        assert page.locator("#kanban-shell-summary").inner_text() == "3 candidatures actives"
        assert page.locator('[data-kanban-col-count="saved"]').inner_text() == "2"
    finally:
        context.close()

    # API failure falls back to 0 without crashing & surfaces config error honestly
    context_err, page_err = _new_isolated_page(browser)
    page_err.route(
        "**/api/opportunities*",
        lambda route: route.fulfill(status=200, content_type="application/json", body="[]"),
    )
    page_err.route(
        "**/api/applications*",
        lambda route: route.fulfill(status=500, content_type="application/json", body='{"detail":"boom"}'),
    )
    page_err.route(
        "**/api/config",
        lambda route: route.fulfill(status=500, content_type="application/json", body='{"detail":"config down"}'),
    )
    try:
        page_err.goto(live_server_url, wait_until="networkidle")
        assert page_err.locator("#kanban-badge").inner_text() == "0"
        assert page_err.locator("#settings-shell-summary").inner_text() == "Configuration indisponible pour le moment."
        assert page_err.locator("#modal-hunt-preview").inner_text() == "Configuration indisponible pour le moment."
        assert "Aucune opportunité disponible pour le moment." in page_err.locator("#feed-list").inner_text()
        assert page_err.locator("#feed-count").inner_text() == "0 opportunité"
    finally:
        context_err.close()


def test_modal_keyboard_esc_tab_and_backdrop(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    _mock_base_apis(page)

    try:
        page.goto(live_server_url, wait_until="networkidle")

        # Esc with no overlay is a safe no-op
        page.keyboard.press("Escape")
        assert not page.locator("#modal-hunt").is_visible()

        # Open hunt modal via header CTA -> locks background scroll & makes header/main inert
        page.click("#btn-open-hunt-modal")
        assert page.locator("#modal-hunt").is_visible()
        assert page.locator("#modal-hunt").get_attribute("aria-hidden") == "false"
        assert page.evaluate("() => document.body.style.overflow") == "hidden"
        assert page.evaluate("() => Boolean(document.querySelector('header').inert && document.querySelector('main').inert)")

        backdrop_bg = page.locator("#modal-hunt").evaluate("el => getComputedStyle(el).backgroundColor")
        assert backdrop_bg == "rgba(28, 25, 23, 0.25)"

        # Boundary Tab / Shift+Tab wrap-around inside modal
        assert page.evaluate("() => document.activeElement?.getAttribute('aria-label')") == "Fermer la fenêtre"
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("() => document.activeElement?.textContent?.trim()") == "Fermer"
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement?.getAttribute('aria-label')") == "Fermer la fenêtre"

        # Esc closes modal, releases inert/scroll lock, and restores trigger focus
        page.keyboard.press("Escape")
        assert not page.locator("#modal-hunt").is_visible()
        assert page.evaluate("() => document.body.style.overflow") == ""
        assert page.evaluate("() => !document.querySelector('header').inert && !document.querySelector('main').inert")
        assert page.evaluate("() => document.activeElement?.id") == "btn-open-hunt-modal"

        # [data-modal-close] button dismissal
        page.click("#btn-open-hunt-modal")
        assert page.locator("#modal-hunt").is_visible()
        page.click("#modal-hunt button:has-text('Fermer')")
        assert not page.locator("#modal-hunt").is_visible()
        assert page.evaluate("() => document.activeElement?.id") == "btn-open-hunt-modal"

        # Backdrop click closes modal
        page.click("#btn-open-hunt-modal")
        assert page.locator("#modal-hunt").is_visible()
        page.mouse.click(5, 5)
        assert not page.locator("#modal-hunt").is_visible()
    finally:
        context.close()


def test_mobile_viewport_layout_and_min_font_size(browser, live_server_url):
    context, page = _new_isolated_page(browser, width=375, height=812)
    _mock_base_apis(
        page,
        opps=[
            {
                "id": "opp-m1",
                "source": "wttj",
                "opportunity_type": "offer",
                "title": "Design System Lead",
                "company": "Studio Warm",
                "location": "Paris, France",
                "url": "https://example.com/job/1",
                "status": "new",
            }
        ],
    )

    try:
        page.goto(live_server_url, wait_until="networkidle")
        for nav_id in ["#nav-feed", "#nav-kanban", "#nav-profile", "#nav-settings", "#btn-open-hunt-modal"]:
            assert page.locator(nav_id).is_visible()

        overflow = page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")
        assert not overflow

        min_font = page.evaluate(
            """() => {
                let minPx = 999;
                for (const el of document.querySelectorAll('body *')) {
                    const style = getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const hasDirectText = Array.from(el.childNodes).some(
                        n => n.nodeType === Node.TEXT_NODE && n.textContent.trim().length > 0
                    );
                    if (hasDirectText) {
                        minPx = Math.min(minPx, parseFloat(style.fontSize));
                    }
                }
                return minPx;
            }"""
        )
        assert min_font >= 12.0
    finally:
        context.close()


def test_api_401_rejects_typed_error_and_displays_toast(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    page.route(
        "**/api/opportunities*",
        lambda route: route.fulfill(
            status=401,
            content_type="application/json",
            body='{"detail":"Authentification requise"}',
        ),
    )
    page.route(
        "**/api/applications*",
        lambda route: route.fulfill(status=200, content_type="application/json", body="[]"),
    )
    page.route(
        "**/api/config",
        lambda route: route.fulfill(status=200, content_type="application/json", body="{}"),
    )

    try:
        page.goto(live_server_url, wait_until="networkidle")
        toast = page.locator("#toast-container .jp-toast-error")
        assert toast.count() >= 1
        assert "Authentification requise" in toast.first.inner_text()

        err_info = page.evaluate(
            """async () => {
                const { request, ApiError } = await import('/static/js/api.js');
                try {
                    await request('/api/opportunities?limit=1', { silent: true });
                    return null;
                } catch (err) {
                    return {
                        isApiError: err instanceof ApiError,
                        status: err.status,
                        code: err.code,
                        isAuthError: err.isAuthError,
                    };
                }
            }"""
        )
        assert err_info == {
            "isApiError": True,
            "status": 401,
            "code": "UNAUTHORIZED",
            "isAuthError": True,
        }
    finally:
        context.close()


def test_sqlite_status_aliases_and_xss_escaping(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    opps = [
        {
            "id": "opp-fav",
            "source": "wttj",
            "opportunity_type": "offer",
            "title": "Brand Designer <img src=x onerror=window.__xss=1>",
            "company": "Atelier",
            "location": "Lyon",
            "url": "javascript:alert(1)",
            "status": "favorite",
        },
        {
            "id": "opp-sav",
            "source": "wttj",
            "opportunity_type": "offer",
            "title": "Art Director",
            "company": "Studio",
            "location": "Paris, France",
            "remote_policy": "on-site",
            "url": "https://example.com/ok",
            "status": "saved",
        },
        {
            "id": "opp-post",
            "source": "linkedin",
            "opportunity_type": "post",
            "title": "Post Recruteur",
            "company": "Studio Koto",
            "location": "London, UK",
            "url": "https://linkedin.com/feed/update/1",
            "status": "applied",
        },
        {
            "id": "opp-int",
            "source": "indeed",
            "opportunity_type": "offer",
            "title": "Product Lead",
            "company": "Acme",
            "location": "New York, USA",
            "url": "https://example.com/int",
            "status": "interview",
        },
        {
            "id": "opp-arch",
            "source": "indeed",
            "opportunity_type": "offer",
            "title": "Old Role",
            "company": "Corp",
            "location": "Paris",
            "url": "https://example.com/old",
            "status": "archived",
        },
        {
            "id": "opp-rej",
            "source": "indeed",
            "opportunity_type": "offer",
            "title": "Rejected Role",
            "company": "Corp2",
            "location": "Paris",
            "url": "https://example.com/rej",
            "status": "rejected",
        },
    ]
    _mock_base_apis(page, opps=opps)

    try:
        page.goto(live_server_url, wait_until="networkidle")
        statuses = page.evaluate(
            """async () => {
                const { getState } = await import('/static/js/state.js');
                return Object.fromEntries(getState().opportunities.map(o => [o.id, o.status]));
            }"""
        )
        assert statuses == {
            "opp-fav": "kept",
            "opp-sav": "kept",
            "opp-post": "applied",
            "opp-int": "interview",
            "opp-arch": "archived",
            "opp-rej": "rejected",
        }
        assert page.evaluate("() => window.__xss || false") is False
        assert page.locator("#feed-count").inner_text() == "4 opportunités"

        assert page.locator('[data-opp-id="opp-fav"]').is_visible()
        assert "Gardé" in page.locator('[data-opp-id="opp-fav"]').inner_text()
        assert "Lien indisponible" in page.locator('[data-opp-id="opp-fav"]').inner_text()

        sav_card = page.locator('[data-opp-id="opp-sav"]')
        assert "WTTJ" in sav_card.inner_text()
        assert "Sur site" in sav_card.inner_text()
        assert "FR" in sav_card.inner_text()
        sav_link = sav_card.locator('a[href="https://example.com/ok"]')
        assert "Voir l'offre" in sav_link.inner_text()
        assert sav_link.get_attribute("target") == "_blank"
        assert sav_link.get_attribute("rel") == "noopener noreferrer"
        assert "Art Director" in (sav_link.get_attribute("aria-label") or "")

        post_card = page.locator('[data-opp-id="opp-post"]')
        assert "Candidaté" in post_card.inner_text()
        assert "EU" in post_card.inner_text()
        post_link = post_card.locator('a[href="https://linkedin.com/feed/update/1"]')
        assert "Voir le post" in post_link.inner_text()
        assert "Voir le post" in (post_link.get_attribute("aria-label") or "")

        int_card = page.locator('[data-opp-id="opp-int"]')
        assert "Entretien" in int_card.inner_text()
        assert "US" in int_card.inner_text()

        assert page.locator('[data-opp-id="opp-arch"]').count() == 0
        assert page.locator('[data-opp-id="opp-rej"]').count() == 0
    finally:
        context.close()


def test_feed_error_state_and_retry_recovery(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    attempts = {"count": 0}

    def handle_opps(route):
        attempts["count"] += 1
        if attempts["count"] == 1:
            route.fulfill(status=500, content_type="application/json", body='{"detail":"Erreur chargement"}')
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([
                {
                    "id": "opp-recovered",
                    "source": "wttj",
                    "opportunity_type": "offer",
                    "title": "Lead UX",
                    "company": "Porcelain Co",
                    "location": "Paris",
                    "url": "https://example.com/recovered",
                    "status": "new",
                }
            ]),
        )

    page.route("**/api/opportunities*", handle_opps)
    page.route("**/api/applications*", lambda route: route.fulfill(status=200, content_type="application/json", body="[]"))
    page.route("**/api/config", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))

    try:
        page.goto(live_server_url, wait_until="networkidle")
        assert "Erreur chargement" in page.locator("#feed-list").inner_text()
        assert page.locator("#btn-retry-feed").is_visible()

        page.click("#btn-retry-feed")
        page.locator('[data-opp-id="opp-recovered"]').wait_for(state="visible")
        assert page.locator("#feed-count").inner_text() == "1 opportunité"
    finally:
        context.close()


def test_api_client_error_variants_and_toast_dismiss(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    _mock_base_apis(page)
    page.route("**/api/test-500", lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"Server boom"}'))
    page.route(
        "**/api/test-422",
        lambda r: r.fulfill(
            status=422,
            content_type="application/json",
            body=json.dumps({"detail": [{"msg": "Champ requis"}, {"msg": "Format invalide"}]}),
        ),
    )
    page.route("**/api/test-404", lambda r: r.fulfill(status=404, content_type="text/plain", body="Not found text"))
    page.route("**/api/test-bad-json", lambda r: r.fulfill(status=200, content_type="application/json", body="{bad-json"))
    page.route("**/api/test-network", lambda r: r.abort())
    page.route(
        "**/api/test-raw-body",
        lambda r: r.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "contentType": r.request.headers.get("content-type", ""),
                "body": r.request.post_data,
            }),
        ),
    )

    try:
        page.goto(live_server_url, wait_until="networkidle")
        results = page.evaluate(
            """async () => {
                const { request, ApiError } = await import('/static/js/api.js');
                const capture = async (path, opts) => {
                    try {
                        await request(path, opts);
                        return null;
                    } catch (err) {
                        return {
                            isApiError: err instanceof ApiError,
                            status: err.status,
                            code: err.code,
                            message: err.message,
                        };
                    }
                };
                const r500 = await capture('/api/test-500');
                const r422 = await capture('/api/test-422', { silent: true });
                const r404 = await capture('/api/test-404', { silent: true });
                const rBadJson = await capture('/api/test-bad-json');
                const rNet = await capture('/api/test-network', { silent: true });
                const rawEcho = await request('/api/test-raw-body', {
                    method: 'POST',
                    headers: { 'content-type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({ a: '1', b: '2' }),
                });
                return { r500, r422, r404, rBadJson, rNet, rawEcho };
            }"""
        )

        assert results["r500"] == {"isApiError": True, "status": 500, "code": "SERVER_ERROR", "message": "Server boom"}
        assert results["r422"] == {
            "isApiError": True,
            "status": 422,
            "code": "HTTP_ERROR",
            "message": "Champ requis; Format invalide",
        }
        assert results["r404"] == {"isApiError": True, "status": 404, "code": "HTTP_ERROR", "message": "Not found text"}
        assert results["rBadJson"]["isApiError"] is True
        assert results["rBadJson"]["status"] == 200
        assert results["rBadJson"]["code"] == "INVALID_RESPONSE"
        assert results["rNet"] == {
            "isApiError": True,
            "status": 0,
            "code": "NETWORK_ERROR",
            "message": "Connexion au serveur impossible. Vérifiez votre réseau.",
        }
        assert results["rawEcho"]["contentType"] == "application/x-www-form-urlencoded"
        assert results["rawEcho"]["body"] == "a=1&b=2"

        # Silent calls did NOT add toasts; non-silent r500 + rBadJson created 2 toasts
        toasts = page.locator("#toast-container .jp-toast-error")
        assert toasts.count() == 2
        toasts.first.locator("button").click()
        assert toasts.count() == 1
    finally:
        context.close()


def test_service_worker_registration_and_cache_eviction(browser, live_server_url):
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        service_workers="allow",
    )
    page = context.new_page()
    page.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(status=200, body="", content_type="text/css"))
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    _mock_base_apis(page)

    try:
        # Seed legacy jobby-v3 cache on same origin before SW installs
        page.goto(f"{live_server_url}/health", wait_until="networkidle")
        page.evaluate(
            """async () => {
                const cache = await caches.open('jobby-v3');
                await cache.put('/legacy-asset', new Response('old'));
            }"""
        )

        # Navigate to /, wait for SW registration & activation (evicts jobby-v3),
        # then reload so SW fetch handler caches under jobby-v4.
        page.goto(live_server_url, wait_until="networkidle")
        reg_url = page.evaluate(
            """async () => {
                const reg = await navigator.serviceWorker.ready;
                return reg.active ? reg.active.scriptURL : '';
            }"""
        )
        assert reg_url.endswith("/sw.js")

        page.reload(wait_until="networkidle")
        cache_keys = page.evaluate("async () => await caches.keys()")
        assert "jobby-v3" not in cache_keys
        assert "jobby-v4" in cache_keys
    finally:
        context.close()


def test_feed_filters_matrix_and_alternance_counters(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    opps = [
        {
            "id": "opp-li-offer",
            "source": "linkedin",
            "opportunity_type": "offer",
            "title": "Senior Python Engineer",
            "company": "Acme",
            "location": "Paris, France",
            "remote_policy": "full",
            "description": "Vous encadrerez des stagiaires sur la stack Python.",
            "published_at": "2026-11-11T10:00:00+00:00",
            "discovered_at": "2026-11-11T10:00:00+00:00",
            "score": 82,
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "opp-li-post",
            "source": "linkedin",
            "opportunity_type": "post",
            "title": "Post Recrutement Lead Tech",
            "company": "Scale Studio",
            "location": "Berlin, Germany",
            "remote_policy": "hybrid",
            "description": "Nous recrutons un Lead Tech en hybride.",
            "published_at": "2026-11-11T11:00:00+00:00",
            "discovered_at": "2026-11-11T11:00:00+00:00",
            "score": 95,
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "opp-wttj-freelance",
            "source": "wttj",
            "opportunity_type": "freelance",
            "title": "Mission Freelance Data",
            "company": "DataFlow",
            "location": "New York, USA",
            "remote_policy": "onsite",
            "description": "Mission sur site.",
            "published_at": "2026-11-11T09:00:00+00:00",
            "discovered_at": "2026-11-11T09:00:00+00:00",
            "score": 70,
            "status": "kept",
            "is_seen": True,
        },
        {
            "id": "opp-unknown-other",
            "source": "indeed",
            "opportunity_type": "offer",
            "title": "Software Architect",
            "company": "GlobalTech",
            "location": "Tokyo",
            "remote_policy": "unknown",
            "description": "Architecture logicielle.",
            "published_at": "2026-11-11T08:00:00+00:00",
            "discovered_at": "2026-11-11T08:00:00+00:00",
            "score": None,
            "status": "new",
            "is_seen": True,
        },
        {
            "id": "opp-alt",
            "source": "wttj",
            "opportunity_type": "offer",
            "title": "Alternance Développeur Python",
            "company": "CampusTech",
            "location": "Lyon, France",
            "remote_policy": "hybrid",
            "description": "Alternance 12 mois.",
            "published_at": "2026-11-11T12:00:00+00:00",
            "discovered_at": "2026-11-11T12:00:00+00:00",
            "score": 88,
            "status": "new",
            "is_seen": False,
        },
    ]
    _mock_base_apis(page, opps=opps)

    try:
        page.goto(live_server_url, wait_until="networkidle")

        # Default: Masquer alternances is checked -> 4 visible of 5 active, N = 2, WTTJ = 1, LinkedIn = 2
        assert page.locator("#filter-hide-alternance").is_checked()
        assert page.locator("#feed-count").inner_text() == "4 opportunités sur 5"
        assert page.locator('[data-opp-id="opp-alt"]').count() == 0
        assert page.locator('[data-opp-id="opp-li-offer"]').is_visible()  # strict: description with 'stagiaires' not hidden
        assert page.locator("#filter-new-count").inner_text() == "2"
        assert page.locator('[data-source-count="linkedin"]').inner_text() == "2"
        assert page.locator('[data-source-count="wttj"]').inner_text() == "1"
        assert page.locator('[data-source-count="indeed"]').inner_text() == "1"
        assert page.locator('[data-source-count="hellowork"]').inner_text() == "0"
        assert page.locator('[data-source-count="glassdoor"]').inner_text() == "0"
        assert page.locator('[data-source-count="teamtailor"]').inner_text() == "0"
        assert page.locator('[data-source-count="jobgether"]').inner_text() == "0"

        # Nouveau (#15803D) badge visible on unseen cards and absent on seen cards
        new_badge = page.locator('[data-opp-id="opp-li-offer"] [data-new-badge]')
        assert new_badge.is_visible()
        assert "Nouveau" in new_badge.inner_text()
        badge_color = new_badge.evaluate("el => getComputedStyle(el).color")
        assert badge_color == "rgb(21, 128, 61)"
        assert page.locator('[data-opp-id="opp-wttj-freelance"] [data-new-badge]').count() == 0

        # Uncheck Masquer alternances -> N becomes 3, WTTJ count becomes 2, opp-alt visible
        page.uncheck("#filter-hide-alternance")
        assert page.locator("#feed-count").inner_text() == "5 opportunités"
        assert page.locator('[data-opp-id="opp-alt"]').is_visible()
        assert page.locator("#filter-new-count").inner_text() == "3"
        assert page.locator('[data-source-count="wttj"]').inner_text() == "2"
        page.check("#filter-hide-alternance")
        assert page.locator("#feed-count").inner_text() == "4 opportunités sur 5"
        assert page.locator("#filter-new-count").inner_text() == "2"

        # #filter-only-new toggle on/off + aria-pressed & .is-active states
        only_new_btn = page.locator("#filter-only-new")
        assert only_new_btn.get_attribute("aria-pressed") == "false"
        assert "is-active" not in (only_new_btn.get_attribute("class") or "")
        only_new_btn.click()
        assert only_new_btn.get_attribute("aria-pressed") == "true"
        assert "is-active" in (only_new_btn.get_attribute("class") or "")
        assert page.locator("#feed-count").inner_text() == "2 opportunités sur 5"
        assert page.locator('[data-opp-id="opp-wttj-freelance"]').count() == 0
        only_new_btn.click()
        assert only_new_btn.get_attribute("aria-pressed") == "false"
        assert "is-active" not in (only_new_btn.get_attribute("class") or "")
        assert page.locator("#feed-count").inner_text() == "4 opportunités sur 5"

        # Sources popover: 7 sources, multi-select OR (linkedin + indeed), active count badge
        page.click("#btn-sources-popover")
        assert page.locator("#popover-sources").is_visible()
        assert page.locator("#btn-sources-popover").get_attribute("aria-expanded") == "true"
        assert page.locator('#popover-sources input[type="checkbox"]').count() == 7

        # Select LinkedIn + Indeed -> OR across sources, "(2)" active badge & .is-active
        page.check('[data-filter-source="linkedin"]')
        page.check('[data-filter-source="indeed"]')
        assert page.locator("#sources-active-count").is_visible()
        assert page.locator("#sources-active-count").inner_text() == "(2)"
        assert "is-active" in (page.locator("#btn-sources-popover").get_attribute("class") or "")
        assert page.locator('[data-opp-id="opp-li-offer"]').is_visible()
        assert page.locator('[data-opp-id="opp-li-post"]').is_visible()
        assert page.locator('[data-opp-id="opp-unknown-other"]').is_visible()
        assert page.locator('[data-opp-id="opp-wttj-freelance"]').count() == 0
        assert page.locator("#feed-count").inner_text() == "3 opportunités sur 5"

        # Keep only LinkedIn -> includes both LinkedIn offer and LinkedIn post
        page.uncheck('[data-filter-source="indeed"]')
        assert page.locator("#sources-active-count").inner_text() == "(1)"
        assert page.locator('[data-opp-id="opp-li-offer"]').is_visible()
        assert page.locator('[data-opp-id="opp-li-post"]').is_visible()
        assert page.locator('[data-opp-id="opp-unknown-other"]').count() == 0

        # Esc closes popover & restores focus to #btn-sources-popover when activeModal === null
        page.keyboard.press("Escape")
        assert not page.locator("#popover-sources").is_visible()
        assert page.locator("#btn-sources-popover").get_attribute("aria-expanded") == "false"
        assert page.evaluate("() => document.activeElement?.id") == "btn-sources-popover"

        # Orthogonality Sources = LinkedIn + Type = Posts Réseau -> only opp-li-post
        page.click('[data-filter-type="post"]')
        assert page.locator('[data-opp-id="opp-li-post"]').is_visible()
        assert page.locator('[data-opp-id="opp-li-offer"]').count() == 0
        assert page.locator("#feed-count").inner_text() == "1 opportunité sur 5"

        # Re-open popover, test outside click closes it
        page.click("#btn-sources-popover")
        assert page.locator("#popover-sources").is_visible()
        page.click("#view-feed-heading")
        assert not page.locator("#popover-sources").is_visible()

        # When activeModal !== null, Esc closes modal only and does NOT close popover until next Esc
        page.click("#btn-sources-popover")
        assert page.locator("#popover-sources").is_visible()
        page.evaluate(
            """async () => {
                const { openModal } = await import('/static/js/app.js');
                openModal('modal-hunt');
            }"""
        )
        assert page.locator("#modal-hunt").is_visible()
        page.keyboard.press("Escape")
        assert not page.locator("#modal-hunt").is_visible()
        assert page.locator("#popover-sources").is_visible()
        page.keyboard.press("Escape")
        assert not page.locator("#popover-sources").is_visible()
        assert page.evaluate("() => document.activeElement?.id") == "btn-sources-popover"

        # Uncheck LinkedIn -> 0 source checked = all sources; Type = Offres includes offer + freelance
        page.click("#btn-sources-popover")
        page.uncheck('[data-filter-source="linkedin"]')
        assert not page.locator("#sources-active-count").is_visible()
        assert "is-active" not in (page.locator("#btn-sources-popover").get_attribute("class") or "")
        page.keyboard.press("Escape")
        page.click('[data-filter-type="offer"]')
        assert page.locator('[data-opp-id="opp-li-offer"]').is_visible()
        assert page.locator('[data-opp-id="opp-wttj-freelance"]').is_visible()
        assert page.locator('[data-opp-id="opp-unknown-other"]').is_visible()
        assert page.locator('[data-opp-id="opp-li-post"]').count() == 0

        # Reset Type = Tout
        page.click('[data-filter-type="all"]')

        # Mode multi-select: specific selection excludes unknown; Tous includes unknown
        page.click('[data-filter-mode="full"]')
        page.click('[data-filter-mode="hybrid"]')
        assert page.locator('[data-opp-id="opp-li-offer"]').is_visible()
        assert page.locator('[data-opp-id="opp-li-post"]').is_visible()
        assert page.locator('[data-opp-id="opp-unknown-other"]').count() == 0
        assert page.locator('[data-opp-id="opp-wttj-freelance"]').count() == 0

        page.click('[data-filter-mode="all"]')
        assert page.locator('[data-opp-id="opp-unknown-other"]').is_visible()

        # Regions multi-select: FR + US excludes EU hors FR & other
        page.click('[data-filter-region="france"]')
        page.click('[data-filter-region="usa"]')
        assert page.locator('[data-opp-id="opp-li-offer"]').is_visible()
        assert page.locator('[data-opp-id="opp-wttj-freelance"]').is_visible()
        assert page.locator('[data-opp-id="opp-li-post"]').count() == 0
        assert page.locator('[data-opp-id="opp-unknown-other"]').count() == 0

        # Deselect FR + US -> empty region selection restores all including other
        page.click('[data-filter-region="france"]')
        page.click('[data-filter-region="usa"]')
        assert page.locator('[data-opp-id="opp-unknown-other"]').is_visible()

        # Sorting: Score (95 -> 82 -> 70 -> null) & Region (FR -> EU -> US -> other)
        page.select_option("#filter-sort", "score")
        ids_by_score = page.locator("#feed-list article[data-opp-id]").evaluate_all(
            "els => els.map(el => el.getAttribute('data-opp-id'))"
        )
        assert ids_by_score == ["opp-li-post", "opp-li-offer", "opp-wttj-freelance", "opp-unknown-other"]

        page.select_option("#filter-sort", "region")
        ids_by_region = page.locator("#feed-list article[data-opp-id]").evaluate_all(
            "els => els.map(el => el.getAttribute('data-opp-id'))"
        )
        assert ids_by_region == ["opp-li-offer", "opp-li-post", "opp-wttj-freelance", "opp-unknown-other"]

        # Filtered empty state + Réinitialiser les filtres restores defaults and focus
        page.click("#filter-only-new")
        page.click('[data-filter-mode="onsite"]')
        assert page.locator("#feed-count").inner_text() == "0 opportunité sur 5"
        assert "Aucune opportunité ne correspond à ces critères." in page.locator("#feed-list").inner_text()
        assert page.locator("#btn-reset-filters").is_visible()
        page.click("#btn-reset-filters")
        assert page.locator("#feed-list article[data-opp-id]").count() == 4
        assert page.locator("#feed-count").inner_text() == "4 opportunités sur 5"
        assert page.locator("#filter-only-new").get_attribute("aria-pressed") == "false"
        assert "is-active" not in (page.locator("#filter-only-new").get_attribute("class") or "")
        assert page.locator("#filter-sort").input_value() == "date"
        assert page.evaluate("() => document.activeElement?.id") == "filter-only-new"

        # Performance: full renderFeedView() across 200 items in real DOM completes < 50 ms
        perf_result = page.evaluate(
            """async () => {
                const { renderFeedView } = await import('/static/js/views/feed.js');
                const items = Array.from({ length: 200 }, (_, i) => ({
                    id: `perf-${i}`,
                    source: i % 2 === 0 ? 'linkedin' : 'wttj',
                    opportunity_type: i % 5 === 0 ? 'post' : 'offer',
                    title: `Backend Engineer ${i}`,
                    company: `Studio ${i}`,
                    location: i % 3 === 0 ? 'Paris, France' : 'London, UK',
                    remote_policy: i % 4 === 0 ? 'full' : 'hybrid',
                    published_at: '2026-11-11T10:00:00+00:00',
                    url: `https://example.com/job/${i}`,
                    score: i % 100,
                    status: 'new',
                    is_seen: i % 2 === 0,
                }));
                const snapshot = {
                    opportunities: items,
                    isLoadingFeed: false,
                    feedError: null,
                    feedFilters: {
                        onlyNew: false,
                        sources: [],
                        modes: [],
                        regions: [],
                        type: 'all',
                        sortBy: 'score',
                        hideAlternance: true,
                    },
                };
                const start = performance.now();
                renderFeedView(snapshot);
                const end = performance.now();
                return {
                    elapsedMs: end - start,
                    domCount: document.querySelectorAll('#feed-list article[data-opp-id]').length,
                };
            }"""
        )
        assert perf_result["domCount"] == 200
        assert perf_result["elapsedMs"] < 50.0
    finally:
        context.close()


def test_mark_all_seen_persistence_and_rollback_and_seen_api(browser, live_server_url, monkeypatch, tmp_path):
    db_path = tmp_path / "e2e_seen.db"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))

    storage = Storage(db_path=db_path)
    storage.upsert_opportunity(
        JobOpportunity(
            id="e2e-1",
            source="linkedin",
            title="Python Staff Engineer",
            company="Porcelain Lab",
            location="Paris",
            url="https://linkedin.com/jobs/view/e2e1",
            is_seen=False,
        )
    )
    storage.upsert_opportunity(
        JobOpportunity(
            id="e2e-2",
            source="wttj",
            title="Design Engineer",
            company="Studio Warm",
            location="Paris",
            url="https://wttj.co/jobs/e2e2",
            is_seen=False,
        )
    )
    storage.upsert_opportunity(
        JobOpportunity(
            id="e2e-3",
            source="indeed",
            title="Already Seen Role",
            company="OldCorp",
            location="Paris",
            url="https://indeed.com/jobs/e2e3",
            is_seen=True,
        )
    )

    context, page = _new_isolated_page(browser)
    try:
        page.goto(live_server_url, wait_until="networkidle")
        assert page.locator("#filter-new-count").inner_text() == "2"
        assert page.locator('[data-opp-id="e2e-1"] [data-new-badge]').is_visible()
        assert page.locator('[data-opp-id="e2e-2"] [data-new-badge]').is_visible()
        assert page.locator('[data-opp-id="e2e-3"] [data-new-badge]').count() == 0

        # Test PATCH /seen via api.js + state.js live update
        page.evaluate(
            """async () => {
                const { markOpportunitySeen } = await import('/static/js/api.js');
                const { syncOpportunitySeen } = await import('/static/js/state.js');
                await markOpportunitySeen('e2e-1', true);
                syncOpportunitySeen('e2e-1', true);
            }"""
        )
        assert page.locator("#filter-new-count").inner_text() == "1"
        assert page.locator('[data-opp-id="e2e-1"] [data-new-badge]').count() == 0
        assert storage.get_opportunity("e2e-1").is_seen is True

        # Click Tout marquer comme vu -> persists in SQLite across F5 reload & disables button
        assert page.locator("#btn-mark-all-seen").is_enabled()
        with page.expect_response("**/api/opportunities/mark-all-seen"):
            page.click("#btn-mark-all-seen")
        assert page.locator("#filter-new-count").inner_text() == "0"
        assert page.locator("#btn-mark-all-seen").is_disabled()
        assert page.locator("[data-new-badge]").count() == 0
        assert storage.get_opportunity("e2e-2").is_seen is True

        page.reload(wait_until="networkidle")
        assert page.locator("#filter-new-count").inner_text() == "0"
        assert page.locator("#btn-mark-all-seen").is_disabled()
        assert page.locator("[data-new-badge]").count() == 0
    finally:
        context.close()

    # Rollback + toast when mark-all-seen API fails
    context_err, page_err = _new_isolated_page(browser)
    _mock_base_apis(
        page_err,
        opps=[
            {
                "id": "rb-1",
                "source": "wttj",
                "opportunity_type": "offer",
                "title": "Rollback Offer 1",
                "company": "Co",
                "location": "Paris",
                "url": "https://example.com/rb1",
                "status": "new",
                "is_seen": False,
            },
            {
                "id": "rb-2",
                "source": "linkedin",
                "opportunity_type": "offer",
                "title": "Rollback Offer 2",
                "company": "Co",
                "location": "Paris",
                "url": "https://example.com/rb2",
                "status": "new",
                "is_seen": False,
            },
        ],
    )
    page_err.route(
        "**/api/opportunities/mark-all-seen",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"detail":"Erreur marquage"}',
        ),
    )

    try:
        page_err.goto(live_server_url, wait_until="networkidle")
        assert page_err.locator("#filter-new-count").inner_text() == "2"

        page_err.click("#filter-only-new")
        page_err.click("#btn-mark-all-seen")

        toast = page_err.locator("#toast-container .jp-toast-error")
        toast.first.wait_for(state="visible")
        assert "Erreur marquage" in toast.first.inner_text()

        # Rolled back cleanly: N == 2, cards restored under onlyNew filter, badges visible, button re-enabled
        assert page_err.locator("#filter-new-count").inner_text() == "2"
        assert page_err.locator("#btn-mark-all-seen").is_enabled()
        assert page_err.locator('[data-opp-id="rb-1"] [data-new-badge]').is_visible()
        assert page_err.locator('[data-opp-id="rb-2"] [data-new-badge]').is_visible()
    finally:
        context_err.close()


def test_mobile_filter_bar_and_sources_popover_bounds(browser, live_server_url):
    context, page = _new_isolated_page(browser, width=430, height=812)
    _mock_base_apis(
        page,
        opps=[
            {
                "id": "mob-1",
                "source": "linkedin",
                "opportunity_type": "offer",
                "title": "Mobile Lead Engineer",
                "company": "Warm Co",
                "location": "Paris, France",
                "url": "https://example.com/mob1",
                "status": "new",
                "is_seen": False,
            }
        ],
    )

    try:
        page.goto(live_server_url, wait_until="networkidle")
        assert not page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")

        # Open Sources popover on 430px viewport where button sits near right edge -> clamped inside 430px
        page.click("#btn-sources-popover")
        assert page.locator("#popover-sources").is_visible()
        box_430 = page.locator("#popover-sources").bounding_box()
        assert box_430 is not None
        assert box_430["x"] >= 0
        assert box_430["x"] + box_430["width"] <= 430
        assert not page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")

        # Resize viewport to 375x812 while popover is open -> window.resize repositions popover within 375px
        page.set_viewport_size({"width": 375, "height": 812})
        page.wait_for_function(
            """() => {
                const el = document.getElementById('popover-sources');
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.left >= 0 && r.right <= 375;
            }"""
        )
        box_375 = page.locator("#popover-sources").bounding_box()
        assert box_375 is not None
        assert box_375["x"] >= 0
        assert box_375["x"] + box_375["width"] <= 375
        assert not page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")

        min_font = page.evaluate(
            """() => {
                let minPx = 999;
                for (const el of document.querySelectorAll('body *')) {
                    const style = getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const hasDirectText = Array.from(el.childNodes).some(
                        n => n.nodeType === Node.TEXT_NODE && n.textContent.trim().length > 0
                    );
                    if (hasDirectText) {
                        minPx = Math.min(minPx, parseFloat(style.fontSize));
                    }
                }
                return minPx;
            }"""
        )
        assert min_font >= 12.0

        # Switching away from feed tab closes the popover and stops Escape from targeting it
        page.evaluate(
            """async () => {
                const { setActiveView } = await import('/static/js/state.js');
                setActiveView('kanban');
            }"""
        )
        assert page.locator("#view-kanban").is_visible()
        assert page.locator("#popover-sources").evaluate("el => el.classList.contains('hidden')")
        assert page.locator("#btn-sources-popover").get_attribute("aria-expanded") == "false"
        page.keyboard.press("Escape")
        assert page.evaluate("() => document.activeElement?.id") != "btn-sources-popover"
    finally:
        context.close()


def test_is_alternance_strict_metadata_tags_and_state_noop(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    _mock_base_apis(page)

    try:
        page.goto(live_server_url, wait_until="networkidle")
        checks = page.evaluate(
            """async () => {
                const { isAlternanceOrStage } = await import('/static/js/utils.js');
                const stateMod = await import('/static/js/state.js');

                const altChecks = {
                    rawContract: isAlternanceOrStage({
                        title: 'Backend Engineer',
                        raw_metadata: { contract_type: 'apprenticeship' },
                    }),
                    upperApprenticeshipTag: isAlternanceOrStage({
                        title: 'Backend Engineer',
                        tags: ['APPRENTICESHIP'],
                    }),
                    upperStageTag: isAlternanceOrStage({
                        title: 'Product Designer',
                        tags: ['STAGE'],
                    }),
                    paddedAlternanceTag: isAlternanceOrStage({
                        title: 'Data Engineer',
                        tags: [' Alternance '],
                    }),
                    descriptionIgnored: isAlternanceOrStage({
                        title: 'Senior Python Engineer',
                        description: 'Vous encadrerez des stagiaires',
                    }),
                };

                stateMod.setOpportunities([
                    { id: 'st-1', source: 'linkedin', status: 'new', is_seen: true },
                ]);
                let emitCount = 0;
                const unsub = stateMod.subscribe(() => { emitCount += 1; });

                // No-op mutators & invalid source key must NOT emit
                stateMod.setOnlyNew(false);
                stateMod.setHideAlternance(true);
                stateMod.toggleFeedMode('all');
                stateMod.toggleFeedSource('unknown-source');
                stateMod.markAllOpportunitiesSeenInState();
                const noopEmits = emitCount;

                // Valid mutations DO emit
                stateMod.toggleFeedSource('linkedin');
                const afterValidEmit = emitCount;
                unsub();

                return {
                    altChecks,
                    noopEmits,
                    afterValidEmit,
                    sourcesAfter: stateMod.getState().feedFilters.sources,
                };
            }"""
        )
        assert checks["altChecks"] == {
            "rawContract": True,
            "upperApprenticeshipTag": True,
            "upperStageTag": True,
            "paddedAlternanceTag": True,
            "descriptionIgnored": False,
        }
        assert checks["noopEmits"] == 0
        assert checks["afterValidEmit"] == 1
        assert checks["sourcesAfter"] == ["linkedin"]
    finally:
        context.close()


def test_sort_date_score_tiebreak_and_invalid_published_at(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    opps = [
        {
            "id": "s-4",
            "source": "wttj",
            "opportunity_type": "offer",
            "title": "Old High Score",
            "company": "Alpha",
            "location": "Paris",
            "published_at": "2026-11-11T08:00:00+00:00",
            "discovered_at": "2026-11-11T08:00:00+00:00",
            "url": "https://example.com/s4",
            "score": 90,
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "s-2",
            "source": "linkedin",
            "opportunity_type": "offer",
            "title": "Invalid Published Fallback",
            "company": "Beta",
            "location": "Paris",
            "published_at": "not-a-valid-date",
            "discovered_at": "2026-11-11T11:00:00+00:00",
            "url": "https://example.com/s2",
            "score": None,
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "s-1",
            "source": "indeed",
            "opportunity_type": "offer",
            "title": "Newest Mid Score",
            "company": "Gamma",
            "location": "Paris",
            "published_at": "2026-11-11T12:00:00+00:00",
            "discovered_at": "2026-11-11T12:00:00+00:00",
            "url": "https://example.com/s1",
            "score": 50,
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "s-3",
            "source": "hellowork",
            "opportunity_type": "offer",
            "title": "Missing Published Fallback",
            "company": "Delta",
            "location": "Paris",
            "published_at": None,
            "discovered_at": "2026-11-11T09:00:00+00:00",
            "url": "https://example.com/s3",
            "score": None,
            "status": "new",
            "is_seen": False,
        },
    ]
    _mock_base_apis(page, opps=opps)

    try:
        page.goto(live_server_url, wait_until="networkidle")

        # Initial default 'date' sort uses valid discovered_at fallback when published_at is invalid/null
        ids_default = page.locator("#feed-list article[data-opp-id]").evaluate_all(
            "els => els.map(el => el.getAttribute('data-opp-id'))"
        )
        assert ids_default == ["s-1", "s-2", "s-3", "s-4"]
        assert "Récemment" not in page.locator('[data-opp-id="s-2"]').inner_text()

        # Switch to 'score': 90 -> 50 -> null tie-broken by date (s-2 before s-3)
        page.select_option("#filter-sort", "score")
        ids_score = page.locator("#feed-list article[data-opp-id]").evaluate_all(
            "els => els.map(el => el.getAttribute('data-opp-id'))"
        )
        assert ids_score == ["s-4", "s-1", "s-2", "s-3"]

        # Switch back to 'date'
        page.select_option("#filter-sort", "date")
        ids_back_date = page.locator("#feed-list article[data-opp-id]").evaluate_all(
            "els => els.map(el => el.getAttribute('data-opp-id'))"
        )
        assert ids_back_date == ["s-1", "s-2", "s-3", "s-4"]

        # Switch to 'score' then hit empty state and click #btn-reset-filters -> restores #filter-sort to 'date'
        page.select_option("#filter-sort", "score")
        page.click('[data-filter-type="post"]')
        assert page.locator("#btn-reset-filters").is_visible()
        page.click("#btn-reset-filters")
        assert page.locator("#filter-sort").input_value() == "date"
        ids_after_reset = page.locator("#feed-list article[data-opp-id]").evaluate_all(
            "els => els.map(el => el.getAttribute('data-opp-id'))"
        )
        assert ids_after_reset == ["s-1", "s-2", "s-3", "s-4"]
    finally:
        context.close()
