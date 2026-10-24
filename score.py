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
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.metaculus.com/api"
PAUSE = 1.0  # secondes entre deux appels
DEFAULT_TOURNAMENTS = "33121:Fall FutureEval 2026,minibench:MiniBench"
STATUSES = ["open", "closed", "resolved", "upcoming"]
BOT_SINCE = os.environ.get("BOT_SINCE", "2026-10-20")  # entrée du bot en tournoi : mesure de couverture


def clean(value, limit=90):
    """Neutralise une chaîne venue de l'API avant de l'écrire dans le journal du job ou le résumé
    Markdown : caractères de contrôle et séparateurs de ligne remplacés (une ligne qui commence par
    « :: » serait une commande de workflow), suites de deux-points réduites à un seul, barre
    verticale et accent grave remplacés (tableau et code Markdown). Les remplacements sont faits
    après la troncature, pour qu'elle ne puisse pas faire réapparaître une séquence interdite."""
    text = str(value)[: limit * 2]
    text = re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", " ", text)
    text = re.sub(r":{2,}", ":", text).replace("|", "/").replace("`", "'").strip()[:limit]
    text = re.sub(r":{2,}", ":", text)
    assert "::" not in text
    return text


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


def list_posts(token, tournament, statuses, forecaster_id=None):
    """Posts du tournoi pour les statuts donnés (id, titre, ouverture, fermeture), ou seulement
    ceux prévus par forecaster_id. Sert au compte et à la couverture (ouvertes non prévues)."""
    params = {"tournaments": tournament, "statuses": statuses, "limit": 100,
              "with_cp": "false", "offset": 0}
    if forecaster_id:
        params["forecaster_id"] = forecaster_id
    posts = []
    while True:
        data = api("/posts/", params, token)
        results = data.get("results", [])
        for p in results:
            q = p.get("question") or {}
            kind = (f"question:{q.get('type')}" if q else "groupe" if p.get("group_of_questions")
                    else "conditionnelle" if p.get("conditional") else "carnet" if p.get("notebook") else "?")
            open_dt = clean(p.get("open_time") or q.get("open_time") or p.get("published_at") or "", 32)
            close_dt = clean(q.get("actual_close_time") or p.get("scheduled_close_time") or q.get("scheduled_close_time") or "", 32)
            posts.append({
                "id": p["id"],
                "title": clean(p.get("title") or q.get("title") or ""),
                "open": open_dt[:10], "close": close_dt[:10],
                "open_dt": open_dt[:16].replace("T", " "), "close_dt": close_dt[:16].replace("T", " "),
                "kind": clean(kind, 40), "status": clean(p.get("status") or q.get("status") or "?", 20),
            })
        if not data.get("next") or not results:
            break
        params["offset"] += len(results)
    return posts


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
    # Forme qui répond (vérifié le 23/10) : /leaderboards/project/<id>/ → {score_type, prize_pool,
    # finalized, entries: [...]}. Les autres restent en secours si l'API change.
    candidates = [f"/leaderboards/project/{pid}/", f"/projects/{pid}/leaderboard/"] if pid else []
    candidates.append(f"/projects/tournaments/{tournament}/leaderboard/")
    for path in candidates:
        try:
            data = api(path, token=token)
        except urllib.error.HTTPError as e:
            print(f"  ({path} → HTTP {e.code})")
            continue
        # /leaderboards/project/<id>/ renvoie une LISTE de classements (plusieurs barèmes possibles) :
        # on prend le classement principal.
        if isinstance(data, list) and data and isinstance(data[0], dict) and "entries" in data[0]:
            data = next((b for b in data if b.get("is_primary_leaderboard")), data[0])
        if isinstance(data, dict):
            entries = data.get("entries") or data.get("leaderboard") or data.get("results") or []
            score_type = data.get("score_type")
            print(f"  (classement {path} : type {score_type}, dotation {data.get('prize_pool')}, "
                  f"finalisé {data.get('finalized')}, {len(entries)} entrées)")
        else:
            entries, score_type = data, None
        if entries:
            print(f"  (champs d'une entrée : {sorted(entries[0].keys())})")
            return score_type, entries
        if isinstance(data, dict) and "entries" in data:
            return score_type, []  # classement trouvé mais encore vide (aucune question résolue)
        print(f"  ({path} → forme inattendue : {sorted(data.keys()) if isinstance(data, dict) else type(data).__name__})")
    return None, []


