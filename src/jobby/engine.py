"""Deduplication and Scoring Engine for Jobby."""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Tuple

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

    def score_opportunity(self, opp: JobOpportunity) -> Tuple[float, List[str]]:
        score = 0.0
        reasons: List[str] = []

        title_cleaned = clean_text(opp.title)
        desc_cleaned = clean_text(opp.description)
        full_text = f"{title_cleaned} {desc_cleaned}"

        # 1. Target roles / Métier match (up to 55 pts)
        matched_in_post = False
        if opp.opportunity_type == OpportunityType.POST:
            # For social posts, strictly evaluate the post body itself,
            # completely ignoring the author's title, current job or headline.
            for role in self.config.target_roles:
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
            for role in self.config.target_roles:
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
