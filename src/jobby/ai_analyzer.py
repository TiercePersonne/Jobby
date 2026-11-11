"""AI & NLP Analyzer for job opportunities and social posts.

Provides:
- Intent classification: candidate seeking alternance/job vs company hiring.
- Accurate remote policy detection (full remote, hybrid, onsite, unknown).
- Concise 1-sentence AI summaries.
- Multi-provider AI engine: Local NLP heuristic (0 setup, fast, free) +
  optional LLM APIs (Gemini, OpenAI, Ollama).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger("jobby.ai")


def detect_remote_policy(
    title: str = "",
    description: str = "",
    location: Optional[str] = None,
    current_policy: Optional[str] = None,
) -> str:
    """
    Detects whether an offer/post is:
    - 'full': 100% remote / télétravail total
    - 'hybrid': hybride / télétravail partiel
    - 'onsite': sur site / présentiel
    - 'unknown': non précisé
    """
    if current_policy in ["full", "hybrid", "onsite"]:
        return current_policy

    loc_str = (location or "").lower()
    desc_str = (description or "").lower()
    title_str = (title or "").lower()
    text = f"{title_str} {loc_str} {desc_str}"

    # 1. Full remote checks
    full_remote_patterns = [
        r"100\s*%\s*remote",
        r"full\s*remote",
        r"full-remote",
        r"100\s*%\s*t[ée]l[ée]travail",
        r"t[ée]l[ée]travail\s*(total|complet|[àa]\s*100\s*%)",
        r"totalement\s*[àa]\s*distance",
        r"remote\s*only",
        r"remote\s*d['\’]o[uù]\s*vous\s*voulez",
        r"remote\s*-\s*france",
        r"remote\s*\(france\)",
        r"remote\s*world",
        r"anywhere",
    ]
    for pat in full_remote_patterns:
        if re.search(pat, text):
            return "full"

    # 2. Hybrid checks
    hybrid_patterns = [
        r"hybrid[e]?",
        r"t[ée]l[ée]travail\s*(partiel|occasionnel|possible|r[ée]gulier|flexible)",
        r"\d\s*j(ours)?\s*(de)?\s*t[ée]l[ée]travail",
        r"t[ée]l[ée]travail\s*:\s*\d\s*j",
        r"\d\s*j\s*/\s*semaine",
        r"\d\s*jours?\s*/\s*semaine",
        r"politique\s*hybride",
        r"mode\s*hybride",
        r"rythme\s*hybride",
        r"mixte",
    ]
    for pat in hybrid_patterns:
        if re.search(pat, text):
            return "hybrid"

    # 3. On-site checks
    onsite_patterns = [
        r"sur\s*site",
        r"sur-site",
        r"on-site",
        r"onsite",
        r"pr[ée]sentiel",
        r"au\s*bureau",
        r"pas\s*de\s*t[ée]l[ée]travail",
        r"aucun\s*t[ée]l[ée]travail",
        r"non\s*ouvert\s*au\s*t[ée]l[ée]travail",
    ]
    for pat in onsite_patterns:
        if re.search(pat, text):
            return "onsite"

    # 4. Loose remote keyword if not accompanied by onsite terms
    if "remote" in loc_str or "télétravail" in loc_str:
        return "full"

    return "unknown"


def analyze_post_heuristic(
    commentary: str,
    author_name: str = "",
    author_title: str = "",
) -> Dict[str, Any]:
    """
    Fast rule-based NLP classifier for LinkedIn posts.
    Distinguishes candidates looking for alternance/internships vs recruiters hiring.
    """
    comm_lower = commentary.lower()
    title_lower = (author_title or "").lower()
    full_text = f"{comm_lower} {title_lower}"

    # Alternance / Student markers
    alternance_markers = [
        "alternance",
        "alternant",
        "alternante",
        "apprentissage",
        "contrat d'apprentissage",
        "contrat de professionnalisation",
        "contrat pro",
        "stage",
        "stagiaire",
        "#recherchealternance",
        "#alternance",
        "#trouvetonentreprise",
    ]
    is_alternance = any(m in comm_lower for m in alternance_markers)

    # Candidate seeking markers (first-person intent to find work)
    candidate_markers = [
        "je recherche",
        "je suis à la recherche",
        "à la recherche d'une",
        "à la recherche d'un",
        "en recherche active",
        "en recherche d'une",
        "en recherche d'un",
        "cherche une alternance",
        "cherche un stage",
        "cherche un emploi",
        "cherche un poste",
        "cherche activement",
        "mon cv",
        "mon portfolio",
        "mon profil",
        "partagez mon post",
        "n'hésitez pas à partager",
        "étudiant en",
        "étudiante en",
        "master 1",
        "master 2",
        "m1",
        "m2",
        "bachelor",
        "école",
        "rythme :",
        "rythme d'alternance",
        "pour la rentrée",
        "dès septembre",
        "#opentowork",
        "open to work",
        "disponible immédiatement",
        "en transition professionnelle",
    ]
    candidate_score = sum(1 for m in candidate_markers if m in comm_lower)

    # Author headline student / seeker markers
    if any(m in title_lower for m in ["étudiant", "student", "en recherche", "looking for", "aspirant", "junior"]):
        candidate_score += 2

    # Recruiter / Company hiring markers
    hiring_markers = [
        "nous recrutons",
        "nous recherchons",
        "on recrute",
        "je recrute",
        "notre équipe grandit",
        "notre équipe s'agrandit",
        "rejoignez-nous",
        "rejoindre notre",
        "rejoins notre",
        "poste à pourvoir",
        "opportunité en",
        "offre d'emploi",
        "offre d'alternance",
        "notre futur",
        "notre future",
        "notre alternant",
        "we are hiring",
        "we're hiring",
        "join our team",
        "apply here",
        "pour postuler",
        "candidatures à",
        "envoyez votre cv",
        "hiring",
    ]
    hiring_score = sum(1 for m in hiring_markers if m in comm_lower)

    # Author headline recruiter markers
    if any(m in title_lower for m in ["recruteur", "recruiter", "talent acquisition", "rh", "hr", "founder", "cto", "ceo", "tech lead", "manager", "engineering lead"]):
        hiring_score += 2

    # Intent Decision
    if is_alternance:
        if candidate_score > hiring_score or (candidate_score >= 1 and hiring_score == 0):
            intent = "candidate_seeking_alternance"
            is_candidate = True
        elif hiring_score > 0:
            intent = "hiring_alternance"
            is_candidate = False
        else:
            # Default to candidate if alternance without hiring verbs
            intent = "candidate_seeking_alternance"
            is_candidate = True
    else:
        if candidate_score >= 2 and candidate_score > hiring_score:
            intent = "candidate_seeking_job"
            is_candidate = True
        elif hiring_score > 0 or candidate_score == 0:
            intent = "hiring_job"
            is_candidate = False
        else:
            intent = "other"
            is_candidate = False

    # Generate concise summary
    author_disp = author_name or "Auteur"
    first_line = commentary.strip().split("\n")[0][:100]

    if intent == "candidate_seeking_alternance":
        summary = f"🎓 Candidat : {author_disp} recherche une alternance / stage ({first_line})"
    elif intent == "hiring_alternance":
        summary = f"🏢 Offre : {author_disp} recrute un profil en alternance ({first_line})"
    elif intent == "candidate_seeking_job":
        summary = f"🙋 Candidat : {author_disp} en recherche active d'un poste ({first_line})"
    elif intent == "hiring_job":
        summary = f"💼 Recrutement : {author_disp} recrute ({first_line})"
    else:
        summary = f"📢 Publication réseau de {author_disp}"

    remote = detect_remote_policy(title=first_line, description=commentary)

    return {
        "intent": intent,
        "is_candidate": is_candidate,
        "is_alternance": is_alternance,
        "summary": summary,
        "remote_policy": remote,
        "provider": "heuristic",
    }


async def analyze_post_with_llm(
    commentary: str,
    author_name: str = "",
    author_title: str = "",
    provider: str = "auto",
    api_key: Optional[str] = None,
    ollama_url: Optional[str] = "http://localhost:11434",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deep semantic analysis using LLM (Gemini, OpenAI, or local Ollama),
    falling back to heuristic if not configured or in case of error.
    """
    # Check environment keys if not passed explicitly
    gemini_key = api_key or os.environ.get("GEMINI_API_KEY")
    openai_key = api_key or os.environ.get("OPENAI_API_KEY")

    # Determine provider
    chosen_provider = provider
    if chosen_provider == "auto":
        if gemini_key:
            chosen_provider = "gemini"
        elif openai_key:
            chosen_provider = "openai"
        else:
            # Check if Ollama is responsive
            chosen_provider = "heuristic"

    # Try Ollama if explicitly asked
    if chosen_provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                prompt = _build_llm_prompt(commentary, author_name, author_title)
                ollama_model = model or "llama3" or "mistral"
                resp = await client.post(
                    f"{ollama_url.rstrip('/')}/api/generate",
                    json={"model": ollama_model, "prompt": prompt, "format": "json", "stream": False},
                )
                if resp.status_code == 200:
                    raw_text = resp.json().get("response", "{}")
                    parsed = json.loads(raw_text)
                    parsed["provider"] = "ollama"
                    return parsed
        except Exception as e:
            logger.debug(f"Ollama call failed, falling back to heuristic: {e}")

    # Try Gemini
    if chosen_provider == "gemini" and gemini_key:
        try:
            gemini_model = model or "gemini-1.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}"
            prompt = _build_llm_prompt(commentary, author_name, author_title)
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"},
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    text_out = data["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text_out)
                    parsed["provider"] = "gemini"
                    return parsed
        except Exception as e:
            logger.warning(f"Gemini API call failed: {e}")

    # Try OpenAI
    if chosen_provider == "openai" and openai_key:
        try:
            openai_model = model or "gpt-4o-mini"
            url = "https://api.openai.com/v1/chat/completions"
            prompt = _build_llm_prompt(commentary, author_name, author_title)
            headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
            payload = {
                "model": openai_model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "You are an expert HR and recruitment intelligence classifier."},
                    {"role": "user", "content": prompt},
                ],
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_text = data["choices"][0]["message"]["content"]
                    parsed = json.loads(raw_text)
                    parsed["provider"] = "openai"
                    return parsed
        except Exception as e:
            logger.warning(f"OpenAI API call failed: {e}")

    # Fallback to local heuristic
    return analyze_post_heuristic(commentary, author_name, author_title)


