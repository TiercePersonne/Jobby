"""Jobby - Assistant de connexion LinkedIn 1-Clic pour PC Windows.

Ouvre une fenêtre de navigation Chromium dédiée pour vous connecter à LinkedIn
(idéalement avec un compte secondaire). Dès que l'authentification réussit,
le cookie de session `li_at` est extrait et transmis à votre instance Jobby.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
import json
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description="Jobby LinkedIn Connection Assistant")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="URL du serveur Jobby (défaut: http://127.0.0.1:8000)")
    args = parser.parse_args()

    server_url = args.server.rstrip("/")
    print("=" * 60)
    print("  Jobby - Assistant de Connexion LinkedIn (PC Windows)")
    print("=" * 60)
    print(f"Serveur cible : {server_url}")
    print("Ouverture du navigateur Chrome...")
    print("Connectez-vous à votre compte LinkedIn dans la fenêtre qui s'affiche.")
    print("Dès que vous serez connecté, la session sera synchronisée automatiquement.")
    print("-" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        li_at_cookie = None
        max_wait_seconds = 300  # 5 minutes
        start_time = time.time()

        while time.time() - start_time < max_wait_seconds:
            try:
                cookies = context.cookies()
                for c in cookies:
                    if c.get("name") == "li_at" and c.get("value"):
                        li_at_cookie = c.get("value")
                        break
            except Exception:
                pass

            if li_at_cookie:
                break
            time.sleep(1.0)

        if not li_at_cookie:
            print("[ATTENTION] Délai d'attente dépassé ou cookie li_at non détecté.")
            browser.close()
            return

        print("\n[INFO] Cookie de session li_at détecté !")
        print(f"[INFO] Envoi au serveur Jobby ({server_url}/api/auth/linkedin-cookie)...")

        try:
            req = urllib.request.Request(
                f"{server_url}/api/auth/linkedin-cookie",
                data=json.dumps({"li_at": li_at_cookie}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201):
                    print("\n" + "=" * 60)
                    print("  SUCCÈS : Connexion LinkedIn synchronisée avec succès !")
                    print("  Les Posts Réseau sont désormais débloqués dans votre flux.")
                    print("=" * 60)
                else:
                    print(f"[ERREUR] Le serveur a répondu avec le statut HTTP {resp.status}")
        except Exception as e:
            print(f"[ERREUR] Impossible de joindre le serveur Jobby : {e}")
            print(f"Vous pouvez copier votre cookie manuellement : {li_at_cookie[:12]}...")

        time.sleep(2.5)
        browser.close()


if __name__ == "__main__":
    main()
