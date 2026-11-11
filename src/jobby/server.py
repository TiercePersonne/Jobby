"""FastAPI server providing REST API and PWA interface for Jobby."""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
import secrets
from typing import Any, Dict, List, Optional
import uuid

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.responses import Response

from jobby.ai_analyzer import (
    analyze_post_heuristic,
    analyze_post_with_llm,
    detect_remote_policy,
    evaluate_candidate_match,
    generate_tailored_cover_letter,
    rank_opportunities_by_ideal_brief,
)
from jobby.config import JobbyConfig, load_config, save_config
from jobby.cv_parser import extract_text_from_cv
from jobby.engine import DeduplicationEngine, ScoringEngine
from jobby.models import Application, ApplicationStatus, MatchEvaluation, OpportunityType, SearchParams, User, UserProfile
from jobby.providers import registry
from jobby.reporter import Reporter
from jobby.storage import Storage
from jobby.user_auth import create_session_token, delete_session_token, hash_password, validate_session_token, verify_password

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(title="Jobby API", version="0.1.0")


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    # Whitelist health checks and public PWA assets
    if request.url.path in ["/manifest.json", "/sw.js", "/health", "/favicon.ico"] or request.url.path.startswith("/static/"):
        return await call_next(request)

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


@app.get("/health")
def health_check():
    return {"status": "ok"}


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
def set_status(opp_id: str, body: StatusUpdateRequest, request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    valid_statuses = ["new", "saved", "favorite", "kept", "applied", "interview", "archived", "rejected"]
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Status must be one of {valid_statuses}")

    success = storage.update_status(opp_id, body.status)
    if not success:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    # Synchronize with embedded application kanban pipeline
    user = get_current_user_or_default(request, storage)
    if body.status in ["saved", "kept", "favorite"]:
        storage.sync_opportunity_to_application(opp_id, user.id, ApplicationStatus.SAVED)
    elif body.status in ["archived", "rejected"]:
        storage.sync_opportunity_to_application(opp_id, user.id, ApplicationStatus.REJECTED)
    elif body.status == "applied":
        storage.sync_opportunity_to_application(opp_id, user.id, ApplicationStatus.APPLIED)
    elif body.status == "interview":
        storage.sync_opportunity_to_application(opp_id, user.id, ApplicationStatus.INTERVIEW)
    elif body.status == "new":
        storage.delete_application_for_opportunity(opp_id, user.id)

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
        scoring.score_opportunity(opp, active_keywords=params.keywords)
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


# ===================================================================
# User Authentication & Session APIs
# ===================================================================

class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


def get_current_user_or_default(request: Request, storage: Storage) -> User:
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    if not token:
        token = request.cookies.get("jobby_session")
    if token:
        sess = validate_session_token(token)
        if sess:
            u = storage.get_user_by_id(sess["user_id"])
            if u:
                return u

    # Default user for single-tenant / frictionless mode
    u = storage.get_user_by_email("user@jobby.local")
    if not u:
        u = User(
            id="usr_" + uuid.uuid4().hex[:12],
            email="user@jobby.local",
            full_name="Mon Profil Candidat",
            password_hash=hash_password("default123"),
        )
        storage.create_user(u)
    return u


@app.post("/api/auth/register")
def register(body: RegisterRequest) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    
    existing = storage.get_user_by_email(body.email)
    if existing:
        raise HTTPException(status_code=400, detail="Un compte existe déjà avec cette adresse email.")

    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Le mot de passe doit comporter au moins 6 caractères.")

    user = User(
        id="usr_" + uuid.uuid4().hex[:12],
        email=body.email.strip().lower(),
        full_name=body.full_name.strip() or body.email.split("@")[0].capitalize(),
        password_hash=hash_password(body.password),
    )
    storage.create_user(user)

    token = create_session_token(user.id, user.email, user.full_name)
    response = JSONResponse(content={
        "status": "success",
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name},
        "token": token,
    })
    response.set_cookie(key="jobby_session", value=token, max_age=3600 * 24 * 30, httponly=True)
    return response


@app.post("/api/auth/login")
def login(body: LoginRequest) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)

    user = storage.get_user_by_email(body.email)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")

    token = create_session_token(user.id, user.email, user.full_name)
    response = JSONResponse(content={
        "status": "success",
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name},
        "token": token,
    })
    response.set_cookie(key="jobby_session", value=token, max_age=3600 * 24 * 30, httponly=True)
    return response


