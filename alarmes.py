#!/usr/bin/env python3
"""Alarmes du relevé quotidien : questions de Fall fermées sans prévision du robot, et solde de la
clé OpenRouter. Lecture seule : rien n'est publié, aucun fichier n'est gardé.

Le dépôt est public, donc le journal aussi : on n'écrit que des nombres et des identifiants de
questions déjà fermées ; jamais la clé, jamais un autre champ de la réponse d'OpenRouter, jamais un
titre ni une prévision.

Sortie pour le job suivant (GITHUB_OUTPUT) : `alerte` (true ou false) et `texte`, une phrase
courte faite de chiffres et de libellés fixes. Le job qui écrit l'issue ne reçoit que cela.

Usage (variables d'environnement) :
    METACULUS_TOKEN      jeton du compte bot (obligatoire pour la partie Metaculus)
    OPENROUTER_API_KEY   clé du bot (facultative ; sans elle, pas de contrôle du solde)
    DERNIER_RELEVE       instant du dernier relevé réussi, ISO 8601 UTC (facultatif ; 24 h sinon)
    FALL_TOURNAMENT      identifiant du tournoi Fall (facultatif, 33121 par défaut)
    SEUIL_OPENROUTER     solde en dollars sous lequel on alerte (facultatif, 2 par défaut)
    ESSAI_ALERTE         « true » force l'alerte, pour essayer le job qui écrit l'issue
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import api, list_posts  # noqa: E402

FALL = os.environ.get("FALL_TOURNAMENT", "33121")
SEUIL = float(os.environ.get("SEUIL_OPENROUTER", "2") or 2)


def solde_openrouter(key):
    """Champ limit_remaining de GET /api/v1/key, ou None. En cas d'erreur, seul le code HTTP est
    écrit (le corps de la réponse peut contenir des informations sur la clé)."""
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": "tingale-bot/alarmes (github.com/cajyl/tingale-bot)",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"OpenRouter : HTTP {e.code}")
        return None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"OpenRouter : lecture impossible ({type(e).__name__})")
        return None
    value = (data.get("data") or {}).get("limit_remaining") if isinstance(data, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def depuis():
    """Début de la fenêtre : dernier relevé réussi, sinon il y a 24 heures."""
    raw = os.environ.get("DERNIER_RELEVE", "").strip()
    if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?Z", raw):
        return datetime.strptime(raw[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timedelta(hours=24)


def main():
    raisons = []

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        solde = solde_openrouter(key)
        if solde is None:
            print("Solde OpenRouter : non lu")
        else:
            print(f"Solde OpenRouter : {solde:.2f} $ (seuil d'alerte {SEUIL:.2f} $)")
            if solde < SEUIL:
                raisons.append(f"solde OpenRouter {solde:.2f} $, sous {SEUIL:.2f} $")
    else:
        print("OPENROUTER_API_KEY absente : solde non contrôlé.")

    token = os.environ.get("METACULUS_TOKEN", "").strip()
    if token:
        start = depuis()
        borne = start.strftime("%Y-%m-%d %H:%M")
        my_id = api("/users/me/", token=token)["id"]
        fermees = list_posts(token, FALL, ["closed", "resolved"])
        prevues = {p["id"] for p in list_posts(token, FALL, ["closed", "resolved"], forecaster_id=my_id)}
        recentes = [p for p in fermees if p["close_dt"] >= borne]
        manquees = sorted(p["id"] for p in recentes if p["id"] not in prevues)
        print(f"Fall : {len(recentes)} questions fermées depuis {borne} UTC, dont {len(manquees)} sans prévision du robot")
        if manquees:
            print("  sans prévision : " + ", ".join(str(i) for i in manquees))
            raisons.append(f"{len(manquees)} question(s) de Fall fermée(s) sans prévision du robot depuis {borne} UTC "
                           f"(posts {', '.join(str(i) for i in manquees[:20])})")
    else:
        print("METACULUS_TOKEN manquant : couverture non contrôlée.")

    if os.environ.get("ESSAI_ALERTE", "").strip().lower() == "true":
        raisons.append("essai de l'alarme, rien à faire")

    texte = " ; ".join(raisons)
    # Le texte ne contient que des chiffres et des libellés fixes ; on le borne quand même à un jeu
    # de caractères sûr et à une seule ligne avant de le transmettre.
    texte = re.sub(r"[^0-9A-Za-zÀ-ÿ $.,;:()'/_-]", " ", texte)[:500]
    print("Alerte : " + ("oui" if raisons else "non"))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"alerte={'true' if raisons else 'false'}\n")
            f.write(f"texte={texte}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        print(f"Erreur HTTP {e.code} sur l'API Metaculus", file=sys.stderr)
        sys.exit(1)
