"""Unit tests for AI & NLP Post Analyzer, Remote Policy Detection, and Gemini reliability."""

import json
import logging
import httpx
import pytest
from jobby.ai_analyzer import (
    analyze_post_heuristic,
    analyze_post_with_llm,
    analyze_post_with_llm_with_meta,
    detect_remote_policy,
    evaluate_candidate_match,
    evaluate_candidate_match_with_meta,
    generate_tailored_cover_letter,
    generate_tailored_cover_letter_with_meta,
    rank_opportunities_by_ideal_brief,
    rank_opportunities_by_ideal_brief_with_meta,
)


def test_detect_remote_policy():
    # 100% remote variations
    assert detect_remote_policy(title="Backend Dev Full Remote", description="Poste ouvert en 100% télétravail") == "full"
    assert detect_remote_policy(title="Python Engineer", description="Totalement à distance, remote France") == "full"
    assert detect_remote_policy(title="Dev", description="", location="100% Télétravail / Remote") == "full"

    # Hybrid variations
    assert detect_remote_policy(title="Dev Python", description="Politique hybride avec 2 jours de télétravail par semaine") == "hybrid"
    assert detect_remote_policy(title="Data Scientist", description="Télétravail flexible (3j/semaine)") == "hybrid"

    # On-site variations
    assert detect_remote_policy(title="Dev C++", description="Poste 100% présentiel sur site à Paris, pas de télétravail") == "onsite"

    # Unknown
    assert detect_remote_policy(title="Dev Python", description="Salaire 55k") == "unknown"


def test_analyze_post_candidate_seeking_alternance():
    text = (
        "Bonjour le réseau ! Actuellement étudiante en Master 1 Data Science, "
        "je suis activement à la recherche d’une alternance de 12 mois à partir de septembre 2026. "
        "Rythme 3j entreprise / 2j école. N'hésitez pas à partager mon CV ! #recherchealternance #alternance"
    )
    result = analyze_post_heuristic(text, author_name="Julie", author_title="Étudiante en Data")
    assert result["intent"] == "candidate_seeking_alternance"
    assert result["is_candidate"] is True
    assert result["is_alternance"] is True
    assert "alternance" in result["summary"].lower()


def test_analyze_post_hiring_alternance():
    text = (
        "Notre équipe Tech s'agrandit ! Nous recrutons un alternant Développeur Python (F/H) "
        "pour la rentrée 2026. Télétravail partiel possible (2j/semaine). Rejoignez-nous !"
    )
    result = analyze_post_heuristic(text, author_name="Paul", author_title="Tech Lead")
    assert result["intent"] == "hiring_alternance"
    assert result["is_candidate"] is False
    assert result["is_alternance"] is True
    assert result["remote_policy"] == "hybrid"


def test_analyze_post_hiring_job():
    text = (
        "On recrute ! Nous cherchons un Lead Developer FastAPI / PostgreSQL en CDI. "
        "100% remote possible partout en France. Contactez-moi en MP."
    )
    result = analyze_post_heuristic(text, author_name="Sophie", author_title="DRH")
    assert result["intent"] == "hiring_job"
    assert result["is_candidate"] is False
    assert result["is_alternance"] is False
    assert result["remote_policy"] == "full"