def _build_llm_prompt(commentary: str, author_name: str, author_title: str) -> str:
    return f"""Analyse ce post LinkedIn et classe-le précisément au format JSON.

Auteur : {author_name or 'Inconnu'} ({author_title or 'Non spécifié'})
Contenu du post :
\"\"\"
{commentary[:2000]}
\"\"\"

Tu dois renvoyer STRICTEMENT un JSON valide avec les clés :
- "intent": une valeur parmi ["candidate_seeking_alternance", "hiring_alternance", "candidate_seeking_job", "hiring_job", "other"]
  * "candidate_seeking_alternance": un étudiant/candidat qui cherche une alternance ou un stage (ex: "Je recherche une alternance", CV partagé).
  * "hiring_alternance": une entreprise ou un recruteur qui recrute un(e) alternant(e).
  * "candidate_seeking_job": un candidat qui cherche un travail (CDI, CDD, Freelance).
  * "hiring_job": une entreprise ou recruteur qui propose un poste classique (CDI, CDD, Freelance).
  * "other": discussion ou actualité générale.
- "is_candidate": true si l'auteur est un candidat qui cherche du travail, false s'il recrute.
- "is_alternance": true s'il s'agit d'alternance ou de stage, false sinon.
- "summary": une phrase concise en français résumant le post (max 100 caractères).
- "remote_policy": une valeur parmi ["full", "hybrid", "onsite", "unknown"]
"""
