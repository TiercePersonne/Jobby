@echo off
chcp 65001 > nul
title Jobby - Connexion LinkedIn 1-Clic
echo ========================================================
echo   Jobby - Assistant de Connexion LinkedIn 1-Clic
echo ========================================================
echo.
echo Ce script ouvre une fenetre dediee pour vous connecter.
echo Connectez-vous a votre compte LinkedIn secondaire.
echo Dès la connexion reussie, votre session sera transmise a Jobby.
echo.

if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe scripts\connect_linkedin.py %*
) else (
    python scripts\connect_linkedin.py %*
)

echo.
echo Appuyez sur une touche pour quitter...
pause > nul
