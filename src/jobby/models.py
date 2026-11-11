"""Data models for Jobby."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field


def normalize_job_title(title: str) -> str:
    """Strip gender markers (H/F, F/H), brackets and extra punctuation."""
    if not title:
        return ""
    cleaned = re.sub(r"\(?[hf]/[hf]\)?", "", title, flags=re.I)
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    return "".join(cleaned.lower().split())


class OpportunityType(str, Enum):
    OFFER = "offer"          # Official job posting on a board/site
    POST = "post"            # Social post (recruiter, employee, founder)
    FREELANCE = "freelance"  # Mission / freelance opportunity
    UNKNOWN = "unknown"


class JobOpportunity(BaseModel):
    id: str = Field(default="", description="Unique identifier (auto-generated if empty)")
    source: str = Field(description="Origin source: linkedin, wttj, hellowork, glassdoor, jobbygather, etc.")
    opportunity_type: OpportunityType = Field(default=OpportunityType.OFFER)
    title: str = Field(description="Job title or concise subject line")
    company: Optional[str] = Field(default=None, description="Hiring company or organization")
    location: Optional[str] = Field(default=None, description="Location (City, Country, Remote)")
    remote_policy: Optional[str] = Field(default=None, description="Remote status: full, hybrid, on-site")
    salary_raw: Optional[str] = Field(default=None, description="Original salary text if mentioned")
    url: str = Field(description="Canonical URL to the offer or post")
    
    # Recruiter / Author details (especially valuable for social posts)
    author_name: Optional[str] = Field(default=None, description="Poster or recruiter name")
    author_profile_url: Optional[str] = Field(default=None, description="Poster profile link")
    author_title: Optional[str] = Field(default=None, description="Poster headline/job title")
    
    # Content & Timestamps
    description: str = Field(default="", description="Full description or post text body")
    published_at: Optional[datetime] = Field(default=None, description="Original publication datetime")
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Intelligence, AI & State
    post_intent: Optional[str] = Field(default=None, description="Intent: candidate_seeking_alternance, candidate_seeking_job, hiring_alternance, hiring_job, other")
    ai_summary: Optional[str] = Field(default=None, description="Concise 1-sentence AI summary of the post or job")
    score: Optional[float] = Field(default=None, ge=0.0, le=100.0, description="Relevance score (0-100)")
    score_reasons: List[str] = Field(default_factory=list, description="Explanations for the assigned score")
    tags: List[str] = Field(default_factory=list, description="Extracted keywords, tech stack, skills")
    status: str = Field(default="new", description="Lifecycle: new, saved, contacted, applied, archived")
    raw_metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary raw payload from provider")

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            self.id = self.compute_fingerprint()

    def compute_fingerprint(self) -> str:
        """Deterministic fingerprint based on canonical URL or normalized company+title."""
        if self.url and not self.url.startswith("urn:li:"):
            # Clean tracking parameters from URL
            clean_url = self.url.split("?")[0].strip().lower()
            return hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:16]
        
        # Fallback to normalized company + title
        norm_company = re.sub(r"\W+", "", (self.company or "").lower())
        norm_title = normalize_job_title(self.title)
        raw_seed = f"{self.source}:{norm_company}:{norm_title}"
        return hashlib.sha256(raw_seed.encode("utf-8")).hexdigest()[:16]

    @property
    def dedup_key(self) -> str:
        """Key used for cross-source deduplication (company + normalized title)."""
        norm_company = re.sub(r"\W+", "", (self.company or "unknown").lower())
        norm_title = normalize_job_title(self.title)
        return f"{norm_company}:{norm_title}"


class SearchParams(BaseModel):
    """Parameters passed to any provider during search."""
    keywords: List[str] = Field(default_factory=list, description="Keywords to match (e.g. ['python', 'backend'])")
    location: Optional[str] = Field(default=None, description="Target city, region, or country")
    remote_only: bool = Field(default=False, description="Filter only 100% remote positions")
    max_results: int = Field(default=25, ge=1, le=200, description="Max results per provider")
    posted_within_days: int = Field(default=7, ge=1, le=30, description="Freshness window in days")
    include_posts: bool = Field(default=True, description="Whether to include social/recruiter posts")
    include_offers: bool = Field(default=True, description="Whether to include official job board offers")
