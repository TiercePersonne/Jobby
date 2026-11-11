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


def _mock_base_apis(page, *, opps=None, apps=None, config=None, profile=None):
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
    page.route(
        "**/api/profile",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                profile
                if profile is not None
                else {
                    "user": {"id": "usr_test", "email": "user@jobby.local", "full_name": "Mon Profil Candidat"},
                    "profile": {
                        "user_id": "usr_test",
                        "target_roles": ["Product Designer"],
                        "skills": ["Figma"],
                        "ideal_job_brief": None,
                    },
                    "has_cv": False,
                }
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
    page_err.route(
        "**/api/profile",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"profile":{"ideal_job_brief":null}}'),
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
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement?.id") == "hunt-keywords"
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("() => document.activeElement?.getAttribute('aria-label')") == "Fermer la fenêtre"
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("() => document.activeElement?.textContent?.trim()") == "Lancer la chasse"
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
    page.route(
        "**/api/profile",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"profile":{"ideal_job_brief":null}}'),
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
    page.route("**/api/profile", lambda route: route.fulfill(status=200, content_type="application/json", body='{"profile":{"ideal_job_brief":null}}'))

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


def test_ux_dr6_cards_scoring_synthesis_accordion_and_scroll_neutrality(browser, live_server_url):
    context, page = _new_isolated_page(browser, width=1440, height=900)
    seen_calls = []
    page.route(
        "**/api/opportunities/*/seen",
        lambda route: (seen_calls.append(route.request.url), route.fulfill(status=200, content_type="application/json", body='{"status":"updated","is_seen":true}')),
    )

    opps = [
        {
            "id": "card-high",
            "source": "wttj",
            "opportunity_type": "offer",
            "title": "Lead Product Designer",
            "company": "Alan",
            "location": "Paris, France",
            "remote_policy": "full",
            "salary_raw": "75k€",
            "description": "Pilotage du Design System\n\nCollaboration produit.",
            "published_at": "2026-11-11T12:00:00+00:00",
            "discovered_at": "2026-11-11T12:00:00+00:00",
            "url": "https://example.com/high",
            "score": 92,
            "tags": ["Figma", "Design System", "Remote"],
            "raw_metadata": {
                "ai_match": {
                    "verdict": "Match sémantique (2 mots-clés alignés)",
                }
            },
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "card-post-mid",
            "source": "linkedin",
            "opportunity_type": "post",
            "title": "Directeur Artistique Senior",
            "company": "Studio Koto",
            "author_name": "Marc Vance",
            "author_title": "Fondateur",
            "location": "Berlin, Germany",
            "remote_policy": "hybrid",
            "description": "Nous cherchons un DA senior autonome.",
            "published_at": "2026-11-11T11:00:00+00:00",
            "discovered_at": "2026-11-11T11:00:00+00:00",
            "url": "https://example.com/post",
            "score": 72,
            "ai_summary": "Recrutement : Marc Vance recrute un DA senior.",
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "card-low-gemini",
            "source": "indeed",
            "opportunity_type": "offer",
            "title": "Brand Designer",
            "company": "Swile",
            "location": "Paris, France",
            "remote_policy": "onsite",
            "description": "Identité de marque.",
            "published_at": "2026-11-11T10:00:00+00:00",
            "discovered_at": "2026-11-11T10:00:00+00:00",
            "url": "https://example.com/low",
            "score": 45,
            "raw_metadata": {
                "ai_match": {
                    "verdict": "Adéquation partielle sur le rythme sur site.",
                    "provider_used": "gemini",
                    "used_fallback": False,
                }
            },
            "status": "new",
            "is_seen": True,
        },
        {
            "id": "card-zero-score",
            "source": "hellowork",
            "opportunity_type": "offer",
            "title": "Graphic Designer",
            "company": "Atelier",
            "location": "Lyon, France",
            "description": "Création print et web.",
            "published_at": "2026-11-11T09:00:00+00:00",
            "discovered_at": "2026-11-11T09:00:00+00:00",
            "url": "https://example.com/zero",
            "score": 0,
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "card-null-score",
            "source": "jobgether",
            "opportunity_type": "offer",
            "title": "UI Designer",
            "company": "RemoteCo",
            "location": "New York, USA",
            "description": "   ",
            "published_at": "2026-11-11T08:00:00+00:00",
            "discovered_at": "2026-11-11T08:00:00+00:00",
            "url": "https://example.com/null",
            "score": None,
            "status": "new",
            "is_seen": True,
        },
    ]
    _mock_base_apis(page, opps=opps)

    try:
        page.goto(live_server_url, wait_until="networkidle")

        # High score badge (>= 85 -> #F0FDF4 / #14532D), zero tag stack, heuristic NOT labeled Gemini
        high_card = page.locator('[data-opp-id="card-high"]')
        high_badge = high_card.locator("[data-score-badge]")
        assert high_badge.inner_text() == "92% Match"
        assert high_badge.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(240, 253, 244)"
        assert high_badge.evaluate("el => getComputedStyle(el).color") == "rgb(20, 83, 45)"
        assert "Figma" not in high_card.inner_text()
        assert "Design System" not in high_card.locator(".jp-label").first.inner_text()
        high_callout = high_card.locator("[data-ai-callout]")
        assert "Synthèse :" in high_callout.inner_text()
        assert "Gemini" not in high_callout.inner_text()

        # Post network author & mid score (#FEFCE8 / #713F12) & ai_summary
        post_card = page.locator('[data-opp-id="card-post-mid"]')
        mid_badge = post_card.locator("[data-score-badge]")
        assert mid_badge.inner_text() == "72% Match"
        assert mid_badge.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(254, 252, 232)"
        assert mid_badge.evaluate("el => getComputedStyle(el).color") == "rgb(113, 63, 18)"
        assert "Marc Vance — Fondateur" in post_card.locator("[data-opp-author]").inner_text()
        assert "Synthèse :" in post_card.locator("[data-ai-callout]").inner_text()
        assert "Gemini" not in post_card.locator("[data-ai-callout]").inner_text()

        # Low score (< 60 -> #F5F5F4) & explicit Gemini provider_used=gemini
        low_card = page.locator('[data-opp-id="card-low-gemini"]')
        low_badge = low_card.locator("[data-score-badge]")
        assert low_badge.inner_text() == "45% Match"
        assert low_badge.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(245, 245, 244)"
        assert "Gemini :" in low_card.locator("[data-ai-callout]").inner_text()

        # Score 0 and None omit badge; card without verdict/ai_summary omits callout
        assert page.locator('[data-opp-id="card-zero-score"] [data-score-badge]').count() == 0
        assert page.locator('[data-opp-id="card-zero-score"] [data-ai-callout]').count() == 0
        assert page.locator('[data-opp-id="card-null-score"] [data-score-badge]').count() == 0

        # Accordion body includes salary_raw when provided and fallback copy when description is empty
        assert "Rémunération : 75k€" in page.locator('[data-opp-id="card-high"] .jp-accordion-body').text_content()
        assert "Aucune description détaillée fournie." in page.locator('[data-opp-id="card-null-score"] .jp-accordion-body').text_content()

        # Accordion collapsed by default; strictly 3 actions (no Lettre IA); zero box-shadow
        for card_id, expected_cta in [
            ("card-high", "Voir l'offre"),
            ("card-post-mid", "Voir le post"),
        ]:
            card = page.locator(f'[data-opp-id="{card_id}"]')
            assert card.evaluate("el => getComputedStyle(el).boxShadow") == "none"
            assert card.locator("details.jp-accordion").get_attribute("open") is None
            assert card.locator('[data-action="toggle-keep"]').is_visible()
            assert card.locator('[data-action="archive"]').is_visible()
            assert expected_cta in card.locator('[data-action="open-link"]').inner_text()
            assert card.locator('[data-action]').count() == 3
            assert "Lettre IA" not in card.inner_text()

        # Scroll down and up: zero /seen calls and unseen cards remain is_seen=false
        page.evaluate("() => { window.scrollTo(0, 600); window.scrollTo(0, 1200); window.scrollTo(0, 0); }")
        page.wait_for_timeout(150)
        assert seen_calls == []
        assert page.locator('[data-opp-id="card-high"]').get_attribute("data-is-seen") == "false"
        assert page.locator('[data-opp-id="card-high"] [data-new-badge]').is_visible()
        assert page.locator("#filter-new-count").inner_text() == "3"

        # Responsive 375px check: zero horizontal overflow and min font >= 12px
        page.set_viewport_size({"width": 375, "height": 812})
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
    finally:
        context.close()


def test_explicit_is_seen_accordion_link_and_rollback(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    context.route("https://example.com/**", lambda route: route.fulfill(status=200, body="ok", content_type="text/html"))

    seen_requests = []
    fail_mode = {"fail": False}

    def handle_seen(route):
        opp_id = route.request.url.split("/api/opportunities/")[1].split("/seen")[0]
        body = route.request.post_data_json
        seen_requests.append((opp_id, body))
        if fail_mode["fail"]:
            route.fulfill(status=500, content_type="application/json", body='{"detail":"Seen API down"}')
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "updated", "id": opp_id, "is_seen": True}),
        )

    page.route("**/api/opportunities/*/seen", handle_seen)

    opps = [
        {
            "id": "seen-acc-1",
            "source": "wttj",
            "opportunity_type": "offer",
            "title": "Design Ops Lead",
            "company": "Porcelain",
            "location": "Paris, France",
            "description": "Détails complets du poste Design Ops.",
            "published_at": "2026-11-11T12:00:00+00:00",
            "discovered_at": "2026-11-11T12:00:00+00:00",
            "url": "https://example.com/acc-1",
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "seen-link-2",
            "source": "linkedin",
            "opportunity_type": "offer",
            "title": "Staff Product Designer",
            "company": "WarmLab",
            "location": "Paris, France",
            "description": "Description détaillée.",
            "published_at": "2026-11-11T11:00:00+00:00",
            "discovered_at": "2026-11-11T11:00:00+00:00",
            "url": "https://example.com/link-2",
            "status": "new",
            "is_seen": False,
        },
        {
            "id": "seen-err-3",
            "source": "indeed",
            "opportunity_type": "offer",
            "title": "Brand Lead",
            "company": "Studio Err",
            "location": "Paris, France",
            "description": "Contenu affiché malgré erreur.",
            "published_at": "2026-11-11T10:00:00+00:00",
            "discovered_at": "2026-11-11T10:00:00+00:00",
            "url": "https://example.com/err-3",
            "status": "new",
            "is_seen": False,
        },
    ]
    _mock_base_apis(page, opps=opps)

    try:
        page.goto(live_server_url, wait_until="networkidle")
        assert page.locator("#filter-new-count").inner_text() == "3"

        # 1. Opening <details> via keyboard sends PATCH /seen, keeps <summary> focus, keeps <details open>
        acc1_summary = page.locator('[data-opp-id="seen-acc-1"] details.jp-accordion > summary')
        acc1_summary.focus()
        with page.expect_response("**/api/opportunities/seen-acc-1/seen"):
            acc1_summary.press("Enter")

        assert page.locator('[data-opp-id="seen-acc-1"] details.jp-accordion').evaluate("el => el.open") is True
        assert page.evaluate(
            "() => document.activeElement?.tagName === 'SUMMARY' && document.activeElement.closest('[data-opp-id]')?.getAttribute('data-opp-id') === 'seen-acc-1'"
        )
        assert "Détails complets du poste Design Ops." in page.locator('[data-opp-id="seen-acc-1"] .jp-accordion-body').inner_text()
        assert page.locator('[data-opp-id="seen-acc-1"] [data-new-badge]').count() == 0
        assert page.locator("#filter-new-count").inner_text() == "2"
        assert seen_requests == [("seen-acc-1", {"is_seen": True})]

        # 2. Closing <details> does NOT trigger /seen
        page.click('[data-opp-id="seen-acc-1"] details.jp-accordion > summary')
        page.wait_for_timeout(100)
        assert page.locator('[data-opp-id="seen-acc-1"] details.jp-accordion').evaluate("el => el.open") is False
        assert len(seen_requests) == 1

        # Re-open first card so we confirm a second render keeps it open
        page.click('[data-opp-id="seen-acc-1"] details.jp-accordion > summary')
        assert page.locator('[data-opp-id="seen-acc-1"] details.jp-accordion').evaluate("el => el.open") is True
        assert len(seen_requests) == 1  # already seen, no duplicate request

        # 3. Clicking external link marks seen explicitly and keeps first card's <details open>
        with page.expect_popup() as popup_info:
            with page.expect_response("**/api/opportunities/seen-link-2/seen"):
                page.click('[data-opp-id="seen-link-2"] [data-action="open-link"]')
        popup_info.value.close()

        assert page.locator('[data-opp-id="seen-link-2"] [data-new-badge]').count() == 0
        assert page.locator("#filter-new-count").inner_text() == "1"
        assert page.locator('[data-opp-id="seen-acc-1"] details.jp-accordion').evaluate("el => el.open") is True

        # 4. API failure on /seen rolls back is_seen + shows toast while keeping <details> open
        fail_mode["fail"] = True
        with page.expect_response("**/api/opportunities/seen-err-3/seen"):
            page.click('[data-opp-id="seen-err-3"] details.jp-accordion > summary')

        toast = page.locator("#toast-container .jp-toast-error")
        toast.first.wait_for(state="visible")
        assert "Seen API down" in toast.first.inner_text()
        assert page.locator('[data-opp-id="seen-err-3"] details.jp-accordion').evaluate("el => el.open") is True
        assert page.locator('[data-opp-id="seen-err-3"] [data-new-badge]').is_visible()
        assert page.locator("#filter-new-count").inner_text() == "1"

        # 5. Opening <details> while onlyNew is active keeps that card visible until collapsed,
        # and middle-click (auxclick button=1) on open-link triggers /seen
        fail_mode["fail"] = False
        page.click("#filter-only-new")
        assert page.locator('[data-opp-id="seen-err-3"]').is_visible()
        # Close seen-err-3 first, then re-open under onlyNew
        page.click('[data-opp-id="seen-err-3"] details.jp-accordion > summary')
        with page.expect_response("**/api/opportunities/seen-err-3/seen"):
            page.click('[data-opp-id="seen-err-3"] details.jp-accordion > summary')
        assert page.locator('[data-opp-id="seen-err-3"]').is_visible()
        assert page.locator('[data-opp-id="seen-err-3"] details.jp-accordion').evaluate("el => el.open") is True
        assert page.locator("#filter-new-count").inner_text() == "0"
        # Collapse <details> -> card now leaves onlyNew view (toggle event is queued asynchronously by browser)
        page.click('[data-opp-id="seen-err-3"] details.jp-accordion > summary')
        page.locator('[data-opp-id="seen-err-3"]').wait_for(state="detached")
        assert page.locator('[data-opp-id="seen-err-3"]').count() == 0

        # Middle-click (auxclick button=1) on an unseen link triggers /seen
        page.evaluate(
            """async () => {
                const { setOpportunities, setOnlyNew } = await import('/static/js/state.js');
                setOnlyNew(false);
                setOpportunities([{
                    id: 'seen-aux-4',
                    source: 'wttj',
                    opportunity_type: 'offer',
                    title: 'Middle Click Role',
                    company: 'AuxCo',
                    location: 'Paris',
                    url: 'https://example.com/aux-4',
                    status: 'new',
                    is_seen: false,
                }]);
            }"""
        )
        with page.expect_response("**/api/opportunities/seen-aux-4/seen"):
            with context.expect_page() as aux_popup:
                page.locator('[data-opp-id="seen-aux-4"] [data-action="open-link"]').click(button="middle")
        aux_popup.value.close()
        assert page.locator('[data-opp-id="seen-aux-4"] [data-new-badge]').count() == 0
    finally:
        context.close()


