# Jobby 🎯

Agent intelligent de recherche d'emploi automatisée multi-plateforme.

## 📌 Présentation du Projet
**Jobby** est un agent IA autonome conçu pour automatiser et optimiser la recherche d'emploi. Il prospecte et agrège des offres depuis de multiples canaux :
- **LinkedIn** (offres, posts de recruteurs, alertes réseau)
- **Job boards & agrégateurs** (Indeed, Welcome to the Jungle, Glassdoor, France Travail / Apec, Google Jobs)
- **Pages Carrières & ATS** (Greenhouse, Lever, Workday, etc.)
- **Flux RSS & alertes email**

### Capacités clés prévues :
1. **Scraping / Ingestion multi-sources** : Collecte intelligente et respectueuse des quotas / anti-bot.
2. **Filtrage sémantique & scoring** : Matching avec le CV et le profil du candidat via LLM.
3. **Tableau de bord de suivi (CRM de candidature)** : Statuts des candidatures, relances, entretiens.
4. **Assistance à la candidature** : Génération de lettres de motivation ciblées et adaptation de CV.
5. **Automatisation & alertes** : Notifications en temps réel (Discord, Telegram, Email, Slack).

---

## ⚡ Méthode de développement : BMAD (Breakthrough Method for Agile AI-Driven Development)
Ce projet est structuré et piloté avec le framework **BMAD (v6.12.4)**.

### Modules installés :
- **Core** : Moteur BMAD et gestion de configuration.
- **BMM (BMad Method)** : Suite agile complète (Analyse, PRD, Architecture, Epics & Stories, Dev, QA, Rétrospectives).
- **BMB (BMad Builder)** : Outils pour créer des agents, workflows et modules sur-mesure.

### Outils & Agents configurés :
- Compatible avec **Google Antigravity** (`.agent/skills`) et **Antigravity CLI** (`.agents/skills`).
- Outil Python ultra-rapide **`uv`** configuré pour l'exécution des scripts de rendu BMAD.

### Comment démarrer :
Pour démarrer les phases BMAD :
1. **Clarification & Brief** : Invoquer `bmad-product-brief` ou `bmad-agent-analyst` pour formaliser le périmètre de Jobby.
2. **Spécifications (PRD)** : Invoquer `bmad-prd` ou `bmad-agent-pm`.
3. **Architecture technique** : Invoquer `bmad-architecture` ou `bmad-agent-architect`.
4. **Découpage & Développement** : Invoquer `bmad-create-epics-and-stories` puis `bmad-agent-dev`.
5. **Assistance globale** : Demander `bmad-help` ou `bmad-party-mode` pour une consultation multi-agents.
