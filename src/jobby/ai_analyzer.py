"""AI & NLP Analyzer for job opportunities and social posts.

Provides:
- Intent classification: candidate seeking alternance/job vs company hiring.
- Accurate remote policy detection (full remote, hybrid, onsite, unknown).
- Concise 1-sentence AI summaries.
- Multi-provider AI engine: Local NLP heuristic (0 setup, fast, free) +
  optional LLM APIs (Gemini, OpenAI, Ollama).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, Optional, List
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
            gemini_model = model or "gemini-flash-latest"
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
                else:
                    # Fallback to gemma model if model is not available
                    fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemma-4-26b-a4b-it:generateContent?key={gemini_key}"
                    resp2 = await client.post(fallback_url, json={"contents": [{"parts": [{"text": prompt}]}]})
                    if resp2.status_code == 200:
                        text_out = resp2.json()["candidates"][0]["content"]["parts"][0]["text"]
                        cleaned = re.sub(r"^```json\s*|\s*```$", "", text_out.strip())
                        parsed = json.loads(cleaned)
                        parsed["provider"] = "gemini-gemma"
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


# ===================================================================
# Deep Candidate CV Matching & Approach Pitch (Google AI Studio)
# ===================================================================

async def evaluate_candidate_match(
    profile_data: Dict[str, Any],
    opportunity_data: Dict[str, Any],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluates how well a candidate's CV, portfolio and skills match a specific job opportunity.
    Returns: score (0-100), verdict, strengths, weaknesses, approach pitch.
    """
    gemini_key = api_key or os.environ.get("GEMINI_API_KEY")

    if gemini_key:
        try:
            gemini_model = model or "gemini-flash-latest"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}"
            
            prompt = f"""Tu es un expert du recrutement tech et du matching de talents.
Évalue la compatibilité entre ce candidat (CV, portfolio, compétences) et cette offre d'emploi.

--- PROFIL CANDIDAT ---
Rôles ciblés : {', '.join(profile_data.get('target_roles', []))}
Compétences déclarées : {', '.join(profile_data.get('skills', []))}
Portfolio / Projets : {profile_data.get('portfolio_url') or 'Non renseigné'}
GitHub : {profile_data.get('github_url') or 'Non renseigné'}
Bio / Parcours : {profile_data.get('bio') or 'Non renseigné'}
Extrait du CV :
\"\"\"
{(profile_data.get('cv_text') or '')[:3000]}
\"\"\"

--- OFFRE D'EMPLOI / POST ---
Titre : {opportunity_data.get('title')}
Entreprise : {opportunity_data.get('company') or 'Non précisée'}
Localisation / Remote : {opportunity_data.get('location')} ({opportunity_data.get('remote_policy')})
Auteur / Recruteur : {opportunity_data.get('author_name') or ''}
Description de l'offre :
\"\"\"
{(opportunity_data.get('description') or '')[:3000]}
\"\"\"

Renvoie STRICTEMENT un objet JSON valide (sans backticks markdown si possible) avec ces clés :
- "score": nombre flottant ou entier entre 0 et 100 représentant l'adéquation technique et humaine.
- "verdict": courte phrase d'appréciation (ex: "Excellente opportunité", "Profil très aligné", "Gaps sur certaines technos").
- "strengths": liste de 2 à 4 points forts concrets du profil pour cette offre précise.
- "weaknesses": liste de 1 à 3 points d'attention ou technos secondaires manquantes.
- "pitch": un message d'accroche personnalisé de 3 phrases maximum, direct, percutant et poli, prêt à être envoyé au recruteur sur LinkedIn ou par email pour attirer son attention.
"""

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json",
                },
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    cleaned = re.sub(r"^```json\s*|\s*```$", "", raw_text.strip())
                    parsed = json.loads(cleaned)
                    parsed["score"] = float(parsed.get("score", 75))
                    return parsed
        except Exception as e:
            logger.warning(f"Gemini CV match evaluation failed: {e}")

    # Fallback heuristic calculation
    return _heuristic_cv_match(profile_data, opportunity_data)


