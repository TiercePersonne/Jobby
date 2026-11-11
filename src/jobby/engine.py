"""Deduplication and Scoring Engine for Jobby."""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Tuple

from jobby.ai_analyzer import analyze_post_heuristic, detect_remote_policy
from jobby.config import JobbyConfig
from jobby.models import JobOpportunity, OpportunityType

logger = logging.getLogger(__name__)


def clean_text(text: str) -> str:
    """Normalize text by removing punctuation and lowercasing."""
    if not text:
        return ""
    # Remove H/F, F/H, CDI, (H/F), etc.
    text = re.sub(r"\(?[hf]/[hf]\)?", "", text, flags=re.I)
    cleaned = re.sub(r"[^\w\s]", " ", text)
    return " ".join(cleaned.lower().split())


class DeduplicationEngine:
    """Deduplicates opportunities across multiple sources."""

    @staticmethod
    def deduplicate(opportunities: List[JobOpportunity]) -> List[JobOpportunity]:
        merged_by_key: Dict[str, JobOpportunity] = {}

        for opp in opportunities:
            key = opp.dedup_key
            if not key or key == "unknown:":
                key = opp.id

            if key not in merged_by_key:
                merged_by_key[key] = opp
            else:
                existing = merged_by_key[key]
                # Merge tags
                combined_tags = list(set(existing.tags + opp.tags))
                existing.tags = combined_tags

                # Keep richer description
                if len(opp.description) > len(existing.description):
                    existing.description = opp.description

                # Track multiple sources in metadata
                cross_sources: List[str] = existing.raw_metadata.get("cross_sources", [existing.source])
                if opp.source not in cross_sources:
                    cross_sources.append(opp.source)
                existing.raw_metadata["cross_sources"] = cross_sources

                # Prefer social post type if one is a post
                if opp.opportunity_type == OpportunityType.POST and existing.opportunity_type != OpportunityType.POST:
                    existing.opportunity_type = OpportunityType.POST
                    existing.author_name = opp.author_name
                    existing.author_title = opp.author_title
                    existing.author_profile_url = opp.author_profile_url

        return list(merged_by_key.values())


class ScoringEngine:
    """Evaluates and scores job opportunities against candidate preferences."""

    def __init__(self, config: JobbyConfig):
        self.config = config

    def score_opportunity(
        self,
        opp: JobOpportunity,
        active_keywords: Optional[List[str]] = None,
    ) -> Tuple[float, List[str]]:
        score = 0.0
        reasons: List[str] = []

        # Auto-detect remote policy if missing or unknown
        if not opp.remote_policy or opp.remote_policy == "unknown":
            opp.remote_policy = detect_remote_policy(opp.title, opp.description, opp.location)

        # Auto-analyze post intent & summary for social posts
        if opp.opportunity_type == OpportunityType.POST and (not opp.post_intent or not opp.ai_summary):
            analysis = analyze_post_heuristic(opp.description, opp.author_name or "", opp.author_title or "")
            opp.post_intent = analysis.get("intent")
            opp.ai_summary = analysis.get("summary")
            if not opp.remote_policy or opp.remote_policy == "unknown":
                opp.remote_policy = analysis.get("remote_policy")

        title_cleaned = clean_text(opp.title)
        desc_cleaned = clean_text(opp.description)
        full_text = f"{title_cleaned} {desc_cleaned}"

        # Combine configured target roles with active search query keywords
        roles_to_match = list(self.config.target_roles or [])
        if active_keywords:
            for kw in active_keywords:
                if kw and kw not in roles_to_match:
                    roles_to_match.append(kw)

        # 1. Target roles / Métier match (up to 55 pts)
        matched_in_post = False
        if opp.opportunity_type == OpportunityType.POST:
            # For social posts, strictly evaluate the post body itself,
            # completely ignoring the author's title, current job or headline.
            for role in roles_to_match:
                norm_role = clean_text(role)
                if norm_role and norm_role in desc_cleaned:
                    score += 55.0
                    reasons.append(f"Poste ciblé '{role}' cité dans le post (+55)")
                    matched_in_post = True
                    break
            if not matched_in_post:
                score -= 30.0
                reasons.append("Poste ciblé absent du texte du post (-30)")
        else:
            for role in roles_to_match:
                norm_role = clean_text(role)
                if norm_role and (norm_role in title_cleaned or norm_role in full_text):
                    score += 55.0
                    reasons.append(f"Poste ciblé '{role}' (+55)")
                    break

        # 2. Location / Remote preferences (up to 30 pts)
        location_text = clean_text(opp.location or "")
        remote_matched = False

        if opp.remote_policy == "full" or "remote" in title_cleaned or "télétravail" in full_text:
            remote_matched = True

        if self.config.remote_only:
            if remote_matched:
                score += 30.0
                reasons.append("100% Télétravail confirmé (+30)")
            else:
                score -= 30.0
                reasons.append("Non 100% remote alors que requis (-30)")
        else:
            loc_matched = False
            for loc in self.config.preferred_locations:
                if clean_text(loc) in location_text or clean_text(loc) in full_text:
                    score += 25.0
                    reasons.append(f"Localisation '{loc}' correspondante (+25)")
                    loc_matched = True
                    break
            if not loc_matched and remote_matched:
                score += 20.0
                reasons.append("Option télétravail détectée (+20)")

        # 3. Social post / Recruiter direct contact bonus (+15 pts)
        if opp.opportunity_type == OpportunityType.POST and matched_in_post:
            score += 15.0
            reasons.append("Post direct de recruteur/réseau (+15)")

        # 4. Negative keywords penalty (-40 pts)
        found_negatives: List[str] = []
        for neg in self.config.negative_keywords:
            pattern = r"\b" + re.escape(neg.lower()) + r"\b"
            if re.search(pattern, title_cleaned):
                found_negatives.append(neg)

        # Flag alternance or stage in tags
        alt_pattern = r"\b(alternan(?:ce|t|te|ts|tes)|apprentissage|contrat\s+pro|contrat\s+de\s+professionnalisation|stage|stagiaire|stagiaires|internship|intern)\b"
        contract_info = opp.raw_metadata.get("contract_type", "") if isinstance(opp.raw_metadata, dict) else ""
        if re.search(alt_pattern, title_cleaned) or re.search(alt_pattern, str(contract_info).lower()):
            if "alternance" not in opp.tags:
                opp.tags.append("alternance")

        if found_negatives:
            score -= 40.0
            reasons.append(f"Mots indésirables détectés ({', '.join(found_negatives)}) (-40)")

        # Bound score between 0 and 100
        final_score = max(0.0, min(100.0, round(score, 1)))
        return final_score, reasons

    def evaluate_all(self, opportunities: List[JobOpportunity]) -> List[JobOpportunity]:
        """Scores each opportunity in-place and sorts by score descending."""
        for opp in opportunities:
            score, reasons = self.score_opportunity(opp)
            opp.score = score
            opp.score_reasons = reasons

        return sorted(opportunities, key=lambda x: (x.score or 0.0), reverse=True)
