"""Configuration loader for Jobby."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional
import yaml
from pydantic import BaseModel, Field


class JobbyConfig(BaseModel):
    # Candidate profile for scoring and relevance
    target_roles: List[str] = Field(
        default_factory=lambda: [
            "Brand Designer",
            "Graphiste",
            "Graphic Designer",
            "Art Director",
            "Directeur Artistique",
        ]
    )
    preferred_locations: List[str] = Field(default_factory=lambda: ["France", "Paris", "Remote"])
    remote_only: bool = Field(default=False)
    min_salary: Optional[int] = Field(default=None)
    skills_keywords: List[str] = Field(
        default_factory=lambda: [
            "Branding",
            "Figma",
            "Identité Visuelle",
            "Direction Artistique",
            "Adobe Illustrator",
            "Photoshop",
            "InDesign",
            "Typographie",
            "Design System",
        ]
    )
    negative_keywords: List[str] = Field(default_factory=lambda: ["Stage", "Alternance", "Junior", "PHP", "COBOL", "Développeur"])

    # LinkedIn authentication & options
    linkedin_li_at: Optional[str] = Field(default=None, description="LinkedIn session cookie (li_at) for authenticated post search")
    linkedin_include_posts: bool = Field(default=True)
    linkedin_include_offers: bool = Field(default=True)

    # Welcome to the Jungle authentication
    wttj_session_cookie: Optional[str] = Field(default=None, description="WTTJ session cookie (Google/Gmail login)")
    wttj_auth_token: Optional[str] = Field(default=None, description="WTTJ auth token")

    # AI & Post intelligence settings
    ai_provider: str = Field(default="gemini", description="AI Provider: auto, gemini, openai, ollama, or heuristic")
    ai_api_key: Optional[str] = Field(
        default=None,
        description="API Key for Gemini, OpenAI or other LLM provider"
    )
    ai_ollama_url: Optional[str] = Field(default="http://localhost:11434", description="Ollama API base URL")
    ai_model: Optional[str] = Field(default="gemini-flash-latest", description="Custom model name")
    filter_candidate_posts: bool = Field(default=False, description="Filter out posts where candidates seek alternances or jobs")

    # Automated scheduled hunting (12:00 & 17:00 Europe/Paris)
    auto_search_enabled: bool = Field(default=True, description="Enable automated scheduled hunting")
    auto_search_times: List[str] = Field(default_factory=lambda: ["12:00", "17:00"], description="Scheduled hunting times (Europe/Paris)")
    auto_search_queries: List[str] = Field(
        default_factory=lambda: [
            "brand designer",
            "graphiste",
            "graphic designer",
            "art director",
            "directeur artistique",
        ],
        description="Bilingual target roles and queries searched sequentially",
    )

    # Storage & paths
    database_path: str = Field(default="_bmad-output/jobby.db")
    digest_output_path: str = Field(default="_bmad-output/latest_digest.md")


def load_config(config_path: str | Path | None = None) -> JobbyConfig:
    if config_path is None:
        config_path = os.environ.get("JOBBY_CONFIG_PATH", "_bmad-output/jobby_config.yaml")
    path = Path(config_path)
    data = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                data = loaded

    # Environment variables overrides
    db_env = os.environ.get("JOBBY_DATABASE_PATH")
    if db_env:
        data["database_path"] = db_env

    li_at_env = os.environ.get("LINKEDIN_LI_AT")
    if li_at_env:
        data["linkedin_li_at"] = li_at_env

    configured_provider = (data.get("ai_provider") or "").strip().lower()
    has_explicit_key = bool((data.get("ai_api_key") or "").strip())

    gemini_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if gemini_key and not has_explicit_key and configured_provider in ("", "auto", "gemini"):
        data["ai_api_key"] = gemini_key
        if not configured_provider:
            data["ai_provider"] = "gemini"
        has_explicit_key = True

    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if openai_key and not has_explicit_key and configured_provider in ("", "auto", "openai"):
        data["ai_api_key"] = openai_key
        if not configured_provider:
            data["ai_provider"] = "openai"

    return JobbyConfig(**data)


def save_config(config: JobbyConfig, config_path: str | Path | None = None) -> None:
    if config_path is None:
        config_path = os.environ.get("JOBBY_CONFIG_PATH", "_bmad-output/jobby_config.yaml")
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config.model_dump(), f, allow_unicode=True, sort_keys=False)