def test_triage_keep_archive_undo_kanban_persistence_and_rollback(browser, live_server_url, monkeypatch, tmp_path):
    db_path = tmp_path / "e2e_triage.db"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))

    storage = Storage(db_path=db_path)
    storage.upsert_opportunity(
        JobOpportunity(
            id="tr-1",
            source="wttj",
            title="Design Director",
            company="Porcelain Co",
            location="Paris, France",
            url="https://wttj.co/jobs/tr-1",
            score=91,
            status="new",
            is_seen=False,
        )
    )
    storage.upsert_opportunity(
        JobOpportunity(
            id="tr-2",
            source="linkedin",
            title="Product Lead",
            company="Studio B",
            location="Paris, France",
            url="https://linkedin.com/jobs/view/tr-2",
            score=76,
            status="new",
            is_seen=False,
        )
    )

    # Part A: Full SQLite E2E persistence across F5 reload (Je garde, toggle new, Archiver + Annuler)
    context, page = _new_isolated_page(browser)
    try:
        page.goto(live_server_url, wait_until="networkidle")
        assert page.locator("#kanban-badge").inner_text() == "0"
        assert page.locator("#filter-new-count").inner_text() == "2"

        # 1. Press Enter on Je garde on tr-1 -> optimistic 'Gardé', focus stays on button, persists in SQLite
        keep_btn = page.locator('[data-opp-id="tr-1"] [data-action="toggle-keep"]')
        keep_btn.focus()
        with page.expect_response("**/api/opportunities/tr-1/status"):
            keep_btn.press("Enter")

        assert "Gardé" in keep_btn.inner_text()
        assert keep_btn.get_attribute("aria-pressed") == "true"
        assert page.evaluate(
            "() => document.activeElement?.getAttribute('data-action') === 'toggle-keep' && document.activeElement.closest('[data-opp-id]')?.getAttribute('data-opp-id') === 'tr-1'"
        )
        assert page.locator('[data-opp-id="tr-1"] [data-new-badge]').count() == 0
        assert page.locator("#filter-new-count").inner_text() == "1"
        assert page.locator("#kanban-badge").inner_text() == "1"
        assert page.locator('[data-kanban-col-count="saved"]').inner_text() == "1"

        page.reload(wait_until="networkidle")
        assert "Gardé" in page.locator('[data-opp-id="tr-1"] [data-action="toggle-keep"]').inner_text()
        assert page.locator("#kanban-badge").inner_text() == "1"
        assert storage.get_opportunity("tr-1").status == "kept"
        assert storage.get_opportunity("tr-1").is_seen is True

        # 2. Click Gardé again -> toggles kept -> new (removes saved application, badge -1)
        with page.expect_response("**/api/opportunities/tr-1/status"):
            page.click('[data-opp-id="tr-1"] [data-action="toggle-keep"]')

        assert "Je garde" in page.locator('[data-opp-id="tr-1"] [data-action="toggle-keep"]').inner_text()
        assert page.locator("#kanban-badge").inner_text() == "0"
        assert page.locator('[data-kanban-col-count="saved"]').inner_text() == "0"
        assert storage.get_opportunity("tr-1").status == "new"
        assert storage.get_opportunity("tr-1").is_seen is True

        # 3. Keep tr-1 again, then Archiver -> hidden + toast Annuler -> Annuler restores 'kept'
        with page.expect_response("**/api/opportunities/tr-1/status"):
            page.click('[data-opp-id="tr-1"] [data-action="toggle-keep"]')
        assert page.locator("#kanban-badge").inner_text() == "1"

        with page.expect_response("**/api/opportunities/tr-1/status"):
            page.click('[data-opp-id="tr-1"] [data-action="archive"]')

        assert page.locator('[data-opp-id="tr-1"]').count() == 0
        assert page.locator("#kanban-badge").inner_text() == "0"
        assert storage.get_opportunity("tr-1").status == "archived"

        undo_btn = page.locator("#toast-container [data-toast-action]")
        undo_btn.first.wait_for(state="visible")
        assert undo_btn.first.inner_text() == "Annuler"

        with page.expect_response("**/api/opportunities/tr-1/status"):
            undo_btn.first.click()

        page.locator('[data-opp-id="tr-1"]').wait_for(state="visible")
        assert "Gardé" in page.locator('[data-opp-id="tr-1"] [data-action="toggle-keep"]').inner_text()
        assert page.locator("#kanban-badge").inner_text() == "1"

        page.reload(wait_until="networkidle")
        assert page.locator('[data-opp-id="tr-1"]').is_visible()
        assert "Gardé" in page.locator('[data-opp-id="tr-1"] [data-action="toggle-keep"]').inner_text()
        assert page.locator("#kanban-badge").inner_text() == "1"
        assert storage.get_opportunity("tr-1").status == "kept"

        # 4. Archiver on unseen tr-2 -> Annuler restores Nouveau badge & SQLite is_seen=False
        with page.expect_response("**/api/opportunities/tr-2/status"):
            page.click('[data-opp-id="tr-2"] [data-action="archive"]')

        assert page.locator('[data-opp-id="tr-2"]').count() == 0
        undo_tr2 = page.locator("#toast-container [data-toast-action]").last
        undo_tr2.wait_for(state="visible")
        with page.expect_response("**/api/opportunities/tr-2/seen"):
            undo_tr2.click()

        page.locator('[data-opp-id="tr-2"]').wait_for(state="visible")
        assert page.locator('[data-opp-id="tr-2"] [data-new-badge]').is_visible()
        assert storage.get_opportunity("tr-2").status == "new"
        assert storage.get_opportunity("tr-2").is_seen is False

        page.reload(wait_until="networkidle")
        assert page.locator('[data-opp-id="tr-2"] [data-new-badge]').is_visible()
        assert storage.get_opportunity("tr-2").is_seen is False

        # Re-archive tr-2 and verify persistence
        with page.expect_response("**/api/opportunities/tr-2/status"):
            page.click('[data-opp-id="tr-2"] [data-action="archive"]')

        assert page.locator('[data-opp-id="tr-2"]').count() == 0
        assert page.locator("#filter-new-count").inner_text() == "0"
        assert storage.get_opportunity("tr-2").status == "archived"
        assert storage.get_opportunity("tr-2").is_seen is True

        page.reload(wait_until="networkidle")
        assert page.locator('[data-opp-id="tr-2"]').count() == 0
        assert page.locator("#feed-count").inner_text() == "1 opportunité"
    finally:
        context.close()

    # Part B: Rollback + toast when /status API fails (Je garde, Archiver, Annuler) + applied/interview guard
    context_err, page_err = _new_isolated_page(browser)
    status_calls = []
    fail_status = {"fail": True}

    def handle_err_status(route):
        opp_id = route.request.url.split("/api/opportunities/")[1].split("/status")[0]
        body = route.request.post_data_json
        status_calls.append((opp_id, body))
        if fail_status["fail"]:
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"detail":"Erreur triage serveur"}',
            )
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "updated", "id": opp_id, "new_status": body["status"], "is_seen": True}),
        )

    _mock_base_apis(
        page_err,
        opps=[
            {
                "id": "rb-tr-1",
                "source": "wttj",
                "opportunity_type": "offer",
                "title": "Rollback Triage",
                "company": "Co",
                "location": "Paris",
                "url": "https://example.com/rb-tr-1",
                "status": "new",
                "is_seen": False,
            },
            {
                "id": "prog-app-2",
                "source": "linkedin",
                "opportunity_type": "offer",
                "title": "Already Applied",
                "company": "Co2",
                "location": "Paris",
                "url": "https://example.com/prog-2",
                "status": "applied",
                "is_seen": True,
            },
        ],
    )
    page_err.route("**/api/opportunities/*/status", handle_err_status)

    try:
        page_err.goto(live_server_url, wait_until="networkidle")

        # Applied/interview guard: toggle-keep button is disabled and does NOT demote or fire PATCH
        prog_btn = page_err.locator('[data-opp-id="prog-app-2"] [data-action="toggle-keep"]')
        assert prog_btn.is_disabled()
        assert "Candidaté" in prog_btn.inner_text()
        page_err.evaluate(
            """async () => {
                const { default: _unused } = await import('/static/js/views/feed.js').catch(() => ({}));
                document.querySelector('[data-opp-id="prog-app-2"] [data-action="toggle-keep"]')?.click();
            }"""
        )
        assert status_calls == []

        # Je garde fails -> rolls back status='new', is_seen=False (Nouveau restored), kanban=0
        with page_err.expect_response("**/api/opportunities/rb-tr-1/status"):
            page_err.click('[data-opp-id="rb-tr-1"] [data-action="toggle-keep"]')

        err_toast = page_err.locator("#toast-container .jp-toast-error")
        err_toast.first.wait_for(state="visible")
        assert "Erreur triage serveur" in err_toast.first.inner_text()
        assert "Je garde" in page_err.locator('[data-opp-id="rb-tr-1"] [data-action="toggle-keep"]').inner_text()
        assert page_err.locator('[data-opp-id="rb-tr-1"] [data-new-badge]').is_visible()
        assert page_err.locator("#kanban-badge").inner_text() == "0"
        assert page_err.locator("#filter-new-count").inner_text() == "1"

        # Archiver fails -> card reinserted, undo toast removed, error toast shown, is_seen=False
        with page_err.expect_response("**/api/opportunities/rb-tr-1/status"):
            page_err.click('[data-opp-id="rb-tr-1"] [data-action="archive"]')

        page_err.locator('[data-opp-id="rb-tr-1"]').wait_for(state="visible")
        assert page_err.locator("#toast-container [data-toast-action]").count() == 0
        assert page_err.locator('[data-opp-id="rb-tr-1"] [data-new-badge]').is_visible()
        assert page_err.locator("#kanban-badge").inner_text() == "0"

        # Archiver succeeds, then Annuler PATCH fails -> rolls back card removal, badge, and emits error toast
        fail_status["fail"] = False
        with page_err.expect_response("**/api/opportunities/rb-tr-1/status"):
            page_err.click('[data-opp-id="rb-tr-1"] [data-action="archive"]')
        assert page_err.locator('[data-opp-id="rb-tr-1"]').count() == 0

        fail_status["fail"] = True
        undo_btn_err = page_err.locator("#toast-container [data-toast-action]").first
        with page_err.expect_response("**/api/opportunities/rb-tr-1/status"):
            undo_btn_err.click()

        assert page_err.locator('[data-opp-id="rb-tr-1"]').count() == 0
        assert page_err.locator("#kanban-badge").inner_text() == "0"
        assert page_err.locator("#toast-container .jp-toast-error").count() >= 1
    finally:
        context_err.close()