class _FakeResponse:
    def __init__(self, status_code: int, payload: object, raw_text: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self._raw_text = raw_text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _gemini_text_response(text: str, status_code: int = 200) -> _FakeResponse:
    return _FakeResponse(
        status_code,
        {"candidates": [{"content": {"parts": [{"text": text}]}}]},
    )


def _install_fake_httpx(monkeypatch, handler):
    calls = []

    class FakeAsyncClient:
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

    monkeypatch.setattr("jobby.ai_analyzer.httpx.AsyncClient", FakeAsyncClient)
    return calls


@pytest.mark.anyio
async def test_gemini_calls_send_thinking_budget_zero_and_timeout_gte_35(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    async def handler(call):
        prompt_text = call["json"]["contents"][0]["parts"][0]["text"]
        if "Analyse ce post LinkedIn" in prompt_text:
            return _gemini_text_response(
                json.dumps({
                    "intent": "hiring_job",
                    "is_candidate": False,
                    "is_alternance": False,
                    "summary": "Offre Lead Python",
                    "remote_policy": "full",
                })
            )
        if "Évalue la compatibilité" in prompt_text:
            return _gemini_text_response(
                json.dumps({
                    "score": 88,
                    "verdict": "Excellent fit",
                    "strengths": ["Python"],
                    "weaknesses": ["Aucun"],
                    "pitch": "Bonjour!",
                })
            )
        if "Rédige une lettre de motivation" in prompt_text:
            return _gemini_text_response("Lettre personnalisée Gemini.")
        return _gemini_text_response(
            json.dumps([{"id": "opp-1", "score": 91, "verdict": "Top", "pitch": "Go"}])
        )

    calls = _install_fake_httpx(monkeypatch, handler)

    post_res = await analyze_post_with_llm_with_meta("On recrute un Lead Python")
    assert post_res["provider"] == "gemini"
    assert post_res["provider_used"] == "gemini"
    assert post_res["used_fallback"] is False
    assert post_res["fallback_reason"] is None

    match_res = await evaluate_candidate_match_with_meta(
        {"skills": ["Python"], "target_roles": ["Lead Python"]},
        {"title": "Lead Python", "description": "FastAPI"},
    )
    assert match_res["score"] == 88.0
    assert match_res["provider_used"] == "gemini"
    assert match_res["used_fallback"] is False
    assert match_res["fallback_reason"] is None

    letter_str = await generate_tailored_cover_letter(
        {"skills": ["Python"], "target_roles": ["Lead Python"]},
        {"title": "Lead Python", "company": "Acme", "description": "FastAPI"},
    )
    assert letter_str == "Lettre personnalisée Gemini."

    letter_meta = await generate_tailored_cover_letter_with_meta(
        {"skills": ["Python"], "target_roles": ["Lead Python"]},
        {"title": "Lead Python", "company": "Acme", "description": "FastAPI"},
    )
    assert letter_meta["cover_letter"] == "Lettre personnalisée Gemini."
    assert letter_meta["provider_used"] == "gemini"
    assert letter_meta["used_fallback"] is False
    assert letter_meta["fallback_reason"] is None

    rank_meta = await rank_opportunities_by_ideal_brief_with_meta(
        "Lead Python remote",
        [{"id": "opp-1", "title": "Lead Python", "description": "FastAPI", "remote_policy": "full"}],
    )
    assert rank_meta["provider_used"] == "gemini"
    assert rank_meta["used_fallback"] is False
    assert rank_meta["fallback_reason"] is None
    assert rank_meta["results"][0]["provider_used"] == "gemini"

    assert len(calls) == 5
    for call in calls:
        assert "gemini-flash-latest:generateContent" in call["url"]
        assert "?key=" not in call["url"]
        assert call["headers"] == {"x-goog-api-key": "fake-gemini-key"}
        assert call["timeout"] >= 35.0
        assert call["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


@pytest.mark.anyio
async def test_rank_by_brief_batches_20_opportunities_15_plus_5(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    opps = [
        {"id": f"opp-{i}", "title": f"Role {i}", "company": "Studio", "location": "Paris", "remote_policy": "full", "description": "Python Figma"}
        for i in range(20)
    ]

    async def handler(call):
        prompt = call["json"]["contents"][0]["parts"][0]["text"]
        batch_json_str = prompt.split("--- LISTE DES OPPORTUNITÉS ---\n", 1)[1].split("\n\nPour CHAQUE élément", 1)[0]
        batch_items = json.loads(batch_json_str)
        scored = [
            {"id": item["id"], "score": 80 + (idx % 15), "verdict": f"Verdict {item['id']}", "pitch": "Pitch"}
            for idx, item in enumerate(batch_items)
        ]
        return _gemini_text_response(json.dumps(scored))

    calls = _install_fake_httpx(monkeypatch, handler)

    ranked_list = await rank_opportunities_by_ideal_brief("Role Python Figma", opps)
    assert len(ranked_list) == 20
    assert all(item["provider_used"] == "gemini" and item["used_fallback"] is False and item["fallback_reason"] is None for item in ranked_list)

    meta_res = await rank_opportunities_by_ideal_brief_with_meta("Role Python Figma", opps)
    assert meta_res["provider_used"] == "gemini"
    assert meta_res["used_fallback"] is False
    assert meta_res["fallback_reason"] is None
    assert len(meta_res["results"]) == 20

    # Verify batch sizes across both runs: 2 batches per call (15 + 5)
    assert len(calls) == 4
    batch_sizes = []
    for call in calls[:2]:
        assert call["timeout"] >= 35.0
        assert call["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
        prompt = call["json"]["contents"][0]["parts"][0]["text"]
        batch_items = json.loads(prompt.split("--- LISTE DES OPPORTUNITÉS ---\n", 1)[1].split("\n\nPour CHAQUE élément", 1)[0])
        batch_sizes.append(len(batch_items))
    assert sorted(batch_sizes) == [5, 15]


@pytest.mark.anyio
async def test_rank_by_brief_mixed_batches_one_ok_one_timeout(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    opps = [
        {"id": f"opp-{i}", "title": f"Design System {i}", "company": "Co", "location": "Remote", "remote_policy": "full", "description": "Figma remote"}
        for i in range(20)
    ]

    async def handler(call):
        prompt = call["json"]["contents"][0]["parts"][0]["text"]
        batch_items = json.loads(prompt.split("--- LISTE DES OPPORTUNITÉS ---\n", 1)[1].split("\n\nPour CHAQUE élément", 1)[0])
        if len(batch_items) == 5:
            raise httpx.ReadTimeout("simulated batch 2 timeout")
        return _gemini_text_response(
            json.dumps([{"id": item["id"], "score": 90, "verdict": "Gemini OK", "pitch": "Pitch"} for item in batch_items])
        )

    _install_fake_httpx(monkeypatch, handler)

    with caplog.at_level(logging.WARNING, logger="jobby.ai"):
        meta_res = await rank_opportunities_by_ideal_brief_with_meta("Design System Figma remote", opps)

    assert meta_res["provider_used"] == "gemini"
    assert meta_res["used_fallback"] is True
    assert meta_res["fallback_reason"]
    assert "Timeout" in meta_res["fallback_reason"] or "timeout" in meta_res["fallback_reason"].lower()

    by_id = {item["id"]: item for item in meta_res["results"]}
    assert len(by_id) == 20
    for i in range(15):
        assert by_id[f"opp-{i}"]["provider_used"] == "gemini"
        assert by_id[f"opp-{i}"]["used_fallback"] is False
        assert by_id[f"opp-{i}"]["fallback_reason"] is None
    for i in range(15, 20):
        assert by_id[f"opp-{i}"]["provider_used"] == "heuristic"
        assert by_id[f"opp-{i}"]["used_fallback"] is True
        assert by_id[f"opp-{i}"]["fallback_reason"]
    assert any("Batch Gemini scoring failed" in rec.message for rec in caplog.records)


@pytest.mark.anyio
async def test_gemini_fallbacks_missing_key_http_errors_and_invalid_json(monkeypatch, caplog):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # 1. Missing key across all 4 functions
    with caplog.at_level(logging.WARNING, logger="jobby.ai"):
        post_no_key = await analyze_post_with_llm_with_meta("On recrute un dev Python")
        match_no_key = await evaluate_candidate_match({"skills": ["Python"], "target_roles": ["Dev"]}, {"title": "Dev Python", "description": "Python"})
        letter_no_key = await generate_tailored_cover_letter_with_meta({"skills": ["Python"]}, {"title": "Dev Python", "company": "Acme"})
        rank_no_key = await rank_opportunities_by_ideal_brief_with_meta("Dev Python", [{"id": "o1", "title": "Dev Python", "description": "Python"}])

    for res in (post_no_key, match_no_key, letter_no_key, rank_no_key):
        assert res["provider_used"] == "heuristic"
        assert res["used_fallback"] is True
        assert res["fallback_reason"]
    assert post_no_key["provider"] == "heuristic"
    assert rank_no_key["results"][0]["provider_used"] == "heuristic"
    assert rank_no_key["results"][0]["used_fallback"] is True

    # 2. HTTP 429 & HTTP 503 & JSON KO
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    for status_code in (429, 503):
        async def err_handler(call, code=status_code):
            return _FakeResponse(code, {"error": "rate limit"})

        _install_fake_httpx(monkeypatch, err_handler)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="jobby.ai"):
            m_err = await evaluate_candidate_match({"skills": ["Python"], "target_roles": ["Dev"]}, {"title": "Dev Python", "description": "Python"})
            l_err = await generate_tailored_cover_letter_with_meta({"skills": ["Python"]}, {"title": "Dev Python", "company": "Acme"})
            r_err = await rank_opportunities_by_ideal_brief_with_meta("Dev Python", [{"id": "o1", "title": "Dev Python", "description": "Python"}])
            p_err = await analyze_post_with_llm_with_meta("On recrute un dev Python")

        for res in (m_err, l_err, r_err, p_err):
            assert res["provider_used"] == "heuristic"
            assert res["used_fallback"] is True
            assert str(status_code) in res["fallback_reason"]
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)

    # 3. Invalid JSON on HTTP 200
    async def bad_json_handler(call):
        return _gemini_text_response("{broken-json")

    _install_fake_httpx(monkeypatch, bad_json_handler)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="jobby.ai"):
        m_bad = await evaluate_candidate_match({"skills": ["Python"], "target_roles": ["Dev"]}, {"title": "Dev Python", "description": "Python"})
        r_bad = await rank_opportunities_by_ideal_brief_with_meta("Dev Python", [{"id": "o1", "title": "Dev Python", "description": "Python"}])
        p_bad = await analyze_post_with_llm_with_meta("On recrute un dev Python")

    for res in (m_bad, r_bad, p_bad):
        assert res["provider_used"] == "heuristic"
        assert res["used_fallback"] is True
        assert res["fallback_reason"]
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


@pytest.mark.anyio
async def test_analyze_post_gemma_ollama_and_openai_untouched(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    async def gemma_handler(call):
        if "gemini-flash-latest" in call["url"]:
            return _FakeResponse(404, {"error": "not found"})
        if "gemma-4-26b-a4b-it" in call["url"]:
            return _gemini_text_response(
                json.dumps({
                    "intent": "hiring_job",
                    "is_candidate": False,
                    "is_alternance": False,
                    "summary": "Gemma OK",
                    "remote_policy": "hybrid",
                })
            )
        raise AssertionError(f"Unexpected URL: {call['url']}")

    gemma_calls = _install_fake_httpx(monkeypatch, gemma_handler)
    res_gemma = await analyze_post_with_llm("On recrute un dev FastAPI", provider="gemini")
    assert res_gemma["provider"] == "gemini-gemma"
    assert res_gemma["provider_used"] == "gemini-gemma"
    assert res_gemma["used_fallback"] is True
    assert "404" in res_gemma["fallback_reason"]
    assert gemma_calls[0]["timeout"] >= 35.0
    assert gemma_calls[0]["headers"] == {"x-goog-api-key": "fake-gemini-key"}
    assert "?key=" not in gemma_calls[0]["url"]
    assert gemma_calls[0]["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert gemma_calls[1]["headers"] == {"x-goog-api-key": "fake-gemini-key"}
    assert "?key=" not in gemma_calls[1]["url"]
    assert "generationConfig" not in gemma_calls[1]["json"]
    assert "thinkingConfig" not in json.dumps(gemma_calls[1]["json"])

    # Ollama untouched
    async def ollama_handler(call):
        return _FakeResponse(
            200,
            {"response": json.dumps({"intent": "hiring_job", "is_candidate": False, "is_alternance": False, "summary": "Ollama OK", "remote_policy": "full"})},
        )

    ollama_calls = _install_fake_httpx(monkeypatch, ollama_handler)
    res_ollama = await analyze_post_with_llm("On recrute", provider="ollama")
    assert res_ollama["provider"] == "ollama"
    assert res_ollama["provider_used"] == "ollama"
    assert res_ollama["used_fallback"] is False
    assert ollama_calls[0]["timeout"] == 8.0
    assert "thinkingConfig" not in json.dumps(ollama_calls[0]["json"])

    # OpenAI untouched
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")

    async def openai_handler(call):
        return _FakeResponse(
            200,
            {"choices": [{"message": {"content": json.dumps({"intent": "hiring_job", "is_candidate": False, "is_alternance": False, "summary": "OpenAI OK", "remote_policy": "full"})}}]},
        )

    openai_calls = _install_fake_httpx(monkeypatch, openai_handler)
    res_openai = await analyze_post_with_llm("On recrute", provider="openai")
    assert res_openai["provider"] == "openai"
    assert res_openai["provider_used"] == "openai"
    assert res_openai["used_fallback"] is False
    assert openai_calls[0]["timeout"] == 10.0
    assert "thinkingConfig" not in json.dumps(openai_calls[0]["json"])


@pytest.mark.anyio
async def test_ai_analyzer_provider_routing_security_and_heuristic_non_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gem-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")

    # 1. provider="heuristic" (and whitespace/case variations) is NEVER treated as fallback
    for raw_prov in ("heuristic", " HEURISTIC "):
        res_heur = await analyze_post_with_llm("On recrute Python", provider=raw_prov)
        assert res_heur["provider"] == "heuristic"
        assert res_heur["provider_used"] == "heuristic"
        assert res_heur["used_fallback"] is False
        assert res_heur["fallback_reason"] is None

    # 2. Explicit or auto sk- OpenAI key must NEVER route to Gemini
    async def openai_handler(call):
        assert "api.openai.com" in call["url"]
        return _FakeResponse(
            200,
            {"choices": [{"message": {"content": json.dumps({"intent": "hiring_job", "is_candidate": False, "is_alternance": False, "summary": "OpenAI routed", "remote_policy": "full"})}}]},
        )

    auto_calls = _install_fake_httpx(monkeypatch, openai_handler)
    res_auto_sk = await analyze_post_with_llm("On recrute", provider="auto", api_key="sk-explicit-openai")
    assert res_auto_sk["provider_used"] == "openai"
    assert res_auto_sk["used_fallback"] is False
    assert auto_calls[0]["headers"]["Authorization"] == "Bearer sk-explicit-openai"

    # 3. ollama_url=None falls back cleanly to default localhost without AttributeError
    async def ollama_none_handler(call):
        assert call["url"] == "http://localhost:11434/api/generate"
        return _FakeResponse(
            200,
            {"response": json.dumps({"intent": "hiring_job", "is_candidate": False, "is_alternance": False, "summary": "Ollama default", "remote_policy": "unknown"})},
        )

    _install_fake_httpx(monkeypatch, ollama_none_handler)
    res_ollama_none = await analyze_post_with_llm("On recrute", provider="ollama", ollama_url=None)
    assert res_ollama_none["provider_used"] == "ollama"
    assert res_ollama_none["used_fallback"] is False

    # 4. Secret / query string redaction in fallback_reason when httpx raises with URL/key in exception message
    async def leaky_exc_handler(call):
        raise RuntimeError("Request failed for https://example.com/v1?key=secret-gem-key token=secret-gem-key")

    _install_fake_httpx(monkeypatch, leaky_exc_handler)
    res_leak = await evaluate_candidate_match(
        {"skills": ["Python"], "target_roles": ["Dev"]},
        {"title": "Dev Python", "description": "Python"},
        api_key="secret-gem-key",
    )
    assert res_leak["used_fallback"] is True
    assert "secret-gem-key" not in res_leak["fallback_reason"]
    assert "?key=" not in res_leak["fallback_reason"]


@pytest.mark.anyio
async def test_gemini_multipart_safety_block_and_uppercase_fences(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    # 1. Multi-part response skipping thought=True + uppercase ```JSON fence
    async def multipart_handler(call):
        return _FakeResponse(
            200,
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {"thought": True, "text": "internal thought ignore me"},
                                {"text": "```JSON\n{\"intent\": \"hiring_job\", \"is_candidate\": false, "},
                                {"text": "\"is_alternance\": false, \"summary\": \"Multi part OK\", \"remote_policy\": \"full\"}\n```"},
                            ]
                        },
                    }
                ]
            },
        )

    _install_fake_httpx(monkeypatch, multipart_handler)
    res_mp = await analyze_post_with_llm("On recrute", provider="gemini")
    assert res_mp["provider_used"] == "gemini"
    assert res_mp["used_fallback"] is False
    assert res_mp["summary"] == "Multi part OK"

    # 2. promptFeedback.blockReason surfaced
    async def block_handler(call):
        return _FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}})

    _install_fake_httpx(monkeypatch, block_handler)
    res_blk = await generate_tailored_cover_letter_with_meta({"skills": ["Python"]}, {"title": "Dev", "company": "Acme"})
    assert res_blk["provider_used"] == "heuristic"
    assert res_blk["used_fallback"] is True
    assert "blockReason=SAFETY" in res_blk["fallback_reason"]

    # 3. finishReason=SAFETY with no text parts surfaced
    async def finish_safety_handler(call):
        return _FakeResponse(200, {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]})

    _install_fake_httpx(monkeypatch, finish_safety_handler)
    res_fin = await evaluate_candidate_match({"skills": ["Python"], "target_roles": ["Dev"]}, {"title": "Dev"})
    assert res_fin["provider_used"] == "heuristic"
    assert res_fin["used_fallback"] is True
    assert "finishReason=SAFETY" in res_fin["fallback_reason"]


