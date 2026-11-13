#!/usr/bin/env python3
"""Dates des sous-questions de Market Pulse : ouverture, clôture prévue, instant du score « spot »,
révélation de la prévision communautaire, et heures des prévisions du robot. Lecture seule.

Pourquoi : savoir si le robot prévoit chaque sous-question une seule fois, combien de temps avant
l'instant de notation, et si une prévision refaite juste avant la clôture compterait.

Le dépôt est public, donc son journal aussi, et certaines questions sont ouvertes : on n'écrit QUE
des identifiants, des états et des dates. Jamais la valeur d'une prévision, jamais un titre, jamais
un commentaire (ils ne sont pas lus). Aucun fichier n'est conservé.

Usage (variables d'environnement) :
    METACULUS_TOKEN   jeton du compte bot (obligatoire)
    TOURNAMENT        slug ou id du tournoi (facultatif, market-pulse-26q4 par défaut)
"""

import os
import sys
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import api, clean  # noqa: E402

TOURNAMENT = os.environ.get("TOURNAMENT", "market-pulse-26q4")
STATUSES = ["upcoming", "open", "closed", "resolved"]
# Champs de date cherchés sur la question ; l'instant du score « spot » peut porter plusieurs noms.
SPOT_KEYS = ("spot_scoring_time", "spot_scoring_at", "cp_reveal_time")


def posts(token, my_id=None):
    params = {"tournaments": TOURNAMENT, "statuses": STATUSES, "limit": 100, "offset": 0, "with_cp": "false"}
    if my_id:
        params["forecaster_id"] = my_id
    out = []
    while True:
        data = api("/posts/", params, token)
        results = data.get("results", [])
        out.extend(results)
        if not data.get("next") or not results:
            return out
        params["offset"] += len(results)


def questions_of(post):
    if post.get("question"):
        return [post["question"]]
    return (post.get("group_of_questions") or {}).get("questions") or []


def to_dt(value):
    """Datetime UTC depuis une chaîne ISO ou un horodatage, sinon None."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def fmt(value):
    d = to_dt(value)
    return d.strftime("%Y-%m-%d %H:%M") if d else "-"


def main():
    token = os.environ.get("METACULUS_TOKEN", "").strip()
    if not token:
        print("METACULUS_TOKEN manquant : rien à faire.")
        return 0
    my_id = api("/users/me/", token=token)["id"]
    every = posts(token)
    mine = {p["id"] for p in posts(token, my_id)}
    print(f"{TOURNAMENT} : {len(every)} posts, dont {len(mine)} prévus par le robot")

    rows, shown = [], False
    for p in sorted(every, key=lambda x: x["id"]):
        detail = p
        if p["id"] in mine:
            # Le détail du post porte l'historique des prévisions du robot (heures seulement ici).
            try:
                detail = api(f"/posts/{p['id']}/", token=token)
            except urllib.error.HTTPError as e:
                print(f"  post {p['id']} : HTTP {e.code}")
        for q in questions_of(detail):
            if not shown:
                # Noms des champs de date d'une question, pour trouver celui du score « spot ».
                print("  champs *_time de la question : " + clean(sorted(k for k in q if "time" in k or k.endswith("_at")), 400))
                mf = q.get("my_forecasts") or {}
                print("  clés my_forecasts : " + clean(sorted(mf.keys()), 200))
                shown = True
            mf = q.get("my_forecasts") or {}
            history = mf.get("history") or ([mf["latest"]] if mf.get("latest") else [])
            starts = sorted(s for s in (to_dt(h.get("start_time")) for h in history if isinstance(h, dict)) if s)
            spot_key = next((k for k in SPOT_KEYS if q.get(k)), None)
            spot = to_dt(q.get(spot_key)) if spot_key else None
            last = starts[-1] if starts else None
            avance = f"{(spot - last).total_seconds() / 3600:.1f}" if (spot and last) else "-"
            rows.append({
                "post": p["id"], "question": q.get("id"),
                "status": clean(q.get("status") or p.get("status") or "?", 20),
                "resolved": "oui" if q.get("resolution") not in (None, "") else "non",
                "open": fmt(q.get("open_time")), "close": fmt(q.get("scheduled_close_time")),
                "actual_close": fmt(q.get("actual_close_time")),
                "spot": fmt(q.get("spot_scoring_time")), "reveal": fmt(q.get("cp_reveal_time")),
                "spot_key": spot_key or "-",
                "n": len(starts), "first": starts[0].strftime("%Y-%m-%d %H:%M") if starts else "-",
                "last": last.strftime("%Y-%m-%d %H:%M") if last else "-", "avance": avance,
            })

    head = ("| Post | Question | État | Tranchée | Ouverture | Clôture prévue | Clôture réelle | Spot scoring | "
            "Révélation CP | Prévisions du robot | Première | Dernière | Heures avant le spot |")
    lines = ["## Market Pulse : dates des sous-questions (UTC)", "", head, "|" + "---|" * 13]
    for r in rows:
        lines.append(f"| {r['post']} | {r['question']} | {r['status']} | {r['resolved']} | {r['open']} | {r['close']} | "
                     f"{r['actual_close']} | {r['spot']} | {r['reveal']} | {r['n']} | {r['first']} | {r['last']} | {r['avance']} |")
    for line in lines[2:]:
        print("  " + line)
    a_venir = sum(1 for r in rows if r["status"] in ("open", "upcoming"))
    multi = sum(1 for r in rows if r["n"] > 1)
    print(f"Total : {len(rows)} sous-questions ; ouvertes ou à venir : {a_venir} ; prévues plus d'une fois : {multi} ; "
          f"champ du score spot : {sorted({r['spot_key'] for r in rows})}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        print(f"Erreur HTTP {e.code} sur l'API Metaculus", file=sys.stderr)
        sys.exit(1)
