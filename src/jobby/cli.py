"""CLI interface for Jobby using Typer and Rich."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import List, Optional
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from jobby.config import load_config
from jobby.engine import DeduplicationEngine, ScoringEngine
from jobby.models import OpportunityType, SearchParams
from jobby.providers import registry
from jobby.reporter import Reporter
from jobby.storage import Storage

# Configure console with safe encoding for Windows terminal
console = Console(safe_box=True)

app = typer.Typer(
    name="jobby",
    help="Jobby — Agent intelligent multi-sources de recherche d'emploi et posts LinkedIn",
    add_completion=False,
)


@app.command("search")
def search(
    keywords: List[str] = typer.Option(
        None,
        "--keyword",
        "-k",
        help="Mots-clés de recherche (ex: -k python -k fastapi)",
    ),
    location: Optional[str] = typer.Option(
        None,
        "--location",
        "-l",
        help="Localisation cible (ex: Paris, France)",
    ),
    remote: bool = typer.Option(
        False,
        "--remote",
        "-r",
        help="Filtrer uniquement les postes 100% télétravail",
    ),
    sources: Optional[str] = typer.Option(
        None,
        "--sources",
        "-s",
        help="Sources à interroger séparées par des virgules (ex: linkedin,hellowork,wttj)",
    ),
    days: int = typer.Option(
        7,
        "--days",
        "-d",
        help="Fraîcheur des offres en jours",
    ),
    max_results: int = typer.Option(
        25,
        "--max",
        "-m",
        help="Nombre maximum de résultats par source",
    ),
    posts: bool = typer.Option(
        True,
        "--posts/--no-posts",
        help="Inclure les publications et posts informels de recruteurs (LinkedIn)",
    ),
    export_digest: bool = typer.Option(
        True,
        "--export/--no-export",
        help="Générer un digest Markdown",
    ),
) -> None:
    """Lance une recherche d'emploi multi-sources avec déduplication et scoring."""
    config = load_config()
    storage = Storage(config.database_path)
    scoring = ScoringEngine(config)

    search_keywords = keywords if keywords else config.target_roles
    target_location = location if location else (config.preferred_locations[0] if config.preferred_locations else "France")

    console.print(
        Panel.fit(
            f"[bold cyan]Jobby Hunt[/bold cyan] — Recherche en cours...\n"
            f"[dim]Mots-clés :[/dim] [yellow]{', '.join(search_keywords)}[/yellow] | "
            f"[dim]Lieu :[/dim] [green]{target_location}[/green] | "
            f"[dim]Remote :[/dim] [magenta]{'Oui' if remote else 'Tous'}[/magenta]",
            border_style="cyan",
        )
    )

    params = SearchParams(
        keywords=search_keywords,
        location=target_location,
        remote_only=remote,
        max_results=max_results,
        posted_within_days=days,
        include_posts=posts,
    )

    source_list = [s.strip().lower() for s in sources.split(",")] if sources else None

    # Run async search across all providers
    with console.status("[bold green]Collecte des offres et posts reseau...[/bold green]"):
        raw_opportunities = asyncio.run(
            registry.search_all(params, source_names=source_list)
        )

    # Deduplicate
    deduped = DeduplicationEngine.deduplicate(raw_opportunities)

    # Score
    scored = scoring.evaluate_all(deduped)

    # Save to SQLite
    new_count = 0
    for opp in scored:
        is_new, _ = storage.upsert_opportunity(opp)
        if is_new:
            new_count += 1

    # Render results table
    table = Table(
        title=f"Resultats Jobby ({len(scored)} trouves, {new_count} nouveaux)",
        show_header=True,
        header_style="bold magenta",
        safe_box=True,
    )
    table.add_column("Score", justify="center", width=7)
    table.add_column("Source", width=11)
    table.add_column("Type", width=7)
    table.add_column("Titre / Sujet", width=36)
    table.add_column("Entreprise / Auteur", width=22)
    table.add_column("Lieu", width=18)
    table.add_column("URL", width=35)

    for opp in scored[:35]:
        score_val = opp.score or 0.0
        if score_val >= 75:
            score_text = f"[bold green]{int(score_val)}%[/bold green]"
        elif score_val >= 50:
            score_text = f"[yellow]{int(score_val)}%[/yellow]"
        else:
            score_text = f"[dim]{int(score_val)}%[/dim]"

        type_text = "[cyan]Post[/cyan]" if opp.opportunity_type == OpportunityType.POST else "Offre"
        company_or_author = opp.author_name or opp.company or "-"
        loc_display = opp.location or "-"

        table.add_row(
            score_text,
            opp.source.upper(),
            type_text,
            opp.title[:34],
            company_or_author[:20],
            loc_display[:16],
            opp.url,
        )

    console.print(table)

    # Export markdown digest if enabled
    if export_digest:
        digest_file = config.digest_output_path
        query_desc = f"{', '.join(search_keywords)} ({target_location})"
        Reporter.generate_markdown_digest(scored, query_desc=query_desc, output_path=digest_file)
        console.print(f"\n[bold green][OK][/bold green] Digest complet exporte dans : [underline cyan]{digest_file}[/underline cyan]")