def _heuristic_cv_match(profile_data: Dict[str, Any], opportunity_data: Dict[str, Any]) -> Dict[str, Any]:
    """Fast local heuristic matching when no LLM key is available."""
    skills = [s.lower() for s in profile_data.get("skills", [])]
    roles = [r.lower() for r in profile_data.get("target_roles", [])]
    cv_text = (profile_data.get("cv_text") or "").lower()

    title = (opportunity_data.get("title") or "").lower()
    desc = (opportunity_data.get("description") or "").lower()
    full_text = f"{title} {desc}"

    matched_skills = [s for s in skills if s in full_text or s in cv_text]
    role_match = any(r in title or r in desc for r in roles)

    score = 50.0
    if role_match:
        score += 25.0
    if skills:
        score += min(25.0, (len(matched_skills) / max(len(skills), 1)) * 25.0)

    score = round(min(98.0, max(40.0, score)), 1)
    
    verdict = "Profil très aligné" if score >= 80 else ("Bonne opportunité" if score >= 65 else "À explorer avec nuances")
    strengths = [f"Compétence identifiée : {s.capitalize()}" for s in matched_skills[:3]] or ["Expérience générale en tech"]
    weaknesses = ["Vérifier le niveau d'autonomie et les prérequis secondaires"]

    contact = opportunity_data.get("author_name") or "Bonjour"
    role_hint = profile_data.get("target_roles", ["développeur"])[0]
    pitch = f"Bonjour {contact}, je viens de voir votre publication concernant le poste de {opportunity_data.get('title')}. En tant que {role_hint} passionné, vos problématiques font écho à mes projets récents. Seriez-vous ouvert à un rapide échange cette semaine ?"

    return {
        "score": score,
        "verdict": verdict,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "pitch": pitch,
    }


# ===================================================================
# Customized Cover Letter Generation (Style & Tone Cloning)
# ===================================================================

async def generate_tailored_cover_letter(
    profile_data: Dict[str, Any],
    opportunity_data: Dict[str, Any],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """
    Generates a personalized cover letter matching the candidate's exact tone of voice and writing style.
    Uses Google AI Studio Gemini if available, or a smart template fallback.
    """
    gemini_key = api_key or os.environ.get("GEMINI_API_KEY")
    writing_sample = profile_data.get("writing_sample") or ""
    writing_tone = profile_data.get("writing_tone") or "naturel, direct, enthousiaste et percutant"

    if gemini_key:
        try:
            gemini_model = model or "gemini-flash-latest"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}"

            sample_instruction = ""
            if writing_sample.strip():
                sample_instruction = f"""
Voici un exemple de texte écrit par le candidat. Analyse son style (vocabulaire, rythme, absence de clichés corporate, authenticité, clarté) et ÉCRIS EN IMITANT EXACTEMENT SA VOIX :
\"\"\"
{writing_sample[:2500]}
\"\"\"
"""
            else:
                sample_instruction = f"Adopte un ton : {writing_tone}. Pas de formules pompeuses, sois humain, direct et orienté valeur."

            company_str = opportunity_data.get("company") or "L'équipe"
            author_str = opportunity_data.get("author_name") or ""
            prompt = f"""Tu es le candidat lui-même. Rédige une lettre de motivation sur-mesure pour postuler à cette opportunité.

--- CONSIGNES DE STYLE ---
{sample_instruction}
Consignes strictes :
- Pas de phrases creuses du genre "Je me permets de vous contacter" ou "Votre entreprise prestigieuse leader du marché".
- Va droit au but : montre immédiatement que tu as compris les enjeux du poste et de l'entreprise.
- Fais le lien avec des réalisations concrètes tirées de ton CV ou de ton portfolio.
- Conclus sur une proposition d'échange simple et prompte.
- Langue : Français, environ 3 à 4 paragraphes courts et aérés.

--- TON PROFIL (CANDIDAT) ---
Nom : {profile_data.get('full_name') or 'Le Candidat'}
Poste visé : {', '.join(profile_data.get('target_roles', []))}
Compétences : {', '.join(profile_data.get('skills', []))}
Portfolio / Projets : {profile_data.get('portfolio_url') or 'Portfolio en ligne'}
GitHub : {profile_data.get('github_url') or ''}
CV :
\"\"\"
{(profile_data.get('cv_text') or '')[:3000]}
\"\"\"

--- L'OFFRE / LE POST ---
Titre : {opportunity_data.get('title')}
Entreprise : {company_str}
Contact / Recruteur : {author_str}
Contenu :
\"\"\"
{(opportunity_data.get('description') or '')[:3000]}
\"\"\"

Rédige directement la lettre en texte brut (avec sauts de ligne clairs), sans fioritures méta ni introduction.
"""

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.4,
                },
            }
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    letter = data["candidates"][0]["content"]["parts"][0]["text"]
                    return letter.strip()
        except Exception as e:
            logger.warning(f"Gemini cover letter generation failed: {e}")

    # Fallback template
    company = opportunity_data.get("company") or "votre équipe"
    title = opportunity_data.get("title") or "ce poste"
    portfolio_mention = f"\nVous pouvez retrouver mes projets et réalisations sur mon portfolio : {profile_data.get('portfolio_url')}" if profile_data.get("portfolio_url") else ""
    
    return f"""Bonjour,

J'ai découvert avec beaucoup d'intérêt votre recherche pour le rôle de {title} chez {company}. 

Ce qui a retenu mon attention dans votre projet, c'est l'alignement direct avec mes compétences récentes et les défis techniques que j'apprécie particulièrement relever.

Au fil de mes expériences, j'ai notamment eu l'occasion de concevoir des solutions performantes et d'aborder des problématiques similaires à celles évoquées dans votre annonce.{portfolio_mention}

Je serais ravi de vous présenter plus en détail comment mon parcours et mon énergie peuvent s'intégrer au sein de votre équipe.

Disponible pour un premier échange quand cela vous conviendra.

Bien cordialement,"""


