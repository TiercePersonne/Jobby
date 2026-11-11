---
id: SPEC-jobby
companions: []
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Jobby — Agent Autonome Multi-Sources de Veille d'Emploi (Focus LinkedIn Offres & Posts)

## Why

La recherche d'emploi active souffre d'un angle mort critique : une part substantielle des meilleures opportunités n'est jamais publiée sous forme d'offres d'emploi classiques, mais émerge directement dans les flux sociaux sous forme de posts spontanés ("On recrute dans mon équipe", "Je cherche un profil X pour...", "Post ouvert suite à levée de fonds"). De plus, les candidats doivent surveiller manuellement une constellation de plateformes disparates (LinkedIn, Welcome to the Jungle, HelloWork, Glassdoor, JobbyGather). 

**Jobby** résout cette fragmentation et cette perte de temps en agrégeant, dédupliquant et analysant automatiquement les offres officielles et les publications informelles de recrutement, avec une priorité absolue sur LinkedIn et une architecture modulaire permettant d'ajouter facilement n'importe quelle source tierce.

## Capabilities

- **CAP-1 (LinkedIn Deep Hunter)**
  - **intent:** Le système collecte à la fois les offres d'emploi officielles LinkedIn et les posts de réseau pertinents (publiés par des recruteurs, fondateurs, managers) via des requêtes ciblées et des filtres booléens.
  - **success:** Une recherche sur des mots-clés de poste extrait fidèlement les offres d'emploi actives ainsi que les posts mentionnant des recrutements récents (< 7 jours) avec le profil de l'auteur et le lien direct.

- **CAP-2 (Architecture de Connecteurs Enfichables - Multi-Sources)**
  - **intent:** Le système propose une abstraction unifiée `JobSourceProvider` permettant d'interroger et d'ajouter de nouvelles plateformes (Welcome to the Jungle, HelloWork, Glassdoor, JobbyGather, etc.) sans modifier le reste du système.
  - **success:** L'ajout d'une nouvelle source se résume à une classe dérivée avec une méthode d'extraction standardisée produisant des opportunités conformes au contrat de données commun.

- **CAP-3 (Normalisation & Déduplication Intelligente)**
  - **intent:** Le système convertit les données hétérogènes de toutes les sources vers une entité standard `JobOpportunity` et élimine les doublons entre plateformes.
  - **success:** Deux offres ou posts décrivant le même poste dans la même entreprise sur deux sources différentes sont identifiés et fusionnés sans perte d'information.

- **CAP-4 (Filtrage & Scoring Sémantique)**
  - **intent:** Le système filtre les opportunités selon les critères de recherche (technos, localisation, remote, type de contrat) et attribue un score de pertinence (0-100%) basé sur le profil du candidat.
  - **success:** Les opportunités non pertinentes sont filtrées ou déclassées, et chaque opportunité retenue dispose d'un score et d'un résumé court expliquant l'adéquation.

- **CAP-5 (Restitution & Alerting)**
  - **intent:** Le système stocke les opportunités dans une base locale légère (SQLite), génère des récapitulatifs (Markdown / JSON) et permet d'envoyer des alertes dès qu'une opportunité à fort score est détectée.
  - **success:** L'utilisateur consulte en local les opportunités collectées avec leur statut (Nouvelle, Archivée, Contactée) et peut exécuter des exports prêts à l'emploi.

## Constraints

- **Protection Anti-Bot & Sessions :** Pour LinkedIn en particulier, l'authentification et la collecte doivent fonctionner soit via session authentifiée persistée (cookies `li_at`), soit via navigateur headless discret (Playwright avec profils utilisateurs), avec temporisations aléatoires et respect strict des quotas pour éviter tout blocage de compte.
- **Robustesse du Parsing :** Le système doit être résilient aux variations de DOM des plateformes web cibles (sélecteurs tolérants, extraction JSON intégrée si présente).
- **Empreinte Légère & Local First :** L'agent doit pouvoir s'exécuter localement en Python (orchestré via `uv`), sans dépendance d'infrastructure cloud lourde obligatoire.

## Non-goals

- **Candidature automatique "mass-apply" sans validation humaine :** Jobby ne postule pas aveuglément à la place du candidat pour éviter les pénalités et les erreurs.
- **Spam ou démarchage automatisé non supervisé :** Aucun envoi de messages LinkedIn en masse sans relecture préalable.
- **Plateforme SaaS multi-tenant complexe en V1 :** La V1 est un outil personnel / local hautement efficace pour l'utilisateur.

## Success signal

L'utilisateur lance une commande simple (ex: `jobby search --keywords "Python AI" --remote`), et en moins de 2 minutes, Jobby produit un tableau de bord Markdown / console récapitulant les offres officielles et les posts LinkedIn récents correspondants, triés par pertinence, avec les coordonnées du contact ou l'URL directe du post.

## Assumptions

- L'utilisateur dispose d'un compte LinkedIn pouvant être utilisé pour la navigation et l'extraction des flux.
- Les autres sources (WTTJ, HelloWork, Glassdoor) peuvent être interrogées soit par leurs flux publics/endpoints de recherche non authentifiés, soit par scraping web léger.
- Python 3.10+ avec `uv` est disponible sur la machine locale.

## Open Questions

- Souhaitez-vous prioriser pour LinkedIn l'utilisation d'une session navigateur réelle (Playwright interactif où vous vous connectez une fois et la session est sauvegardée), ou l'injection directe d'un cookie de session (`li_at`) ?
- Quel canal d'alerte immédiat préférez-vous pour être prévenu (terminal + fichier markdown, webhook Discord, bot Telegram, ou email) ?