@app.get("/api/auth/me")
def get_me(request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)
    is_authenticated = user.email != "user@jobby.local"
    return {
        "authenticated": is_authenticated,
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name},
    }


@app.post("/api/auth/logout")
def logout(request: Request) -> Dict[str, Any]:
    token = request.cookies.get("jobby_session")
    if token:
        delete_session_token(token)
    response = JSONResponse(content={"status": "logged_out"})
    response.delete_cookie("jobby_session")
    return response


# ===================================================================
# Candidate Profile, CV Upload & Tone APIs
# ===================================================================

class ProfileUpdateRequest(BaseModel):
    portfolio_url: Optional[str] = None
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    bio: Optional[str] = None
    writing_sample: Optional[str] = None
    writing_tone: Optional[str] = None
    target_roles: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    min_salary: Optional[int] = None
    remote_preference: Optional[str] = None


@app.get("/api/profile")
def get_user_profile(request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    profile = storage.get_profile(user.id)
    if not profile:
        profile = UserProfile(
            user_id=user.id,
            target_roles=config.target_roles,
            skills=config.skills_keywords,
            min_salary=config.min_salary,
            remote_preference="full" if config.remote_only else None,
        )
        storage.save_profile(profile)

    return {
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name},
        "profile": profile.model_dump(),
        "has_cv": bool(profile.cv_text),
    }