def test_modal_hunt_prefill_submit_refetch_and_errors(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    search_requests = []
    opps_fetches = {"count": 0}
    search_mode = {"status": 200}

    initial_opps = [
        {
            "id": "existing-1",
            "source": "wttj",
            "opportunity_type": "offer",
            "title": "Existing Offer",
            "company": "Alpha",
            "location": "Paris",
            "url": "https://example.com/existing-1",
            "status": "new",
            "is_seen": True,
        }
    ]
    refetched_opps = [
        {
            "id": "hunted-new-1",
            "source": "linkedin",
            "opportunity_type": "offer",
            "title": "Lead Product Designer",
            "company": "Nova",
            "location": "Paris, France",
            "url": "https://example.com/hunted-1",
            "score": 88,
            "status": "new",
            "is_seen": False,
        },
        *initial_opps,
    ]

    def handle_opps(route):
        opps_fetches["count"] += 1
        payload = initial_opps if opps_fetches["count"] == 1 else refetched_opps
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    def handle_search(route):
        search_requests.append(
            {
                "url": route.request.url,
                "body": route.request.post_data_json,
                "existing_still_visible": page.locator('[data-opp-id="existing-1"]').is_visible(),
            }
        )
        if search_mode["status"] == 422:
            route.fulfill(
                status=422,
                content_type="application/json",
                body='{"detail":[{"msg":"max_results invalide"}]}',
            )
            return
        if search_mode["status"] == 500:
            route.fulfill(status=500, content_type="application/json", body='{"detail":"Erreur collecte"}')
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"total_found": 2, "new_count": 1, "opportunities": refetched_opps}),
        )

    page.route("**/api/opportunities*", handle_opps)
    page.route("**/api/applications*", lambda r: r.fulfill(status=200, content_type="application/json", body="[]"))
    page.route(
        "**/api/config",
        lambda r: r.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "target_roles": ["Product Designer", "Art Director"],
                    "preferred_locations": ["Lyon"],
                    "remote_only": True,
                    "linkedin_include_posts": False,
                }
            ),
        ),
    )
    page.route("**/api/profile", lambda r: r.fulfill(status=200, content_type="application/json", body='{"profile":{"ideal_job_brief":null}}'))
    page.route("**/api/search", handle_search)

    try:
        page.goto(live_server_url, wait_until="networkidle")
        assert page.locator('[data-opp-id="existing-1"]').is_visible()

        # Open hunt modal -> pre-filled from state.config & LinkedIn toggle disables Posts LinkedIn
        page.click("#btn-open-hunt-modal")
        assert page.locator("#modal-hunt").is_visible()
        assert page.locator("#hunt-keywords").input_value() == "Product Designer, Art Director"
        assert page.locator("#hunt-location").input_value() == "Lyon"
        assert page.locator("#hunt-remote-only").is_checked() is True
        assert page.locator("#hunt-include-posts").is_checked() is False
        assert page.locator('#hunt-form input[data-hunt-source]:checked').count() == 7

        # Unchecking LinkedIn disables & unchecks #hunt-include-posts; re-checking restores it
        page.check("#hunt-include-posts")
        page.uncheck('#hunt-form input[data-hunt-source="linkedin"]')
        assert page.locator("#hunt-include-posts").is_disabled()
        assert page.locator("#hunt-include-posts").is_checked() is False
        page.check('#hunt-form input[data-hunt-source="linkedin"]')
        assert page.locator("#hunt-include-posts").is_enabled()
        assert page.locator("#hunt-include-posts").is_checked() is True

        # Unchecking all 7 sources -> error toast and zero /api/search requests
        for src in ["linkedin", "wttj", "indeed", "hellowork", "glassdoor", "teamtailor", "jobgether"]:
            page.uncheck(f'#hunt-form input[data-hunt-source="{src}"]')
        page.click("#btn-submit-hunt")
        assert page.locator("#toast-container .jp-toast-error").is_visible()
        assert len(search_requests) == 0
        for src in ["linkedin", "wttj", "indeed", "hellowork", "glassdoor", "teamtailor", "jobgether"]:
            page.check(f'#hunt-form input[data-hunt-source="{src}"]')

        # Modify fields, submit -> POST /api/search, re-fetch limit=100, existing kept, modal closed + toast
        page.fill("#hunt-keywords", "Design System, Lead UX")
        page.fill("#hunt-location", "Paris")
        page.uncheck('#hunt-form input[data-hunt-source="glassdoor"]')
        page.check("#hunt-include-posts")

        with page.expect_response("**/api/opportunities?limit=100"):
            page.click("#btn-submit-hunt")

        assert len(search_requests) == 1
        assert search_requests[0]["existing_still_visible"] is True
        assert search_requests[0]["body"] == {
            "keywords": ["Design System", "Lead UX"],
            "location": "Paris",
            "remote_only": True,
            "max_results": 30,
            "sources": ["linkedin", "wttj", "indeed", "hellowork", "teamtailor", "jobgether"],
            "include_posts": True,
            "include_offers": True,
        }
        assert not page.locator("#modal-hunt").is_visible()
        assert page.locator('[data-opp-id="hunted-new-1"]').is_visible()
        assert page.locator('[data-opp-id="hunted-new-1"] [data-score-badge]').inner_text() == "88% Match"
        assert page.locator('[data-opp-id="existing-1"]').is_visible()
        assert page.locator("#toast-container .jp-toast-success").is_visible()

        # 422 error -> toast shown, existing offers intact, submit re-enabled
        search_mode["status"] = 422
        page.click("#btn-open-hunt-modal")
        with page.expect_response("**/api/search"):
            page.click("#btn-submit-hunt")
        err_toast = page.locator("#toast-container .jp-toast-error")
        err_toast.last.wait_for(state="visible")
        assert "max_results invalide" in err_toast.last.inner_text()
        assert page.locator("#modal-hunt").is_visible()
        assert page.locator("#btn-submit-hunt").is_enabled()
        assert page.locator('[data-opp-id="hunted-new-1"]').count() == 1
        page.keyboard.press("Escape")
    finally:
        context.close()

    # Pending single-flight guard + post-search fetchOpportunities 500 fallback merge
    ctx_pending, page_pending = _new_isolated_page(browser)
    pending_calls = {"search": 0, "opps": 0}
    held_routes = []

    def handle_pending_opps(route):
        pending_calls["opps"] += 1
        if pending_calls["opps"] == 1:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(initial_opps))
            return
        route.fulfill(status=500, content_type="application/json", body='{"detail":"refresh failed"}')

    def handle_pending_search(route):
        pending_calls["search"] += 1
        held_routes.append(route)

    page_pending.route("**/api/opportunities*", handle_pending_opps)
    page_pending.route("**/api/applications*", lambda r: r.fulfill(status=200, content_type="application/json", body="[]"))
    page_pending.route("**/api/config", lambda r: r.fulfill(status=200, content_type="application/json", body='{"target_roles":["Designer"]}'))
    page_pending.route("**/api/profile", lambda r: r.fulfill(status=200, content_type="application/json", body='{"profile":{"ideal_job_brief":null}}'))
    page_pending.route("**/api/search", handle_pending_search)

    try:
        page_pending.goto(live_server_url, wait_until="networkidle")
        page_pending.click("#btn-open-hunt-modal")
        page_pending.click("#btn-submit-hunt")

        # While in flight: submit disabled, text 'Chasse en cours...', header aria-busy="true"
        page_pending.wait_for_function("() => document.getElementById('btn-submit-hunt')?.disabled === true")
        assert page_pending.locator("#btn-submit-hunt").inner_text() == "Chasse en cours..."
        assert page_pending.locator("#btn-open-hunt-modal").get_attribute("aria-busy") == "true"

        # Duplicate submit while in flight is ignored (single-flight)
        page_pending.evaluate("() => document.getElementById('hunt-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))")
        page_pending.wait_for_timeout(60)
        assert pending_calls["search"] == 1
        assert len(held_routes) == 1

        held_routes[0].fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"total_found": 1, "new_count": 1, "opportunities": refetched_opps[:1]}),
        )
        page_pending.locator('[data-opp-id="hunted-new-1"]').wait_for(state="visible")
        assert not page_pending.locator("#modal-hunt").is_visible()
        assert page_pending.locator('[data-opp-id="existing-1"]').is_visible()
        assert page_pending.locator("#btn-open-hunt-modal").get_attribute("aria-busy") is None
        assert "rafraîchissement" in page_pending.locator("#toast-container .jp-toast:not(.jp-toast-error)").last.inner_text()
    finally:
        ctx_pending.close()

    # Mobile 375px + configError free submission check
    ctx_err, page_err = _new_isolated_page(browser, width=375, height=812)
    err_searches = []
    page_err.route("**/api/opportunities*", lambda r: r.fulfill(status=200, content_type="application/json", body="[]"))
    page_err.route("**/api/applications*", lambda r: r.fulfill(status=200, content_type="application/json", body="[]"))
    page_err.route("**/api/config", lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"config down"}'))
    page_err.route("**/api/profile", lambda r: r.fulfill(status=200, content_type="application/json", body='{"profile":{"ideal_job_brief":null}}'))
    page_err.route(
        "**/api/search",
        lambda r: (
            err_searches.append(r.request.post_data_json),
            r.fulfill(status=200, content_type="application/json", body='{"total_found":0,"new_count":0,"opportunities":[]}'),
        ),
    )

    try:
        page_err.goto(live_server_url, wait_until="networkidle")
        page_err.click("#btn-open-hunt-modal")
        assert page_err.locator("#modal-hunt").is_visible()
        assert page_err.locator("#modal-hunt-preview").inner_text() == "Configuration indisponible pour le moment."
        assert not page_err.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")

        page_err.fill("#hunt-keywords", "Python")
        page_err.fill("#hunt-location", "Remote")
        with page_err.expect_response("**/api/opportunities?limit=100"):
            page_err.click("#btn-submit-hunt")

        assert len(err_searches) == 1
        assert err_searches[0]["keywords"] == ["Python"]
        assert err_searches[0]["location"] == "Remote"
        assert not page_err.locator("#modal-hunt").is_visible()
    finally:
        ctx_err.close()