@app.command("list")
def list_saved(
    status: Optional[str] = typer.Option(None, "--status", help="Filtrer par statut: new, saved, applied, archived"),
    source: Optional[str] = typer.Option(None, "--source", help="Filtrer par source: linkedin, hellowork, wttj"),
    limit: int = typer.Option(20, "--limit", "-n", help="Nombre d'opportunités à afficher"),
) -> None:
    """Affiche les opportunités sauvegardées dans la base de données locale."""
    config = load_config()
    storage = Storage(config.database_path)
    items = storage.list_opportunities(status=status, source=source, limit=limit)

    if not items:
        console.print("[yellow]Aucune opportunite trouvee avec ces criteres.[/yellow]")
        return

    table = Table(title=f"Opportunites enregistrees ({len(items)})", header_style="bold cyan", safe_box=True)
    table.add_column("ID", width=16)
    table.add_column("Statut", width=10)
    table.add_column("Score", justify="center", width=7)
    table.add_column("Source", width=10)
    table.add_column("Titre", width=35)
    table.add_column("Entreprise", width=20)
    table.add_column("URL")

    for item in items:
        score_val = f"{int(item.score)}%" if item.score is not None else "-"
        table.add_row(
            item.id,
            item.status,
            score_val,
            item.source,
            item.title[:33],
            item.company or "-",
            item.url,
        )

    console.print(table)


@app.command("status")
def update_status(
    opportunity_id: str = typer.Argument(..., help="Identifiant de l'opportunité"),
    new_status: str = typer.Argument(..., help="Nouveau statut (new, saved, applied, rejected, archived)"),
) -> None:
    """Met à jour le statut d'une opportunité."""
    config = load_config()
    storage = Storage(config.database_path)
    success = storage.update_status(opportunity_id, new_status)
    if success:
        console.print(f"[green][OK] Opportunite {opportunity_id} mise a jour vers '{new_status}'[/green]")
    else:
        console.print(f"[red][X] Impossible de trouver l'opportunite {opportunity_id}[/red]")


@app.command("stats")
def show_stats() -> None:
    """Affiche les statistiques globales de Jobby."""
    config = load_config()
    storage = Storage(config.database_path)
    stats = storage.get_stats()

    console.print(Panel(
        f"[bold]Total opportunites suivies :[/bold] [cyan]{stats['total']}[/cyan]\n\n"
        f"[bold]Par source :[/bold] {stats['by_source']}\n"
        f"[bold]Par type :[/bold] {stats['by_type']}\n"
        f"[bold]Par statut :[/bold] {stats['by_status']}",
        title="Statistiques Jobby",
        border_style="green",
        safe_box=True
    ))


@app.command("providers")
def show_providers() -> None:
    """Liste les fournisseurs (providers) de jobs connectés."""
    providers = registry.list_providers()
    table = Table(title="Connecteurs Actifs", header_style="bold blue", safe_box=True)
    table.add_column("Code", width=12)
    table.add_column("Nom Affiche", width=28)
    table.add_column("Posts Reseau ?", justify="center", width=15)
    table.add_column("Statut", justify="center", width=10)

    for p in providers:
        supports = "[green]Oui[/green]" if p["supports_posts"] else "[dim]Non[/dim]"
        status = "[green]Active[/green]" if p["enabled"] else "[red]Desactive[/red]"
        table.add_row(p["name"], p["display_name"], supports, status)

    console.print(table)


@app.command("login")
def login(
    service: str = typer.Argument("wttj", help="Service à connecter : 'wttj' (Welcome to the Jungle via Gmail SSO) ou 'linkedin'"),
) -> None:
    """Ouvre un navigateur sécurisé pour connecter votre compte via Google/Gmail ou mot de passe."""
    from jobby.auth import AuthManager
    auth = AuthManager()
    if service.lower() == "wttj":
        auth.login_wttj_interactive()
    elif service.lower() == "linkedin":
        auth.login_linkedin_interactive()
    else:
        console.print(f"[red]Service inconnu : {service}. Choisissez 'wttj' ou 'linkedin'.[/red]")


@app.command("ui")
def start_ui(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Adresse IP d'écoute"),
    port: int = typer.Option(8000, "--port", "-p", help="Port d'écoute"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Ne pas ouvrir automatiquement le navigateur"),
) -> None:
    """Lance le serveur web PWA de Jobby et ouvre l'interface dans le navigateur."""
    import threading
    import webbrowser
    import uvicorn

    url = f"http://{host}:{port}"
    console.print(Panel(
        f"[bold green]Application Jobby PWA disponible ![/bold green]\n\n"
        f"[dim]Interface :[/dim] [underline cyan]{url}[/underline cyan]\n"
        f"[dim]PWA Manifest :[/dim] [cyan]{url}/manifest.json[/cyan]\n"
        f"[dim]Service Worker :[/dim] [cyan]{url}/sw.js[/cyan]\n\n"
        f"[dim]Appuyez sur Ctrl+C pour arreter le serveur.[/dim]",
        title="Jobby Web Dashboard (PWA)",
        border_style="cyan",
        safe_box=True,
    ))

    if not no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run("jobby.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
