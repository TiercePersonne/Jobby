"""Relevance and location filtering utilities for Jobby job providers."""

from __future__ import annotations

import contextvars
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


_CURRENT_AUDIT_SOURCE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_audit_source", default=None
)

# In-memory audit store for the latest search run (also persisted to SQLite by Storage)
_AUDIT_SOURCE_STATS: Dict[str, Dict[str, Any]] = {}
_AUDIT_REJECTED_OFFERS: Dict[str, Dict[str, Any]] = {}


def set_current_audit_source(source: Optional[str]) -> contextvars.Token[Optional[str]]:
    return _CURRENT_AUDIT_SOURCE.set(source.lower() if source else None)


def reset_current_audit_source(token: contextvars.Token[Optional[str]]) -> None:
    _CURRENT_AUDIT_SOURCE.reset(token)


def reset_search_audit(sources: Optional[Iterable[str]] = None) -> None:
    """Reset the in-memory audit counters for a new search session."""
    _AUDIT_REJECTED_OFFERS.clear()
    if sources:
        for src in sources:
            s_key = (src or "").lower().strip()
            if s_key:
                _AUDIT_SOURCE_STATS[s_key] = {
                    "source": s_key,
                    "scanned_count": 0,
                    "kept_count": 0,
                    "filtered_count": 0,
                    "status": "ok",
                    "note": "",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
    else:
        _AUDIT_SOURCE_STATS.clear()


def _ensure_source_stat(source: str) -> Dict[str, Any]:
    s_key = (source or "unknown").lower().strip()
    if s_key not in _AUDIT_SOURCE_STATS:
        _AUDIT_SOURCE_STATS[s_key] = {
            "source": s_key,
            "scanned_count": 0,
            "kept_count": 0,
            "filtered_count": 0,
            "status": "ok",
            "note": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    return _AUDIT_SOURCE_STATS[s_key]


def record_provider_audit_result(
    source: str,
    kept_count: int,
    status: str = "ok",
    note: str = "",
) -> None:
    """Record the final outcome for a provider after search completes."""
    stat = _ensure_source_stat(source)
    stat["kept_count"] = max(int(kept_count), 0)
    if stat["scanned_count"] < stat["kept_count"] + stat["filtered_count"]:
        stat["scanned_count"] = stat["kept_count"] + stat["filtered_count"]
    if status != "ok" or not stat.get("status") or stat.get("status") == "ok":
        stat["status"] = status
    if note:
        stat["note"] = note
    stat["updated_at"] = datetime.now(timezone.utc).isoformat()


def record_filtered_offer(
    source: Optional[str],
    title: str,
    reason: str,
    company: Optional[str] = None,
    location: Optional[str] = None,
    url: Optional[str] = None,
    description: str = "",
) -> None:
    """Record an offer rejected by relevance or location filtering."""
    active_source = (source or _CURRENT_AUDIT_SOURCE.get() or "").lower().strip()
    if not active_source or not title or not title.strip():
        return

    stat = _ensure_source_stat(active_source)
    dedup_raw = f"{active_source}|{title.strip().lower()}|{(company or '').strip().lower()}|{(location or '').strip().lower()}"
    item_id = "flt_" + hashlib.sha1(dedup_raw.encode("utf-8")).hexdigest()[:12]

    if item_id not in _AUDIT_REJECTED_OFFERS:
        stat["filtered_count"] += 1
        stat["scanned_count"] += 1
        _AUDIT_REJECTED_OFFERS[item_id] = {
            "id": item_id,
            "source": active_source,
            "title": title.strip(),
            "company": (company or "Entreprise").strip(),
            "location": (location or "Non précisée").strip(),
            "url": (url or "").strip(),
            "description": (description or "").strip()[:1500],
            "reject_reason": reason,
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        }


def get_search_audit_snapshot() -> Dict[str, Any]:
    """Return current in-memory audit stats and rejected offers."""
    return {
        "sources": list(_AUDIT_SOURCE_STATS.values()),
        "filtered_offers": list(_AUDIT_REJECTED_OFFERS.values()),
    }


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _norm(text: str) -> str:
    return _strip_accents(text or "").lower().strip()


CREATIVE_SEARCH_INDICATORS = (
    "design",
    "designer",
    "graphiste",
    "graphisme",
    "graphique",
    "graphic",
    "directeur artistique",
    "directrice artistique",
    "direction artistique",
    "art director",
    "art direction",
    "creatif",
    "creative",
    "ui",
    "ux",
    "illustrat",
    "infographiste",
    "maquettiste",
    "motion",
    "identite visuelle",
    "branding",
)

# Positive title patterns for genuine visual/digital/brand design & art direction roles
CREATIVE_TITLE_REGEX = re.compile(
    r"\b("
    r"design|designer|designers|"
    r"ai\s+designer|designer\s+ia|creative\s+ai|ai\s+creative|ai\s+artist|creative\s+technologist|prompt\s+designer|"
    r"graphiste|graphistes|graphisme|graphique|graphiques|graphic|"
    r"directeur\s+artistique|directrice\s+artistique|directeurs\s+artistiques|direction\s+artistique|"
    r"directeur\(rice\)\s+artistique|directrice\.eur\s+artistique|directeur\.rice\s+artistique|"
    r"art\s+director|art\s+direction|"
    r"da|d\.a\.|"
    r"infographiste|maquettiste|illustrateur|illustratrice|motion\s+design|motion\s+designer|motion\s+graphiste|"
    r"ui|ux|ui/ux|ux/ui|"
    r"directeur\s+de\s+creation|directrice\s+de\s+creation|creative\s+director|creative\s+lead|"
    r"lead\s+creatif|team\s+creatif|studio\s+creatif|"
    r"identite\s+visuelle|visual\s+brand"
    r")\b",
    re.IGNORECASE,
)

# Core design/art-direction words that override generic manager/lead titles (e.g. "Brand Design Manager")
CORE_DESIGN_ROLE_REGEX = re.compile(
    r"\b("
    r"design|designer|designers|"
    r"graphiste|graphistes|graphisme|graphique|graphic|"
    r"directeur\s+artistique|directrice\s+artistique|direction\s+artistique|art\s+director|"
    r"infographiste|maquettiste|illustrateur|illustratrice|"
    r"ui|ux"
    r")\b",
    re.IGNORECASE,
)

CREATIVE_FALSE_FRIENDS_REGEX = re.compile(
    r"\b("
    r"interior\s+design(?:er)?|architecte\s+d'interieur|architecture\s+d'interieur|decorateur\s+d'interieur|"
    r"fashion\s+design(?:er)?|styliste|modeliste|haute\s+couture|studio\s+denim|"
    r"leather\s+goods|small\s+leather|maroquinerie|footwear|chaussures?|"
    r"antenna\s+design(?:er)?|ic\s+design(?:er)?|chip\s+design(?:er)?|asic\s+design(?:er)?|fpga|pcb\s+design(?:er)?|"
    r"hardware\s+design(?:er)?|mechanical\s+design(?:er)?|electrical\s+design(?:er)?|"
    r"piping\s+design(?:er)?|civil\s+design(?:er)?|structural\s+design(?:er)?|hvac\s+design(?:er)?|"
    r"vehicle\s+motion|sound\s+design(?:er)?|narrative\s+design(?:er)?|level\s+design(?:er)?|"
    r"executive\s+assistant|assistante?\s+de\s+direction|office\s+manager|"
    r"store\s+director|directeur\s+de\s+magasin|responsable\s+boutique|responsable\s+stand|"
    r"evaluateur\s+de\s+marques?|evaluatrice\s+de\s+marques?|brand\s+evaluator|mystery\s+shopper|client\s+mystere|"
    r"supply\s+planner|controleur\s+de\s+gestion|ressources\s+humaines|human\s+resources|talent\s+acquisition|"
    r"facilities\s+associate|account\s+executive|business\s+analyst|business\s+developer|"
    r"software\s+engineer|data\s+engineer|data\s+scientist|development\s+engineer|"
    r"devops|fullstack|full-stack|backend\s+developer|senior\s+developer|product\s+developer|developpeur"
    r")\b",
    re.IGNORECASE,
)

NON_DESIGN_MANAGER_ROLES = (
    "project manager",
    "chef de projet",
    "chef.fe de projet",
    "cheffe de projet",
    "brand manager",
    "community manager",
    "marketing manager",
    "communications manager",
    "communication manager",
    "content manager",
    "account manager",
    "traffic manager",
    "campaign manager",
    "social media manager",
    "brand strategist",
    "creative strategist",
    "creative producer",
    "copywriter",
    "redacteur",
    "conception-redaction",
)

FRENCH_TARGET_INDICATORS = (
    "france",
    "paris",
    "lyon",
    "marseille",
    "bordeaux",
    "lille",
    "nantes",
    "toulouse",
    "nice",
    "strasbourg",
    "rennes",
    "montpellier",
    "grenoble",
    "ile-de-france",
    "hauts-de-seine",
    "seine-saint-denis",
    "val-de-marne",
    "yvelines",
    "essonne",
    "val-d'oise",
    "seine-et-marne",
    "neuilly",
    "levallois",
    "boulogne",
    "issy",
    "puteaux",
    "la defense",
    "saint-denis",
    "pantin",
    "montrouge",
    "clichy",
    "courbevoie",
    "ivry",
    "vincennes",
    "montreuil",
    "guyancourt",
    "poissy",
    "saclay",
    "massy",
    "versailles",
    "nanterre",
    "saint-ouen",
    "rueil",
    "suresnes",
    "charenton",
    "gentilly",
    "bagnolet",
    "malakoff",
    "vanves",
    "aix",
    "toulon",
    "annecy",
    "tours",
    "orleans",
    "clermont",
    "dijon",
    "angers",
    "brest",
    "le mans",
    "reims",
    "metz",
    "nancy",
    "mulhouse",
    "caen",
    "rouen",
    "amiens",
    "limoges",
    "perpignan",
    "bayonne",
    "biarritz",
    "pau",
    "la rochelle",
    "poitiers",
    "avignon",
    "nimes",
    "valence",
    "chambery",
)

FRENCH_DEPT_REGEX = re.compile(
    r"\b(75\d{0,3}|92\d{0,3}|93\d{0,3}|94\d{0,3}|91\d{0,3}|78\d{0,3}|95\d{0,3}|77\d{0,3}|69\d{0,3}|13\d{0,3}|33\d{0,3}|59\d{0,3}|44\d{0,3}|31\d{0,3}|06\d{0,3}|67\d{0,3}|35\d{0,3}|34\d{0,3}|38\d{0,3})\b"
)

ALLOWED_REMOTE_REGIONS_FOR_FR = (
    "teletravail",
    "remote job",
    "100% teletravail",
    "full remote",
    "remote (france)",
    "remote (europe)",
    "remote (european union)",
    "remote (eu)",
    "remote (emea)",
    "remote (worldwide)",
    "remote (global)",
    "remote (anywhere)",
)


def is_creative_design_search(keywords: Optional[Iterable[str]]) -> bool:
    if not keywords:
        return False
    joined = _norm(" ".join(k for k in keywords if k))
    if not joined:
        return False
    return any(ind in joined for ind in CREATIVE_SEARCH_INDICATORS)


def explain_job_rejection(
    title: str,
    description: str = "",
    keywords: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Return None if the offer is relevant, or a French explanation if it should be filtered out."""
    if not title or not title.strip():
        return "Intitulé vide"

    norm_title = _norm(title)
    kw_list = [k.strip() for k in (keywords or []) if k and k.strip()]
    if not kw_list:
        return None

    if is_creative_design_search(kw_list):
        joined_kw = _norm(" ".join(kw_list))
        ff_match = CREATIVE_FALSE_FRIENDS_REGEX.search(norm_title)
        if ff_match and ff_match.group(0).lower() not in joined_kw:
            return f"Terme hors-cible exclu : « {ff_match.group(0)} »"

        # Reject non-design manager/producer/copywriter roles unless a core design word is also in title
        for mgr_role in NON_DESIGN_MANAGER_ROLES:
            if mgr_role in norm_title and mgr_role not in joined_kw:
                if not CORE_DESIGN_ROLE_REGEX.search(norm_title):
                    return f"Rôle hors design visuel : « {mgr_role} »"

        if not CREATIVE_TITLE_REGEX.search(norm_title):
            return "Intitulé sans mot-clé design / direction artistique"

        return None

    # Non-creative generic keyword search:
    generic_modifiers = {
        "senior", "junior", "lead", "head", "chief", "manager", "assistant",
        "stage", "stagiaire", "alternance", "alternant", "cdi", "cdd",
        "freelance", "intern", "internship", "brand", "chef", "responsable",
        "associate", "specialist", "consultant", "de", "du", "des", "la", "le", "les",
        "and", "or", "in", "of", "for", "with", "h/f", "f/h", "m/f",
    }
    norm_desc = _norm(description)
    for kw in kw_list:
        n_kw = _norm(kw)
        if not n_kw:
            continue
        if n_kw in norm_title:
            return None
        tokens = [t for t in re.split(r"\W+", n_kw) if len(t) >= 2 and t not in generic_modifiers]
        if tokens and all(t in norm_title for t in tokens):
            return None
        if tokens and any(t in norm_title for t in tokens) and n_kw in norm_desc:
            return None

    return f"Aucun mot-clé recherché trouvé ({', '.join(kw_list[:3])})"


def is_relevant_job_offer(
    title: str,
    description: str = "",
    keywords: Optional[Iterable[str]] = None,
    *,
    source: Optional[str] = None,
    company: Optional[str] = None,
    location: Optional[str] = None,
    url: Optional[str] = None,
) -> bool:
    """Return True only if the job offer is genuinely relevant to the searched keywords."""
    reason = explain_job_rejection(title, description, keywords)
    if reason is not None:
        record_filtered_offer(
            source=source,
            title=title,
            reason=reason,
            company=company,
            location=location,
            url=url,
            description=description,
        )
        return False
    return True


def explain_location_rejection(
    job_location: Optional[str],
    remote_policy: Optional[str],
    target_location: Optional[str],
) -> Optional[str]:
    """Return None if compatible, or a French explanation if the job location is outside the target."""
    if not target_location or not target_location.strip():
        return None

    norm_target = _norm(target_location)
    is_fr_target = any(ind in norm_target for ind in FRENCH_TARGET_INDICATORS)
    if not is_fr_target:
        return None

    if not job_location or not job_location.strip():
        return f"Localisation absente (cible : {target_location})"

    norm_loc = _norm(job_location)

    # 1. Explicit French city, region, country, or postal/department code
    if any(ind in norm_loc for ind in FRENCH_TARGET_INDICATORS) or FRENCH_DEPT_REGEX.search(norm_loc):
        return None

    # 2. Pure remote or Europe/France/Global remote (rejecting US/Asia/LATAM/UK-only remote)
    if norm_loc in ("remote", "teletravail", "100% teletravail / remote") or any(
        allowed in norm_loc for allowed in ALLOWED_REMOTE_REGIONS_FOR_FR
    ):
        return None

    return f"Localisation hors France : « {job_location} »"


def is_location_compatible(
    job_location: Optional[str],
    remote_policy: Optional[str],
    target_location: Optional[str],
    *,
    source: Optional[str] = None,
    title: Optional[str] = None,
    company: Optional[str] = None,
    url: Optional[str] = None,
    description: str = "",
) -> bool:
    """Ensure international (US/UK/DE/Sweden/Asia/etc.) jobs don't pollute French searches."""
    reason = explain_location_rejection(job_location, remote_policy, target_location)
    if reason is not None:
        if title:
            record_filtered_offer(
                source=source,
                title=title,
                reason=reason,
                company=company,
                location=job_location,
                url=url,
                description=description,
            )
        return False
    return True


def expand_creative_queries(keywords: Optional[Iterable[str]], max_queries: int = 5) -> List[str]:
    """Expand a creative search with essential French/English/AI equivalents for broader recall."""
    base: List[str] = []
    for k in keywords or []:
        cleaned = k.strip()
        if cleaned and cleaned not in base:
            base.append(cleaned)

    if not base:
        return [
            "Brand Designer",
            "AI Designer",
            "Designer Graphique",
            "Directeur Artistique",
            "Graphiste",
        ][:max_queries]

    if not is_creative_design_search(base):
        return base[:max_queries]

    existing_lower = {b.lower() for b in base}
    equivalents = [
        "Brand Designer",
        "AI Designer",
        "Designer Graphique",
        "Directeur Artistique",
        "Graphic Designer",
        "Graphiste",
    ]
    for eq in equivalents:
        if len(base) >= max_queries:
            break
        if eq.lower() not in existing_lower:
            base.append(eq)
            existing_lower.add(eq.lower())

    return base[:max_queries]