# ===================================================================
# "3ème Voie" : Ranking & Semantic Matching by Ideal Job Brief
# ===================================================================

async def rank_opportunities_by_ideal_brief(
    brief: str,
    opportunities: List[Any],
    profile_data: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Ranks a list of job opportunities / social posts against the user's verbose 'Ideal Job Brief'.
    Uses Google AI Studio Gemini Flash for semantic multi-factor scoring and advice,
    with a robust keyword & semantic heuristic fallback.
    """
    if not brief or not opportunities:
        return []

    gemini_key = api_key or os.environ.get("GEMINI_API_KEY")
    gemini_model = model or "gemini-flash-latest"

    # Normalize opportunity inputs to dict
    items_to_score = []
    for opp in opportunities:
        if isinstance(opp, dict):
            opp_id = opp.get("id") or ""
            title = opp.get("title") or ""
            company = opp.get("company") or ""
            loc = opp.get("location") or ""
            remote = opp.get("remote_policy") or ""
            desc = opp.get("description") or ""
            opp_type = opp.get("opportunity_type") or "offer"
        else:
            opp_id = getattr(opp, "id", "")
            title = getattr(opp, "title", "")
            company = getattr(opp, "company", "")
            loc = getattr(opp, "location", "")
            remote = getattr(opp, "remote_policy", "")
            desc = getattr(opp, "description", "")
            opp_type = getattr(opp, "opportunity_type", "offer")
            if hasattr(opp_type, "value"):
                opp_type = opp_type.value

        items_to_score.append({
            "id": opp_id,
            "title": title,
            "company": company,
            "location": loc,
            "remote": remote,
            "type": str(opp_type),
            "description": desc[:600],
        })

    results_by_id: Dict[str, Dict[str, Any]] = {}

    # Attempt Gemini batch scoring
    if gemini_key:
        profile_context = ""
        if profile_data:
            roles = ", ".join(profile_data.get("target_roles", []))
            skills = ", ".join(profile_data.get("skills", []))
            profile_context = f"\nContexte candidat : Rôles visés: [{roles}], Compétences: [{skills}]"

        # Split into batches of 25 to respect prompt token limits and optimize latency
        batch_size = 25
        batches = [items_to_score[i:i + batch_size] for i in range(0, len(items_to_score), batch_size)]

        async def process_batch(batch_items: List[Dict[str, Any]]) -> None:
            prompt = f"""Tu es l'agent IA de matching de carrière Jobby.
Le candidat a rédigé le brief verbeux de son 'Job Idéal' :
\"\"\"
{brief.strip()}
\"\"\"
{profile_context}

Analyse chaque opportunité d'emploi ou post LinkedIn ci-dessous par rapport aux souhaits, stack, culture, séniorité et dealbreakers exprimés dans le brief.

--- LISTE DES OPPORTUNITÉS ---
{json.dumps(batch_items, ensure_ascii=False, indent=1)}

Pour CHAQUE élément de la liste, évalue précisément :
1. "id" : l'identifiant exact fourni
2. "score" : entier de 0 à 100 :
   - 90-100 : Match Idéal (parfaitement aligné avec les souhaits clés du brief)
   - 75-89 : Très bon match (solide adéquation technique, culturelle ou de rôle)
   - 45-74 : Match partiel (bonnes compétences mais compromis sur secteur, rôle ou remote)
   - 0-44 : Peu ou pas pertinent (hors sujet, alternance non voulue, décalage)
3. "verdict" : phrase concise (max 15 mots) résumant la raison de la note (ex: "Idéal : rôle Lead Product Designer en full remote avec Figma").
4. "pitch" : phrase d'accroche ou conseil stratégique pour postuler.

Réponds UNIQUEMENT avec un tableau JSON strict, sans backticks markdown, sans texte additionnel :
[
  {{ "id": "...", "score": 92, "verdict": "...", "pitch": "..." }}
]
"""
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json",
                },
            }
            try:
                async with httpx.AsyncClient(timeout=12.0) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        raw_data = resp.json()
                        text_resp = raw_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        # Clean backticks if any
                        text_resp = re.sub(r"^```(?:json)?\s*", "", text_resp)
                        text_resp = re.sub(r"\s*```$", "", text_resp)
                        parsed = json.loads(text_resp)
                        if isinstance(parsed, list):
                            for entry in parsed:
                                if isinstance(entry, dict) and "id" in entry:
                                    results_by_id[entry["id"]] = {
                                        "id": entry["id"],
                                        "score": float(max(0, min(100, int(entry.get("score", 50))))),
                                        "verdict": str(entry.get("verdict", "")),
                                        "pitch": str(entry.get("pitch", "")),
                                    }
            except Exception as e:
                logger.warning(f"Batch Gemini scoring failed: {e}")

        # Run batches concurrently
        await asyncio.gather(*[process_batch(b) for b in batches])

    # Fallback heuristic for any unscored items
    brief_lower = brief.lower()
    brief_words = set(re.findall(r"\b[a-zA-Zà-üÀ-Ü]{4,}\b", brief_lower))
    # Exclude common stopwords
    stopwords = {"dans", "pour", "avec", "sans", "plus", "tout", "tous", "cette", "comme", "mais", "faire", "cherche", "poste", "job", "souhaite"}
    key_words = brief_words - stopwords

    final_results = []
    for item in items_to_score:
        opp_id = item["id"]
        if opp_id in results_by_id:
            final_results.append(results_by_id[opp_id])
            continue

        # Heuristic scoring
        full_text = f"{item['title']} {item['company']} {item['location']} {item['description']}".lower()
        matched = [w for w in key_words if w in full_text]
        match_ratio = len(matched) / max(len(key_words), 1)
        base_score = 35.0 + min(55.0, match_ratio * 120.0)

        # Bonus remote if asked in brief
        if "remote" in brief_lower or "télétravail" in brief_lower:
            if item["remote"] == "full":
                base_score += 10.0
            elif item["remote"] == "hybrid":
                base_score += 5.0

        score = float(max(10, min(95, round(base_score))))
        verdict = f"Match sémantique ({len(matched)} mots-clés alignés : {', '.join(matched[:3])})" if matched else "Pertinence moyenne basée sur le profil"
        pitch = "Mettez en avant vos compétences alignées avec l'offre et votre autonomie."

        fallback_entry = {
            "id": opp_id,
            "score": score,
            "verdict": verdict,
            "pitch": pitch,
        }
        results_by_id[opp_id] = fallback_entry
        final_results.append(fallback_entry)

    # Sort descending by score
    final_results.sort(key=lambda x: x["score"], reverse=True)
    return final_results


