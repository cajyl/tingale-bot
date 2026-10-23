#!/usr/bin/env python3
"""Score du bot : nombre de prévisions publiées et classement, lus via l'API Metaculus.

Pourquoi ce script : depuis le cloud, l'API Metaculus répond 403 sans jeton ; dans GitHub
Actions, le secret METACULUS_TOKEN est disponible, donc le point hebdomadaire peut lire le
score sans le sponsor. Lecture seule : aucune prévision n'est publiée ici.

Usage (variables d'environnement) :
    METACULUS_TOKEN   jeton du compte bot (obligatoire)
    TOURNAMENTS       liste "id-ou-slug:libellé" séparés par des virgules (facultatif)

Sortie : texte lisible dans le journal du job, et un bloc Markdown dans GITHUB_STEP_SUMMARY
si cette variable existe. Dépendances : bibliothèque standard uniquement.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.metaculus.com/api"
PAUSE = 1.0  # secondes entre deux appels
DEFAULT_TOURNAMENTS = "33121:Fall FutureEval 2026,minibench:MiniBench"
STATUSES = ["open", "closed", "resolved", "upcoming"]


def api(path, params=None, token=None):
    """GET sur l'API ; les listes de paramètres sont répétées (statuses=open&statuses=...).

    L'API limite le débit (HTTP 429 observé au premier essai, 23/10) : pause entre deux appels
    et reprise avec attente croissante.
    """
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{BASE}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Token {token}",
        "User-Agent": "tingale-bot/score (github.com/cajyl/tingale-bot)",
        "Accept": "application/json",
    })
    for attempt, wait in enumerate((0, 10, 30, 60)):
        time.sleep(wait or PAUSE)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            print(f"  (429 sur {path}, nouvel essai dans {(10, 30, 60)[attempt]} s)")


def count_posts(token, tournament, forecaster_id=None):
    """Nombre de posts du tournoi (tous statuts), ou de ceux prévus par forecaster_id."""
    params = {"tournaments": tournament, "statuses": STATUSES, "limit": 100,
              "with_cp": "false", "offset": 0}
    if forecaster_id:
        params["forecaster_id"] = forecaster_id
    total, ids = None, []
    while True:
        data = api("/posts/", params, token)
        if total is None:
            total = data.get("count")
        results = data.get("results", [])
        ids.extend(p["id"] for p in results)
        if not data.get("next") or not results:
            break
        params["offset"] += len(results)
    return (total if total is not None else len(ids)), ids


def project_id(token, tournament):
    """Identifiant numérique du projet (un slug comme « minibench » doit être résolu)."""
    if str(tournament).isdigit():
        return int(tournament)
    try:
        data = api(f"/projects/tournaments/{tournament}/", token=token)
        return int(data["id"])
    except (urllib.error.HTTPError, KeyError, ValueError) as e:
        print(f"  (projet {tournament} : {e})")
        return None


def leaderboard(token, tournament):
    """Classement du tournoi. L'URL n'est pas documentée : on essaie plusieurs formes, dans
    l'ordre, et on consigne le code HTTP de chacune pour apprendre d'un passage à l'autre."""
    pid = project_id(token, tournament)
    candidates = [f"/projects/tournaments/{tournament}/leaderboard/"]
    if pid:
        candidates += [f"/leaderboards/project/{pid}/", f"/projects/{pid}/leaderboard/",
                       f"/leaderboards/?project_id={pid}"]
    for path in candidates:
        try:
            data = api(path, token=token)
        except urllib.error.HTTPError as e:
            print(f"  ({path} → HTTP {e.code})")
            continue
        if isinstance(data, dict):
            entries = data.get("entries") or data.get("leaderboard") or data.get("results") or []
            score_type = data.get("score_type")
        else:
            entries, score_type = data, None
        if entries:
            print(f"  (classement lu sur {path} ; champs : {sorted(entries[0].keys())})")
            return score_type, entries
        print(f"  ({path} → vide ; clés : {sorted(data.keys()) if isinstance(data, dict) else type(data).__name__})")
    return None, []


def main():
    token = os.environ.get("METACULUS_TOKEN", "").strip()
    if not token:
        print("METACULUS_TOKEN manquant : rien à faire.")
        return 0
    me = api("/users/me/", token=token)
    my_id, my_name = me["id"], me.get("username", "?")
    print(f"Compte : {my_name} (id {my_id})\n")

    lines = [f"## Score du bot `{my_name}`", "", "| Tournoi | Questions | Prévues par le bot | Rang | Score |", "|---|---|---|---|---|"]
    spec = os.environ.get("TOURNAMENTS", DEFAULT_TOURNAMENTS)
    for item in spec.split(","):
        key, _, label = item.strip().partition(":")
        label = label or key
        try:
            total, _ = count_posts(token, key)
            mine, my_ids = count_posts(token, key, forecaster_id=my_id)
            score_type, entries = leaderboard(token, key)
        except urllib.error.HTTPError as e:
            print(f"{label} : erreur HTTP {e.code} sur {e.url}")
            lines.append(f"| {label} | erreur HTTP {e.code} | | | |")
            continue
        rank, score, n = "—", "—", len(entries)
        for i, e in enumerate(entries, 1):
            user = e.get("user") or {}
            if user.get("id") == my_id or user.get("username") == my_name:
                rank = f"{e.get('rank', i)}/{n}"
                s = e.get("score")
                score = f"{s:.2f}" if isinstance(s, (int, float)) else str(s)
                break
        print(f"{label} : {total} questions, {mine} prévues par le bot, rang {rank}, score {score}"
              + (f" ({score_type})" if score_type else "") + (f" ; classement : {n} entrées" if n else " ; classement non lisible"))
        if my_ids:
            print("  posts prévus : " + ", ".join(str(i) for i in sorted(my_ids)))
        if entries:
            print("  tête de classement : " + " ; ".join(
                f"{(e.get('user') or {}).get('username', '?')} {e.get('score', '')}" for e in entries[:5]))
        lines.append(f"| {label} | {total} | {mine} | {rank} | {score} |")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        print(f"Erreur HTTP {e.code} sur {e.url}", file=sys.stderr)
        sys.exit(1)
