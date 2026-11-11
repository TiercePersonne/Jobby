"""FastAPI server providing REST API and PWA interface for Jobby."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

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
    valid_statuses = ["new", "saved", "applied", "interview", "archived", "rejected"]
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

    # Deduplicate & Score
    deduped = DeduplicationEngine.deduplicate(raw_results)
    scored = scoring.evaluate_all(deduped)

    # Save to SQLite
    new_count = 0
    for opp in scored:
        is_new, _ = storage.upsert_opportunity(opp)
        if is_new:
            new_count += 1

    # Update latest digest
    query_desc = f"{', '.join(params.keywords)} ({params.location})"
    Reporter.generate_markdown_digest(scored, query_desc=query_desc, output_path=config.digest_output_path)

    return {
        "total_found": len(scored),
        "new_count": new_count,
        "opportunities": [o.model_dump() for o in scored],
    }