def test_profile_hydration_on_boot_reload_and_error_handling(browser, live_server_url):
    context, page = _new_isolated_page(browser)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    brief_state = {"value": "Lead Product Designer Figma full remote"}
    put_calls = []

    def handle_profile(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "user": {"id": "usr_1", "email": "user@jobby.local", "full_name": "Mon Profil Candidat"},
                    "profile": {
                        "user_id": "usr_1",
                        "target_roles": ["Product Designer"],
                        "skills": ["Figma"],
                        "ideal_job_brief": brief_state["value"],
                    },
                    "has_cv": False,
                }
            ),
        )

    def handle_profile_brief(route):
        payload = route.request.post_data_json
        put_calls.append({"method": route.request.method, "body": payload})
        raw = payload.get("brief") if isinstance(payload, dict) else None
        cleaned = raw.strip() if isinstance(raw, str) else ""
        brief_state["value"] = cleaned or None
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "status": "saved",
                    "ideal_job_brief": brief_state["value"],
                    "profile": {
                        "user_id": "usr_1",
                        "target_roles": ["Product Designer"],
                        "skills": ["Figma"],
                        "ideal_job_brief": brief_state["value"],
                    },
                }
            ),
        )

    _mock_base_apis(page)
    page.unroute("**/api/profile")
    page.route("**/api/profile", handle_profile)
    page.route("**/api/profile/brief", handle_profile_brief)

    try:
        # 1. Boot hydrates state.profile via GET /api/profile
        page.goto(live_server_url, wait_until="networkidle")
        boot_state = page.evaluate(
            """async () => {
                const { getState } = await import('/static/js/state.js');
                return { profile: getState().profile, profileError: getState().profileError };
            }"""
        )
        assert boot_state["profileError"] is None
        assert boot_state["profile"]["ideal_job_brief"] == "Lead Product Designer Figma full remote"

        # 2. saveProfileBrief() + setIdealJobBrief() + extractProfileObject() edge cases (BH-3, BH-5, EH-5)
        save_res = page.evaluate(
            """async () => {
                const { saveProfileBrief } = await import('/static/js/api.js');
                const { getState, setProfile, setIdealJobBrief, subscribe } = await import('/static/js/state.js');
                const events = [];
                const unsub = subscribe((snap) => events.push(snap.profile?.ideal_job_brief ?? null));
                const apiResp = await saveProfileBrief('  Design Ops Staff Remote  ');
                setIdealJobBrief(apiResp.ideal_job_brief);
                const blankRes = await saveProfileBrief('   ');
                setIdealJobBrief(blankRes.ideal_job_brief);
                const finalState = getState().profile;

                // Trimming + non-emission on duplicate + ignoring non-string/non-nullish values (BH-3)
                const beforeCount = events.length;
                setIdealJobBrief('  Design Ops Reloaded  ');
                const afterTrimCount = events.length;
                setIdealJobBrief('Design Ops Reloaded'); // duplicate -> no-op
                setIdealJobBrief(123); // non-string ignored
                setIdealJobBrief({ invalid: true }); // non-string ignored
                const afterIgnoredCount = events.length;
                const afterIgnoredBrief = getState().profile?.ideal_job_brief;

                // extractProfileObject rejects arrays (EH-5)
                const savedProfile = getState().profile;
                setProfile(['not-a-profile']);
                const arrayRejected = getState().profile;
                setProfile({ profile: ['nested-array'] });
                const nestedArrayRejected = getState().profile;
                setProfile(savedProfile);

                unsub();
                return {
                    apiResp,
                    blankRes,
                    finalState,
                    events,
                    beforeCount,
                    afterTrimCount,
                    afterIgnoredCount,
                    afterIgnoredBrief,
                    arrayRejected,
                    nestedArrayRejected,
                };
            }"""
        )
        assert len(put_calls) == 2
        assert put_calls[0] == {"method": "PUT", "body": {"brief": "  Design Ops Staff Remote  "}}
        assert put_calls[1] == {"method": "PUT", "body": {"brief": "   "}}
        assert save_res["apiResp"]["ideal_job_brief"] == "Design Ops Staff Remote"
        assert save_res["blankRes"]["ideal_job_brief"] is None
        assert save_res["finalState"]["user_id"] == "usr_1"
        assert save_res["finalState"]["target_roles"] == ["Product Designer"]
        assert save_res["finalState"]["skills"] == ["Figma"]
        assert save_res["finalState"]["ideal_job_brief"] is None
        assert save_res["afterTrimCount"] == save_res["beforeCount"] + 1
        assert save_res["afterIgnoredCount"] == save_res["afterTrimCount"]
        assert save_res["afterIgnoredBrief"] == "Design Ops Reloaded"
        assert save_res["arrayRejected"] is None
        assert save_res["nestedArrayRejected"] is None
        assert save_res["events"][:3] == ["Design Ops Staff Remote", None, "Design Ops Reloaded"]

        # 3. Page reload re-hydrates state.profile from GET /api/profile
        brief_state["value"] = "Principal Designer Reloaded"
        page.reload(wait_until="networkidle")
        reloaded_state = page.evaluate(
            """async () => {
                const { getState } = await import('/static/js/state.js');
                return getState().profile;
            }"""
        )
        assert reloaded_state["ideal_job_brief"] == "Principal Designer Reloaded"
        assert errors == []
    finally:
        context.close()

    # 4. API failure on GET /api/profile sets state.profileError and recovers via setIdealJobBrief
    ctx_err, page_err = _new_isolated_page(browser)
    err_page_errors = []
    page_err.on("pageerror", lambda err: err_page_errors.append(str(err)))
    _mock_base_apis(page_err)
    page_err.unroute("**/api/profile")
    page_err.route(
        "**/api/profile",
        lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"profile down"}'),
    )
    try:
        page_err.goto(live_server_url, wait_until="networkidle")
        err_state = page_err.evaluate(
            """async () => {
                const { getState, setIdealJobBrief } = await import('/static/js/state.js');
                const before = { profile: getState().profile, profileError: getState().profileError };
                setIdealJobBrief('  Recovered Brief  ');
                const after = { profile: getState().profile, profileError: getState().profileError };
                return { before, after };
            }"""
        )
        assert err_state["before"]["profile"] is None
        assert err_state["before"]["profileError"] == "profile down"
        assert err_state["after"]["profile"]["ideal_job_brief"] == "Recovered Brief"
        assert err_state["after"]["profileError"] is None
        assert err_page_errors == []
    finally:
        ctx_err.close()

    # 5. Late GET /api/profile resolution does not overwrite concurrent setIdealJobBrief() (EH-7)
    ctx_race, page_race = _new_isolated_page(browser)
    held_profile_routes = []
    _mock_base_apis(page_race)
    page_race.unroute("**/api/profile")
    page_race.route("**/api/profile", lambda r: held_profile_routes.append(r))
    try:
        page_race.goto(live_server_url, wait_until="domcontentloaded")
        page_race.wait_for_function("() => document.readyState === 'complete'")
        page_race.wait_for_timeout(100)
        assert len(held_profile_routes) == 1

        page_race.evaluate(
            """async () => {
                const { setIdealJobBrief } = await import('/static/js/state.js');
                setIdealJobBrief('InFlight User Edit');
            }"""
        )
        held_profile_routes[0].fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"profile": {"user_id": "usr_1", "ideal_job_brief": "Stale Server Brief"}}),
        )
        page_race.wait_for_timeout(100)
        race_brief = page_race.evaluate(
            """async () => {
                const { getState } = await import('/static/js/state.js');
                return getState().profile?.ideal_job_brief ?? null;
            }"""
        )
        assert race_brief == "InFlight User Edit"
    finally:
        ctx_race.close()


