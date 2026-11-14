"""Relevance and location filtering utilities for Jobby job providers."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional


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


def is_relevant_job_offer(
    title: str,
    description: str = "",
    keywords: Optional[Iterable[str]] = None,
) -> bool:
    """Return True only if the job offer is genuinely relevant to the searched keywords."""
    if not title or not title.strip():
        return False

    norm_title = _norm(title)
    kw_list = [k.strip() for k in (keywords or []) if k and k.strip()]
    if not kw_list:
        return True

    if is_creative_design_search(kw_list):
        joined_kw = _norm(" ".join(kw_list))
        ff_match = CREATIVE_FALSE_FRIENDS_REGEX.search(norm_title)
        if ff_match and ff_match.group(0).lower() not in joined_kw:
            return False

        # Reject non-design manager/producer/copywriter roles unless a core design word is also in title
        for mgr_role in NON_DESIGN_MANAGER_ROLES:
            if mgr_role in norm_title and mgr_role not in joined_kw:
                if not CORE_DESIGN_ROLE_REGEX.search(norm_title):
                    return False

        if not CREATIVE_TITLE_REGEX.search(norm_title):
            return False

        return True

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
            return True
        tokens = [t for t in re.split(r"\W+", n_kw) if len(t) >= 2 and t not in generic_modifiers]
        if tokens and all(t in norm_title for t in tokens):
            return True
        if tokens and any(t in norm_title for t in tokens) and n_kw in norm_desc:
            return True

    return False


def is_location_compatible(
    job_location: Optional[str],
    remote_policy: Optional[str],
    target_location: Optional[str],
) -> bool:
    """Ensure international (US/UK/DE/Sweden/Asia/etc.) jobs don't pollute French searches."""
    if not target_location or not target_location.strip():
        return True

    norm_target = _norm(target_location)
    is_fr_target = any(ind in norm_target for ind in FRENCH_TARGET_INDICATORS)
    if not is_fr_target:
        return True

    if not job_location or not job_location.strip():
        return False

    norm_loc = _norm(job_location)

    # 1. Explicit French city, region, country, or postal/department code
    if any(ind in norm_loc for ind in FRENCH_TARGET_INDICATORS) or FRENCH_DEPT_REGEX.search(norm_loc):
        return True

    # 2. Pure remote or Europe/France/Global remote (rejecting US/Asia/LATAM/UK-only remote)
    if norm_loc in ("remote", "teletravail", "100% teletravail / remote") or any(
        allowed in norm_loc for allowed in ALLOWED_REMOTE_REGIONS_FOR_FR
    ):
        return True

    return False


def expand_creative_queries(keywords: Optional[Iterable[str]], max_queries: int = 4) -> List[str]:
    """Expand a creative search with essential French/English equivalents for broader recall."""
    base: List[str] = []
    for k in keywords or []:
        cleaned = k.strip()
        if cleaned and cleaned not in base:
            base.append(cleaned)

    if not base:
        return ["Brand Designer", "Designer Graphique", "Directeur Artistique", "Graphiste"][:max_queries]

    if not is_creative_design_search(base):
        return base[:max_queries]

    existing_lower = {b.lower() for b in base}
    equivalents = [
        "Brand Designer",
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