@pytest.mark.anyio
async def test_edge_case_schemas_nulls_unhashable_nan_and_semaphore(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    # 1. Incomplete {} dict on analyze_post_with_llm triggers fallback
    async def empty_dict_handler(call):
        return _gemini_text_response("{}")

    _install_fake_httpx(monkeypatch, empty_dict_handler)
    res_empty_post = await analyze_post_with_llm("On recrute Python", provider="gemini")
    assert res_empty_post["provider_used"] == "heuristic"
    assert res_empty_post["used_fallback"] is True
    assert "intent" in res_empty_post["fallback_reason"]

    # 2. evaluate_candidate_match rejects missing score, NaN score, bool score, empty verdict;
    #    and _heuristic_cv_match survives target_roles=[] and None skills
    for bad_payload in (
        {"verdict": "Missing score"},
        {"score": float("nan"), "verdict": "NaN score"},
        {"score": True, "verdict": "Bool score"},
        {"score": 85, "verdict": "   "},
    ):
        async def bad_match_handler(call, body=bad_payload):
            # json.dumps allows NaN in Python unless allow_nan=False; send raw JSON text for NaN
            txt = '{"score": NaN, "verdict": "NaN score"}' if isinstance(body.get("score"), float) else json.dumps(body)
            return _gemini_text_response(txt)

        _install_fake_httpx(monkeypatch, bad_match_handler)
        res_m = await evaluate_candidate_match(
            {"target_roles": [], "skills": None, "cv_text": None},
            {"title": None, "description": None, "author_name": None},
        )
        assert res_m["provider_used"] == "heuristic"
        assert res_m["used_fallback"] is True
        assert res_m["pitch"]

    # 3. rank_opportunities_by_ideal_brief: partial omission, unhashable id, Infinity/NaN/bool/OverflowError scores,
    #    null text/verdict/pitch, and None fields on SimpleNamespace opportunities & profile_data
    opps_with_nones = [
        SimpleNamespace(id="ok-1", title="Lead Designer", company=None, location=None, remote_policy=None, description=None, opportunity_type=None),
        SimpleNamespace(id="omit-2", title=None, company="Studio", location="Paris", remote_policy="full", description="Figma", opportunity_type="offer"),
        {"id": "bad-bool", "title": "Job", "description": "Figma"},
        {"id": "bad-inf", "title": "Job", "description": "Figma"},
        {"id": "bad-big", "title": "Job", "description": "Figma"},
        {"id": "null-txt", "title": "Job", "description": "Figma"},
    ]

    async def weird_rank_handler(call):
        # Raw JSON containing unhashable list id, bool score, Infinity, huge 1e500 / 10**400 integer, null verdict/pitch, omitted item
        raw_json = (
            '['
            '{"id": ["unhashable"], "score": 90, "verdict": "Skip"},'
            '{"id": "ok-1", "score": 88, "verdict": null, "pitch": null},'
            '{"id": "bad-bool", "score": true, "verdict": "Bool"},'
            '{"id": "bad-inf", "score": Infinity, "verdict": "Inf"},'
            f'{{"id": "bad-big", "score": {10**400}, "verdict": "Big"}},'
            '{"id": "null-txt", "score": 77, "verdict": null, "pitch": null}'
            ']'
        )
        return _gemini_text_response(raw_json)

    _install_fake_httpx(monkeypatch, weird_rank_handler)
    weird_meta = await rank_opportunities_by_ideal_brief_with_meta(
        "Lead Designer Figma",
        opps_with_nones,
        profile_data={"target_roles": None, "skills": None},
    )
    assert weird_meta["provider_used"] == "gemini"
    assert weird_meta["used_fallback"] is True
    by_id = {r["id"]: r for r in weird_meta["results"]}
    assert by_id["ok-1"]["provider_used"] == "gemini"
    assert by_id["ok-1"]["verdict"] == ""
    assert by_id["ok-1"]["pitch"] == ""
    assert by_id["null-txt"]["provider_used"] == "gemini"
    assert by_id["null-txt"]["verdict"] == ""
    for fb_id in ("omit-2", "bad-bool", "bad-inf", "bad-big"):
        assert by_id[fb_id]["provider_used"] == "heuristic"
        assert by_id[fb_id]["used_fallback"] is True

    # 4. Bounded concurrency (Semaphore <= 4) across 75 opportunities (5 batches of 15)
    many_opps = [{"id": f"m-{i}", "title": "Dev", "description": "Python"} for i in range(75)]
    in_flight = 0
    max_in_flight = 0

    class SlowFakeClient:
        def __init__(self, *args, timeout=None, **kwargs):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None, **kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                await asyncio.sleep(0.02)
                prompt = json["contents"][0]["parts"][0]["text"]
                items = __import__("json").loads(
                    prompt.split("--- LISTE DES OPPORTUNITÉS ---\n", 1)[1].split("\n\nPour CHAQUE élément", 1)[0]
                )
                return _gemini_text_response(__import__("json").dumps([{"id": it["id"], "score": 80, "verdict": "OK", "pitch": "Go"} for it in items]))
            finally:
                in_flight -= 1

    monkeypatch.setattr("jobby.ai_analyzer.httpx.AsyncClient", SlowFakeClient)
    sem_res = await rank_opportunities_by_ideal_brief_with_meta("Dev Python", many_opps)
    assert len(sem_res["results"]) == 75
    assert max_in_flight <= 4