def test_profile_brief_e2e_unmocked_sqlite_persistence(browser, live_server_url, monkeypatch, tmp_path):
    from jobby.config import JobbyConfig, save_config

    db_path = tmp_path / "e2e_brief.db"
    cfg_path = tmp_path / "e2e_config.yaml"
    monkeypatch.setenv("JOBBY_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("JOBBY_CONFIG_PATH", str(cfg_path))
    save_config(
        JobbyConfig(
            target_roles=["Lead Designer", "Design Ops"],
            skills_keywords=["Figma", "Design System"],
            min_salary=70000,
            remote_only=True,
            database_path=str(db_path),
        ),
        config_path=cfg_path,
    )

    context, page = _new_isolated_page(browser)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))

    try:
        # 1. Cold boot against empty SQLite DB -> GET /api/profile auto-creates profile with config defaults
        page.goto(live_server_url, wait_until="networkidle")
        boot_profile = page.evaluate(
            """async () => {
                const { getState } = await import('/static/js/state.js');
                return { profile: getState().profile, profileError: getState().profileError };
            }"""
        )
        assert boot_profile["profileError"] is None
        assert boot_profile["profile"]["ideal_job_brief"] is None
        assert boot_profile["profile"]["target_roles"] == ["Lead Designer", "Design Ops"]
        assert boot_profile["profile"]["skills"] == ["Figma", "Design System"]

        # 2. Save brief via unmocked saveProfileBrief() -> persists to SQLite & survives page reload
        saved = page.evaluate(
            """async () => {
                const { saveProfileBrief } = await import('/static/js/api.js');
                return await saveProfileBrief('  E2E Lead Designer Remote  ');
            }"""
        )
        assert saved["status"] == "saved"
        assert saved["ideal_job_brief"] == "E2E Lead Designer Remote"

        page.reload(wait_until="networkidle")
        reloaded = page.evaluate(
            """async () => {
                const { getState } = await import('/static/js/state.js');
                return getState().profile;
            }"""
        )
        assert reloaded["ideal_job_brief"] == "E2E Lead Designer Remote"

        # 3. Calling saveProfileBrief() without argument (undefined) -> HTTP 422 + error toast, DB brief untouched (BH-5)
        missing_res = page.evaluate(
            """async () => {
                const { saveProfileBrief, ApiError } = await import('/static/js/api.js');
                try {
                    await saveProfileBrief();
                    return { ok: true };
                } catch (err) {
                    return { ok: false, isApiError: err instanceof ApiError, status: err.status };
                }
            }"""
        )
        assert missing_res == {"ok": False, "isApiError": True, "status": 422}
        assert page.locator("#toast-container .jp-toast-error").count() >= 1

        storage = Storage(db_path=db_path)
        default_user = storage.get_user_by_email("user@jobby.local")
        assert storage.get_profile(default_user.id).ideal_job_brief == "E2E Lead Designer Remote"

        # 4. Clearing with whitespace '   ' and with explicit null both clear SQLite to None
        cleared_ws = page.evaluate(
            """async () => {
                const { saveProfileBrief } = await import('/static/js/api.js');
                return await saveProfileBrief('   ');
            }"""
        )
        assert cleared_ws["ideal_job_brief"] is None
        page.reload(wait_until="networkidle")
        after_ws_reload = page.evaluate(
            """async () => {
                const { getState } = await import('/static/js/state.js');
                return getState().profile.ideal_job_brief;
            }"""
        )
        assert after_ws_reload is None

        page.evaluate(
            """async () => {
                const { saveProfileBrief } = await import('/static/js/api.js');
                await saveProfileBrief('Temporary Brief');
                await saveProfileBrief(null);
            }"""
        )
        assert storage.get_profile(default_user.id).ideal_job_brief is None
        assert errors == []
    finally:
        context.close()
