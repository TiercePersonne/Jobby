"""FastAPI server providing REST API and PWA interface for Jobby."""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
import secrets
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.responses import Response

from jobby.ai_analyzer import analyze_post_heuristic, analyze_post_with_llm, detect_remote_policy
from jobby.config import JobbyConfig, load_config, save_config
from jobby.engine import DeduplicationEngine, ScoringEngine
from jobby.models import OpportunityType, SearchParams
from jobby.providers import registry
from jobby.reporter import Reporter
from jobby.storage import Storage

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(title="Jobby API", version="0.1.0")


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    auth_user = os.environ.get("JOBBY_AUTH_USER")
    auth_pass = os.environ.get("JOBBY_AUTH_PASSWORD")
    if not auth_user or not auth_pass:
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return Response(
            content="Authentification requise pour accéder à Jobby",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Jobby Pro"'},
        )

    try:
        encoded = auth_header.split(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
        valid_user = secrets.compare_digest(username, auth_user)
        valid_pass = secrets.compare_digest(password, auth_pass)
        if not (valid_user and valid_pass):
            raise ValueError("Identifiants incorrects")
    except Exception:
        return Response(
            content="Identifiants invalides",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Jobby Pro"'},
        )

    return await call_next(request)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class SearchRequest(BaseModel):
    keywords: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    remote_only: bool = False
    min_salary: Optional[int] = None
    max_results: int = 30
    sources: Optional[List[str]] = None
    include_posts: bool = True
    include_offers: bool = True


class StatusUpdateRequest(BaseModel):
    status: str


class AITestRequest(BaseModel):
    provider: Optional[str] = "auto"
    api_key: Optional[str] = None
    ollama_url: Optional[str] = "http://localhost:11434"
    sample_text: Optional[str] = None


@app.get("/manifest.json")
def get_manifest():
    manifest_path = STATIC_DIR / "manifest.json"
    return FileResponse(manifest_path, media_type="application/manifest+json")


@app.get("/sw.js")
def get_service_worker():
    sw_path = STATIC_DIR / "sw.js"
    return FileResponse(sw_path, media_type="application/javascript")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    config = load_config()
    return templates.TemplateResponse(request=request, name="index.html", context={"config": config})


@app.get("/api/config")
def get_config() -> JobbyConfig:
    return load_config()


@app.post("/api/config")
def update_config(config: JobbyConfig) -> Dict[str, Any]:
    save_config(config)
    return {"status": "saved", "config": config}


@app.get("/api/providers")
def get_providers() -> List[Dict[str, Any]]:
    return registry.list_providers()


@app.get("/api/stats")
def get_stats() -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    return storage.get_stats()


@app.get("/api/auth/status")
def auth_status() -> Dict[str, Any]:
    config = load_config()
    return {
        "linkedin_connected": bool(config.linkedin_li_at),
        "wttj_connected": bool(config.wttj_session_cookie or config.wttj_auth_token),
    }


@app.post("/api/auth/login-wttj")
def trigger_wttj_login() -> Dict[str, Any]:
    import threading
    from jobby.auth import AuthManager
    auth = AuthManager()
    threading.Thread(target=auth.login_wttj_interactive, daemon=True).start()
    return {"status": "started", "message": "Le navigateur de connexion WTTJ (Google/Gmail SSO) a été lancé."}


@app.post("/api/auth/login-linkedin")
def trigger_linkedin_login() -> Dict[str, Any]:
    import threading
    from jobby.auth import AuthManager
    auth = AuthManager()
    threading.Thread(target=auth.login_linkedin_interactive, daemon=True).start()
    return {"status": "started", "message": "Le navigateur de connexion LinkedIn a été lancé."}


@app.post("/api/ai/test")
async def test_ai_connection(req: AITestRequest) -> Dict[str, Any]:
    config = load_config()
    provider = req.provider or config.ai_provider or "auto"
    api_key = req.api_key or config.ai_api_key
    ollama_url = req.ollama_url or config.ai_ollama_url
    sample = req.sample_text or "Je recherche une alternance de 12 mois en Data / Python dès septembre. Mon CV est en PJ."

    result = await analyze_post_with_llm(
        commentary=sample,
        author_name="Candidat Test",
        author_title="Étudiant Master",
        provider=provider,
        api_key=api_key,
        ollama_url=ollama_url,
    )
    return {
        "status": "success",
        "provider": result.get("provider", provider),
        "analysis": result,
    }


@app.post("/api/ai/analyze-all")
async def analyze_all_opportunities() -> Dict[str, Any]:
    """Re-analyzes and normalizes remote_policy and post_intent for all stored opportunities."""
    config = load_config()
    storage = Storage(config.database_path)
    opps = storage.list_opportunities(limit=500)

    updated_count = 0
    for opp in opps:
        modified = False
        # Normalize remote policy
        new_remote = detect_remote_policy(opp.title, opp.description, opp.location, opp.remote_policy)
        if new_remote != opp.remote_policy:
            opp.remote_policy = new_remote
            modified = True

        # Classify posts
        if opp.opportunity_type == OpportunityType.POST:
            if not opp.post_intent or not opp.ai_summary or opp.post_intent == "post":
                analysis = analyze_post_heuristic(opp.description, opp.author_name or "", opp.author_title or "")
                opp.post_intent = analysis.get("intent")
                opp.ai_summary = analysis.get("summary")
                if not opp.remote_policy or opp.remote_policy == "unknown":
                    opp.remote_policy = analysis.get("remote_policy")
                modified = True

        if modified:
            storage.upsert_opportunity(opp)
            updated_count += 1

    return {"status": "completed", "analyzed_count": len(opps), "updated_count": updated_count}


@app.get("/api/opportunities")
def list_opportunities(
    status: Optional[str] = None,
    source: Optional[str] = None,
    min_score: Optional[float] = None,
    opp_type: Optional[OpportunityType] = None,
    limit: int = 60,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    config = load_config()
    storage = Storage(config.database_path)
    opps = storage.list_opportunities(
        source=source,
        status=status,
        min_score=min_score,
        opp_type=opp_type,
        limit=limit,
        offset=offset,
    )
    return [o.model_dump() for o in opps]


@app.patch("/api/opportunities/{opp_id}/status")
def set_status(opp_id: str, body: StatusUpdateRequest) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    valid_statuses = ["new", "saved", "favorite", "kept", "applied", "interview", "archived", "rejected"]
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Status must be one of {valid_statuses}")

    success = storage.update_status(opp_id, body.status)
    if not success:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return {"status": "updated", "id": opp_id, "new_status": body.status}


@app.post("/api/search")
async def trigger_search(req: SearchRequest) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)

    # If min_salary is provided, update config temporarily for scoring
    if req.min_salary:
        config.min_salary = req.min_salary

    scoring = ScoringEngine(config)

    params = SearchParams(
        keywords=req.keywords if req.keywords else config.target_roles,
        location=req.location or (config.preferred_locations[0] if config.preferred_locations else "France"),
        remote_only=req.remote_only,
        max_results=req.max_results,
        include_posts=req.include_posts,
        include_offers=req.include_offers,
    )

    # Concurrent search across providers
    raw_results = await registry.search_all(params, source_names=req.sources)

    # Deduplicate & Sort by scraping date (most recent first)
    deduped = DeduplicationEngine.deduplicate(raw_results)
    deduped.sort(key=lambda x: x.discovered_at, reverse=True)

    # Save to SQLite & evaluate intelligence
    new_count = 0
    for opp in deduped:
        scoring.score_opportunity(opp)
        is_new, _ = storage.upsert_opportunity(opp)
        if is_new:
            new_count += 1

    # Update latest digest
    query_desc = f"{', '.join(params.keywords)} ({params.location})"
    Reporter.generate_markdown_digest(deduped, query_desc=query_desc, output_path=config.digest_output_path)

    return {
        "total_found": len(deduped),
        "new_count": new_count,
        "opportunities": [o.model_dump() for o in deduped],
    }
