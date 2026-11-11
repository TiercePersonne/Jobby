"""Unit tests for AI & NLP Post Analyzer and Remote Policy Detection."""

import pytest
from jobby.ai_analyzer import analyze_post_heuristic, detect_remote_policy


def test_detect_remote_policy():
    # 100% remote variations
    assert detect_remote_policy(title="Backend Dev Full Remote", description="Poste ouvert en 100% télétravail") == "full"
    assert detect_remote_policy(title="Python Engineer", description="Totalement à distance, remote France") == "full"
    assert detect_remote_policy(title="Dev", description="", location="100% Télétravail / Remote") == "full"

    # Hybrid variations
    assert detect_remote_policy(title="Dev Python", description="Politique hybride avec 2 jours de télétravail par semaine") == "hybrid"
    assert detect_remote_policy(title="Data Scientist", description="Télétravail flexible (3j/semaine)") == "hybrid"

    # On-site variations
    assert detect_remote_policy(title="Dev C++", description="Poste 100% présentiel sur site à Paris, pas de télétravail") == "onsite"

    # Unknown
    assert detect_remote_policy(title="Dev Python", description="Salaire 55k") == "unknown"


def test_analyze_post_candidate_seeking_alternance():
    text = (
        "Bonjour le réseau ! Actuellement étudiante en Master 1 Data Science, "
        "je suis activement à la recherche d’une alternance de 12 mois à partir de septembre 2026. "
        "Rythme 3j entreprise / 2j école. N'hésitez pas à partager mon CV ! #recherchealternance #alternance"
    )
    result = analyze_post_heuristic(text, author_name="Julie", author_title="Étudiante en Data")
    assert result["intent"] == "candidate_seeking_alternance"
    assert result["is_candidate"] is True
    assert result["is_alternance"] is True
    assert "alternance" in result["summary"].lower()


def test_analyze_post_hiring_alternance():
    text = (
        "Notre équipe Tech s'agrandit ! Nous recrutons un alternant Développeur Python (F/H) "
        "pour la rentrée 2026. Télétravail partiel possible (2j/semaine). Rejoignez-nous !"
    )
    result = analyze_post_heuristic(text, author_name="Paul", author_title="Tech Lead")
    assert result["intent"] == "hiring_alternance"
    assert result["is_candidate"] is False
    assert result["is_alternance"] is True
    assert result["remote_policy"] == "hybrid"


def test_analyze_post_hiring_job():
    text = (
        "On recrute ! Nous cherchons un Lead Developer FastAPI / PostgreSQL en CDI. "
        "100% remote possible partout en France. Contactez-moi en MP."
    )
    result = analyze_post_heuristic(text, author_name="Sophie", author_title="DRH")
    assert result["intent"] == "hiring_job"
    assert result["is_candidate"] is False
    assert result["is_alternance"] is False
    assert result["remote_policy"] == "full"