def main():
    token = os.environ.get("METACULUS_TOKEN", "").strip()
    if not token:
        print("METACULUS_TOKEN manquant : rien à faire.")
        return 0
    me = api("/users/me/", token=token)
    my_id, my_name = me["id"], me.get("username", "?")
    print(f"Compte : {my_name} (id {my_id})\n")

    lines = [f"## Score du bot `{my_name}`", "", "| Tournoi | Questions | Prévues par le bot | Ouvertes | Ouvertes non prévues | Depuis l'entrée du bot | Rang | Score |",
             "|---|---|---|---|---|---|---|---|"]
    spec = os.environ.get("TOURNAMENTS", DEFAULT_TOURNAMENTS)
    for item in spec.split(","):
        key, _, label = item.strip().partition(":")
        label = label or key
        try:
            all_posts = list_posts(token, key, STATUSES)
            my_posts = list_posts(token, key, STATUSES, forecaster_id=my_id)
            open_posts = list_posts(token, key, ["open"])
            score_type, entries = leaderboard(token, key)
        except urllib.error.HTTPError as e:
            print(f"{label} : erreur HTTP {e.code} sur {e.url}")
            lines.append(f"| {label} | erreur HTTP {e.code} | | | | | | |")
            continue
        total, mine = len(all_posts), len(my_posts)
        my_ids = {p["id"] for p in my_posts}
        # Couverture : ce que le bot n'a pas prévu parmi les questions encore ouvertes, et la part
        # des questions ouvertes depuis son entrée qu'il a effectivement prévues.
        open_missing = sorted((p for p in open_posts if p["id"] not in my_ids), key=lambda p: p["close"])
        since = [p for p in all_posts if p["open"] >= BOT_SINCE]
        since_mine = [p for p in since if p["id"] in my_ids]
        since_missing = sorted((p for p in since if p["id"] not in my_ids), key=lambda p: p["open_dt"])
        rank, score, n = "—", "—", len(entries)

        def who(e):
            """Nom et id de l'entrée, quelle que soit la forme (user imbriqué ou champs à plat)."""
            u = e.get("user") if isinstance(e.get("user"), dict) else {}
            return (clean(u.get("username") or e.get("username") or e.get("aggregation_method") or "?", 40),
                    u.get("id") or e.get("user_id"))

        for i, e in enumerate(entries, 1):
            name, uid = who(e)
            if uid == my_id or name == my_name:
                rank = f"{e.get('rank', i)}/{n}"
                s = e.get("score")
                score = f"{s:.2f}" if isinstance(s, (int, float)) else str(s)
                break
        print(f"{label} : {total} questions, {mine} prévues par le bot, rang {rank}, score {score}"
              + (f" ({score_type})" if score_type else "") + (f" ; classement : {n} entrées" if n else " ; classement non lisible"))
        if my_ids:
            print("  posts prévus : " + ", ".join(str(i) for i in sorted(my_ids)))
        print(f"  couverture : {len(open_posts)} ouvertes, {len(open_posts) - len(open_missing)} prévues, "
              f"{len(open_missing)} non prévues ; ouvertes depuis le {BOT_SINCE} : {len(since)}, dont {len(since_mine)} prévues")
        for p in open_missing:
            print(f"    non prévue : {p['id']}  ouverte {p['open']}  ferme {p['close']}  {p['title']}")
        for p in since_missing:
            print(f"    manquée depuis l'entrée du bot : {p['id']}  {p['kind']}  {p['status']}  "
                  f"ouverte {p['open_dt']}  fermée {p['close_dt']}  {p['title']}")
        if entries:
            print("  tête de classement : " + " ; ".join(
                f"{who(e)[0]} {e.get('score', '')}" for e in entries[:5]))
        lines.append(f"| {label} | {total} | {mine} | {len(open_posts)} | {len(open_missing)} | "
                     f"{len(since_mine)}/{len(since)} | {rank} | {score} |")
        for p in open_missing:
            lines.append(f"- non prévue : [{p['id']}](https://www.metaculus.com/questions/{p['id']}/) ferme {p['close']} · {p['title']}")
        for p in since_missing:
            lines.append(f"- manquée depuis l'entrée du bot : [{p['id']}](https://www.metaculus.com/questions/{p['id']}/) "
                         f"{p['kind']} · {p['status']} · ouverte {p['open_dt']} · fermée {p['close_dt']} · {p['title']}")

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
