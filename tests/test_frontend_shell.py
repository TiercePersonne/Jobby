"""End-to-end Playwright browser tests for Story 1.1 Warm Porcelain shell & ES6 modules."""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from jobby.server import app


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
