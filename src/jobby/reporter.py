"""Markdown and digest generator for Jobby."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List

from jobby.models import JobOpportunity, OpportunityType


class Reporter:
    """Generates formatted Markdown digests and summaries."""

    @staticmethod
    def generate_markdown_digest(
        opportunities: List[JobOpportunity],
        query_desc: str = "Recherche multi-sources",
        output_path: str | Path = "_bmad-output/latest_digest.md"
    ) -> str:
        now_str = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        
        # Partition opportunities
        posts = [o for o in opportunities if o.opportunity_type == OpportunityType.POST]
        offers = [o for o in opportunities if o.opportunity_type != OpportunityType.POST]
        top_matches = [o for o in opportunities if (o.score or 0) >= 70.0]

        md: List[str] = [
            f"# 🎯 Jobby Digest — {now_str}",
            f"> **Contexte de recherche :** {query_desc}",
            f"> **Total opportunités :** {len(opportunities)} ({len(offers)} offres, {len(posts)} posts réseau)",
            "",
            "---",
            "",
        ]

        # Top matches section
        if top_matches:
            md.append("## 🌟 Opportunités Recommandées (Score ≥ 70%)")
            md.append("")
            for opp in top_matches[:10]:
                type_badge = "📢 Post Réseau" if opp.opportunity_type == OpportunityType.POST else "🏢 Offre"
                score_badge = f"`Score: {opp.score}%`" if opp.score is not None else ""
                md.append(f"### [{opp.title}]({opp.url}) {score_badge}")
                
                meta_parts = [f"**Source:** {opp.source.upper()} ({type_badge})"]
                if opp.company:
                    meta_parts.append(f"**Entreprise:** {opp.company}")
                if opp.location:
                    meta_parts.append(f"**Lieu:** {opp.location}")
                if opp.salary_raw:
                    meta_parts.append(f"**Rémunération:** {opp.salary_raw}")
                md.append(" | ".join(meta_parts))

                if opp.author_name:
                    author_link = f"[{opp.author_name}]({opp.author_profile_url})" if opp.author_profile_url else opp.author_name
                    headline = f" ({opp.author_title})" if opp.author_title else ""
                    md.append(f"- 👤 **Contact direct:** {author_link}{headline}")

                if opp.score_reasons:
                    md.append(f"- 💡 **Pourquoi ce match:** {', '.join(opp.score_reasons)}")

                if opp.description:
                    snippet = opp.description.replace("\n", " ").strip()[:240]
                    md.append(f"- 📝 *{snippet}...*")
                md.append("")

        # LinkedIn Posts section
        if posts:
            md.append("## 📢 Publications & Posts de Recruteurs (LinkedIn)")
            md.append("")
            for post in posts[:15]:
                md.append(f"#### [{post.title}]({post.url})")
                if post.author_name:
                    md.append(f"- **Auteur:** {post.author_name} ({post.author_title or 'Post LinkedIn'})")
                if post.description:
                    snippet = post.description.replace("\n", " ").strip()[:300]
                    md.append(f"- **Contenu:** {snippet}...")
                md.append(f"- 🔗 [Voir le post original]({post.url})")
                md.append("")

        # All job board listings table
        md.append("## 📋 Toutes les opportunités capturées")
        md.append("")
        md.append("| Score | Source | Type | Titre | Entreprise | Localisation | Lien |")
        md.append("| :---: | :---: | :---: | :--- | :--- | :--- | :--- |")

        for opp in opportunities[:40]:
            score_str = f"**{int(opp.score)}%**" if opp.score is not None else "-"
            type_str = "Post" if opp.opportunity_type == OpportunityType.POST else "Offre"
            title_clean = opp.title.replace("|", "-")[:45]
            company_clean = (opp.company or "-").replace("|", "-")[:25]
            loc_clean = (opp.location or "-").replace("|", "-")[:20]
            md.append(f"| {score_str} | {opp.source} | {type_str} | {title_clean} | {company_clean} | {loc_clean} | [Lien]({opp.url}) |")

        md_content = "\n".join(md) + "\n"

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return md_content