@app.post("/api/profile")
def update_user_profile(body: ProfileUpdateRequest, request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    existing = storage.get_profile(user.id)
    cv_filename = existing.cv_filename if existing else None
    cv_text = existing.cv_text if existing else None

    profile = UserProfile(
        user_id=user.id,
        cv_filename=cv_filename,
        cv_text=cv_text,
        portfolio_url=body.portfolio_url,
        github_url=body.github_url,
        linkedin_url=body.linkedin_url,
        bio=body.bio,
        writing_sample=body.writing_sample,
        writing_tone=body.writing_tone or "naturel, direct, enthousiaste et percutant",
        target_roles=body.target_roles if body.target_roles else (config.target_roles or []),
        skills=body.skills if body.skills else (config.skills_keywords or []),
        min_salary=body.min_salary,
        remote_preference=body.remote_preference,
    )
    storage.save_profile(profile)
    return {"status": "saved", "profile": profile.model_dump()}


@app.post("/api/profile/upload-cv")
async def upload_cv(file: UploadFile = File(...), request: Request = None) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    # Extract text from PDF / TXT
    extracted_text = extract_text_from_cv(content, file.filename)
    if not extracted_text:
        raise HTTPException(
            status_code=400,
            detail="Impossible d'extraire le texte de ce fichier. Assurez-vous d'envoyer un PDF avec du texte sélectionnable ou un fichier .txt.",
        )

    profile = storage.get_profile(user.id)
    if not profile:
        profile = UserProfile(user_id=user.id)

    profile.cv_filename = file.filename
    profile.cv_text = extracted_text
    storage.save_profile(profile)

    return {
        "status": "success",
        "filename": file.filename,
        "characters_extracted": len(extracted_text),
        "preview": extracted_text[:300] + ("..." if len(extracted_text) > 300 else ""),
    }


# ===================================================================
# Embedded Application Tracking Pipeline (Kanban) APIs
# ===================================================================

class ApplicationCreateRequest(BaseModel):
    opportunity_id: str
    status: str = "saved"
    notes: Optional[str] = None


class ApplicationStatusRequest(BaseModel):
    status: str


class ApplicationNotesRequest(BaseModel):
    notes: str


@app.get("/api/applications")
def list_applications(request: Request) -> List[Dict[str, Any]]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    apps = storage.get_applications(user.id)
    return [a.model_dump() for a in apps]


@app.post("/api/applications")
def add_application(body: ApplicationCreateRequest, request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    opp = storage.get_opportunity(body.opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable.")

    app_id = f"app_{user.id}_{body.opportunity_id}"
    app = Application(
        id=app_id,
        user_id=user.id,
        opportunity_id=body.opportunity_id,
        status=ApplicationStatus(body.status) if body.status in ApplicationStatus._value2member_map_ else ApplicationStatus.SAVED,
        notes=body.notes,
        contact_name=opp.author_name,
    )
    storage.add_application(app)

    # Optional N8N webhook trigger
    n8n_url = os.environ.get("JOBBY_N8N_WEBHOOK_URL")
    if n8n_url:
        try:
            import httpx
            httpx.post(
                n8n_url,
                json={"event": "application_created", "application": app.model_dump(mode="json"), "opportunity": opp.model_dump(mode="json")},
                timeout=2.0,
            )
        except Exception:
            pass

    return {"status": "created", "application": app.model_dump()}


@app.patch("/api/applications/{app_id}/status")
def update_app_status(app_id: str, body: ApplicationStatusRequest, request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    if body.status not in ApplicationStatus._value2member_map_:
        raise HTTPException(status_code=400, detail=f"Statut invalide. Choix : {list(ApplicationStatus._value2member_map_.keys())}")

    success = storage.update_application_status(app_id, body.status, user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Candidature introuvable.")

    # Two-way sync: update opportunity status to reflect change in kanban
    app = storage.get_application(app_id, user.id)
    if app and app.opportunity_id:
        if body.status == ApplicationStatus.SAVED.value:
            storage.update_status(app.opportunity_id, "kept")
        elif body.status == ApplicationStatus.REJECTED.value:
            storage.update_status(app.opportunity_id, "archived")
        elif body.status in [ApplicationStatus.APPLIED.value, ApplicationStatus.INTERVIEW.value]:
            storage.update_status(app.opportunity_id, body.status)

    return {"status": "updated", "id": app_id, "new_status": body.status}


@app.patch("/api/applications/{app_id}/notes")
def update_app_notes(app_id: str, body: ApplicationNotesRequest, request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    success = storage.update_application_notes(app_id, body.notes, user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Candidature introuvable.")
    return {"status": "updated", "id": app_id}


@app.delete("/api/applications/{app_id}")
def delete_app(app_id: str, request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    app = storage.get_application(app_id, user.id)
    opp_id = app.opportunity_id if app else None

    success = storage.delete_application(app_id, user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Candidature introuvable.")
    if opp_id:
        storage.update_status(opp_id, "new")

    return {"status": "deleted", "id": app_id}


# ===================================================================
# AI Matching, Pitch & Tailored Cover Letter (Google AI Studio)
# ===================================================================

@app.post("/api/ai/match/{opportunity_id}")
async def match_cv_with_opportunity(opportunity_id: str, request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    opp = storage.get_opportunity(opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable.")

    profile = storage.get_profile(user.id)
    if not profile:
        profile = UserProfile(user_id=user.id, target_roles=config.target_roles, skills=config.skills_keywords)

    # Check cached evaluation
    cached = storage.get_match_evaluation(user.id, opportunity_id)
    if cached and cached.score > 0:
        return cached.model_dump()

    # Call AI Evaluator (Gemini with candidate CV & skills vs Offer)
    eval_result = await evaluate_candidate_match(
        profile.model_dump(),
        opp.model_dump(),
        api_key=config.ai_api_key or os.environ.get("GEMINI_API_KEY"),
        model=config.ai_model,
    )

    match = MatchEvaluation(
        user_id=user.id,
        opportunity_id=opportunity_id,
        score=eval_result["score"],
        verdict=eval_result["verdict"],
        strengths=eval_result.get("strengths", []),
        weaknesses=eval_result.get("weaknesses", []),
        pitch=eval_result.get("pitch", ""),
    )
    storage.save_match_evaluation(match)
    return match.model_dump()


@app.post("/api/ai/cover-letter/{opportunity_id}")
async def generate_cover_letter_for_opportunity(opportunity_id: str, request: Request) -> Dict[str, Any]:
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    opp = storage.get_opportunity(opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable.")

    profile = storage.get_profile(user.id)
    if not profile:
        profile = UserProfile(user_id=user.id, target_roles=config.target_roles, skills=config.skills_keywords)

    # Check existing cover letter in cached match
    cached = storage.get_match_evaluation(user.id, opportunity_id)
    if cached and cached.cover_letter:
        return {"opportunity_id": opportunity_id, "cover_letter": cached.cover_letter}

    letter = await generate_tailored_cover_letter(
        profile.model_dump(),
        opp.model_dump(),
        api_key=config.ai_api_key or os.environ.get("GEMINI_API_KEY"),
        model=config.ai_model,
    )

    if cached:
        cached.cover_letter = letter
        storage.save_match_evaluation(cached)
    else:
        new_match = MatchEvaluation(
            user_id=user.id,
            opportunity_id=opportunity_id,
            score=75.0,
            verdict="Généré",
            cover_letter=letter,
        )
        storage.save_match_evaluation(new_match)

    return {"opportunity_id": opportunity_id, "cover_letter": letter}


class IdealBriefRankRequest(BaseModel):
    brief: str
    limit: Optional[int] = 60


@app.post("/api/ai/rank-by-brief")
async def rank_by_ideal_brief(req: IdealBriefRankRequest, request: Request) -> Dict[str, Any]:
    """
    '3ème Voie' : Semantic matching and ranking of all opportunities against a candidate's
    verbose 'Ideal Job' brief using Google AI Studio Gemini.
    """
    config = load_config()
    storage = Storage(config.database_path)
    user = get_current_user_or_default(request, storage)

    brief_text = req.brief.strip()
    if not brief_text:
        raise HTTPException(status_code=400, detail="Veuillez fournir la description de votre job idéal.")

    # Fetch active opportunities from storage (excluding archived)
    all_opps = storage.list_opportunities(limit=req.limit or 60)
    active_opps = [o for o in all_opps if o.status != "archived"]

    if not active_opps:
        return {
            "status": "success",
            "total_evaluated": 0,
            "updated_count": 0,
            "results": [],
            "message": "Aucune opportunité active en base à évaluer.",
        }

    profile = storage.get_profile(user.id)
    profile_dict = profile.model_dump() if profile else {}

    # Persist the ideal job brief in user profile
    if profile:
        profile.raw_metadata = profile.raw_metadata or {}
        profile.raw_metadata["ideal_job_brief"] = brief_text
        storage.save_profile(profile)

    api_key = config.ai_api_key or os.environ.get("GEMINI_API_KEY")
    model = config.ai_model

    ranked_results = await rank_opportunities_by_ideal_brief(
        brief=brief_text,
        opportunities=active_opps,
        profile_data=profile_dict,
        api_key=api_key,
        model=model,
    )

    # Update opportunities in SQLite
    ranked_map = {r["id"]: r for r in ranked_results}
    updated_count = 0
    for opp in active_opps:
        if opp.id in ranked_map:
            res = ranked_map[opp.id]
            opp.score = float(res.get("score", 50.0))
            verdict = res.get("verdict")
            if verdict:
                opp.score_reasons = [verdict]
            opp.raw_metadata = opp.raw_metadata or {}
            opp.raw_metadata["ai_match"] = res
            storage.upsert_opportunity(opp)
            updated_count += 1

    return {
        "status": "success",
        "total_evaluated": len(active_opps),
        "updated_count": updated_count,
        "results": ranked_results,
    }


# ===================================================================
# N8N & Webhooks Integration APIs
# ===================================================================

@app.post("/api/webhooks/trigger-search")
async def webhook_trigger_search(request: Request) -> Dict[str, Any]:
    """Endpoint called by N8N cron or webhook to trigger autonomous hunting."""
    secret = os.environ.get("JOBBY_WEBHOOK_SECRET")
    if secret:
        token = request.headers.get("X-Webhook-Secret") or request.query_params.get("secret")
        if token != secret:
            raise HTTPException(status_code=403, detail="Clé secrète de webhook invalide.")

    config = load_config()
    storage = Storage(config.database_path)
    scoring = ScoringEngine(config)

    params = SearchParams(
        keywords=config.target_roles,
        location=config.preferred_locations[0] if config.preferred_locations else "France",
        remote_only=config.remote_only,
        max_results=30,
        include_posts=config.linkedin_include_posts,
        include_offers=config.linkedin_include_offers,
    )

    raw_results = await registry.search_all(params)
    deduped = DeduplicationEngine.deduplicate(raw_results)
    deduped.sort(key=lambda x: x.discovered_at, reverse=True)

    new_opps = []
    for opp in deduped:
        scoring.score_opportunity(opp)
        is_new, _ = storage.upsert_opportunity(opp)
        if is_new:
            new_opps.append(opp)

    # Forward to N8N webhook if configured
    n8n_url = os.environ.get("JOBBY_N8N_WEBHOOK_URL")
    if n8n_url and new_opps:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                await client.post(
                    n8n_url,
                    json={
                        "event": "new_opportunities_found",
                        "count": len(new_opps),
                        "items": [o.model_dump(mode="json") for o in new_opps[:10]],
                    },
                )
        except Exception as e:
            logger.warning(f"Failed to post to N8N webhook: {e}")

    return {
        "status": "success",
        "total_scraped": len(deduped),
        "new_opportunities": len(new_opps),
    }

