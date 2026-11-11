"""Authentication Assistant for WTTJ (Google/Gmail SSO) and LinkedIn."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, Optional

from jobby.config import load_config, save_config

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages persistent browser profiles and interactive SSO logins (Google/Gmail, LinkedIn)."""

    def __init__(self, profiles_dir: str = "_bmad-output/browser_profiles"):
        self.profiles_dir = Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def login_wttj_interactive(self, timeout_sec: int = 180) -> bool:
        """
        Opens a visible browser for Welcome to the Jungle.
        Allows the user to click 'Continuer avec Google' (Gmail SSO) or use their credentials.
        Detects authentication and extracts session cookies into jobby_config.yaml.
        """
        from playwright.sync_api import sync_playwright

        wttj_profile = self.profiles_dir / "wttj"
        wttj_profile.mkdir(parents=True, exist_ok=True)

        print("\n" + "=" * 65)
        print("🔗 ASSISTANT DE CONNEXION : WELCOME TO THE JUNGLE")
        print("=" * 65)
        print("1. Une fenêtre de navigateur va s'ouvrir.")
        print("2. Cliquez sur 'Continuer avec Google' pour vous connecter avec Gmail.")
        print("3. Dès que vous serez connecté à votre compte, Jobby sauvegardera votre session automatiquement.")
        print("=" * 65 + "\n")

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(wttj_profile),
                headless=False,
                channel="chrome",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--start-maximized",
                ],
                viewport=None,
            )

            page = context.new_page()
            page.goto("https://www.welcometothejungle.com/fr/signin")

            start_time = time.time()
            logged_in = False
            session_cookie_str = ""
            auth_token = ""

            while time.time() - start_time < timeout_sec:
                time.sleep(2)
                cookies = context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies if "welcometothejungle.com" in c["domain"]}

                # Check if logged in via cookies or URL
                current_url = page.url
                has_session = any(k in cookie_dict for k in ["wttj_session", "_wttj_token", "remember_user_token", "ahoy_visitor"])
                
                # Check for user avatar or account element on page
                is_dashboard = "/signin" not in current_url and ("welcometothejungle.com" in current_url)

                if has_session and is_dashboard:
                    # Construct cookie string
                    session_cookie_str = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])
                    auth_token = cookie_dict.get("_wttj_token", "") or cookie_dict.get("wttj_session", "")
                    logged_in = True
                    break

            context.close()

            if logged_in:
                config = load_config()
                config.wttj_session_cookie = session_cookie_str
                config.wttj_auth_token = auth_token
                save_config(config)
                print("\n[OK] Félicitations ! Votre compte Welcome to the Jungle (via Gmail) est connecté !")
                print("     Les cookies de session ont été enregistrés dans _bmad-output/jobby_config.yaml\n")
                return True
            else:
                print("\n[!] Temps écoulé ou connexion non finalisée.\n")
                return False

    def login_linkedin_interactive(self, timeout_sec: int = 180) -> bool:
        """
        Opens a visible browser for LinkedIn login.
        Detects login and automatically extracts the 'li_at' session cookie.
        """
        from playwright.sync_api import sync_playwright

        li_profile = self.profiles_dir / "linkedin"
        li_profile.mkdir(parents=True, exist_ok=True)

        print("\n" + "=" * 65)
        print("🔗 ASSISTANT DE CONNEXION : LINKEDIN")
        print("=" * 65)
        print("1. Une fenêtre de navigateur va s'ouvrir.")
        print("2. Connectez-vous à votre compte LinkedIn (mot de passe ou Google).")
        print("3. Dès que le fil d'actualité apparaît, Jobby extrait votre cookie 'li_at' automatiquement.")
        print("=" * 65 + "\n")

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(li_profile),
                headless=False,
                channel="chrome",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--start-maximized",
                ],
                viewport=None,
            )

            page = context.new_page()
            page.goto("https://www.linkedin.com/login")

            start_time = time.time()
            li_at = None

            while time.time() - start_time < timeout_sec:
                time.sleep(2)
                cookies = context.cookies()
                for c in cookies:
                    if c["name"] == "li_at" and "linkedin.com" in c["domain"]:
                        li_at = c["value"]
                        break
                if li_at:
                    break

            context.close()

            if li_at:
                config = load_config()
                config.linkedin_li_at = li_at
                save_config(config)
                print("\n[OK] Félicitations ! Votre compte LinkedIn est connecté !")
                print(f"     Cookie li_at sauvegardé ({li_at[:8]}...)\n")
                return True
            else:
                print("\n[!] Temps écoulé ou cookie li_at non détecté.\n")
                return False
