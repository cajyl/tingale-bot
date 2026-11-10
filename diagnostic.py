#!/usr/bin/env python3
"""Diagnostic du bot : pour chaque question TRANCHÉE qu'il a prévue, sa réponse, celle des autres
bots, le résultat et les points gagnés ou perdus. Lecture seule : rien n'est publié.

Pourquoi : le classement de Fall du 06/11 (233e sur 454, score négatif) ne reposait que sur deux
questions, prévues par l'ancien modèle ; avant de toucher au bot, il faut savoir quel modèle a
répondu à quoi, et ce que chaque question a rapporté.

Le dépôt est public, donc son journal aussi : seules les questions et sous-questions TRANCHÉES
sont écrites ; les commentaires privés du bot ne sont pas lus (le modèle se déduit de l'heure de
la prévision). Aucun fichier n'est conservé.

Usage (variables d'environnement) :
    METACULUS_TOKEN   jeton du compte bot (obligatoire)
    TOURNAMENTS       liste "id-ou-slug:libellé" séparés par des virgules (facultatif)
"""

import os
import sys
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import api, clean  # noqa: E402

DEFAULT_TOURNAMENTS = "33121:Fall,33135:MiniBench,market-pulse-26q4:Market Pulse"


def resolved_posts(token, tournament, my_id):
    params = {"tournaments": tournament, "statuses": ["resolved"], "forecaster_id": my_id,
              "limit": 100, "offset": 0, "with_cp": "true"}
    posts = []
    while True:
        data = api("/posts/", params, token)
        results = data.get("results", [])
        posts.extend(results)
        if not data.get("next") or not results:
            return posts
        params["offset"] += len(results)


def latest(block):
    return (block or {}).get("latest") or {}


def summarize(post, q):
    """Réponse du bot, agrégat des autres et points, sous une forme comparable entre types."""
    kind = q.get("type")
    mine = latest(q.get("my_forecasts"))
    aggregations = q.get("aggregations") or {}
    agg = latest(aggregations.get("recency_weighted") or aggregations.get("unweighted"))
    res = q.get("resolution")
    row = {"post": post["id"], "question": q.get("id"), "type": kind, "title": q.get("title") or post.get("title"),
           "resolution": res, "open": q.get("open_time"), "close": q.get("actual_close_time") or q.get("scheduled_close_time"),
           "score_data": (q.get("my_forecasts") or {}).get("score_data"),
           }
    if kind == "binary":
        fv = mine.get("forecast_values") or []
        row["mine"] = fv[1] if len(fv) == 2 else None
        row["others"] = (agg.get("centers") or [None])[0]
        row["outcome"] = 1 if res == "yes" else 0 if res == "no" else None
    elif kind == "multiple_choice":
        options = q.get("options") or []
        fv = mine.get("forecast_values") or []
        fa = agg.get("forecast_values") or []
        if res in options:
            i = options.index(res)
            row["mine"] = fv[i] if i < len(fv) else None
            row["others"] = fa[i] if i < len(fa) else None
    return row


# Modèle qui a répondu, d'après l'heure de la prévision : Claude Opus 5.5 depuis le 25/10 13:39 UTC
# (commit f9c27e7), gpt-5-mini avant. Pas besoin de lire les commentaires privés du bot.
SWITCH_TO_CLAUDE = datetime(2026, 10, 25, 13, 39, tzinfo=timezone.utc).timestamp()


def model_of(q):
    start = latest(q.get("my_forecasts")).get("start_time")
    if not isinstance(start, (int, float)):
        return "?"
    return "claude-opus-5-5" if start >= SWITCH_TO_CLAUDE else "gpt-5-mini"


def main():
    token = os.environ.get("METACULUS_TOKEN", "").strip()
    if not token:
        print("METACULUS_TOKEN manquant : rien à faire.")
        return 0
    me = api("/users/me/", token=token)
    my_id = me["id"]
    rows, shown_keys = [], False
    for item in os.environ.get("TOURNAMENTS", DEFAULT_TOURNAMENTS).split(","):
        key, _, label = item.strip().partition(":")
        try:
            posts = resolved_posts(token, key, my_id)
        except urllib.error.HTTPError as e:
            print(f"{label} : HTTP {e.code}")
            continue
        print(f"{label} : {len(posts)} questions résolues prévues par le bot")
        for post in posts:
            questions = [post["question"]] if post.get("question") else \
                ((post.get("group_of_questions") or {}).get("questions") or [])
            for q in questions:
                if not q.get("my_forecasts") or q.get("resolution") is None:
                    continue
                if not shown_keys:
                    print("  clés my_forecasts : " + clean(sorted(q["my_forecasts"].keys()), 300))
                    print("  clés score_data : " + clean(sorted((q["my_forecasts"].get("score_data") or {}).keys()), 300))
                    print("  clés aggregations : " + clean(sorted((q.get("aggregations") or {}).keys()), 300))
                    shown_keys = True
                row = summarize(post, q)
                row["tournament"] = label
                row["model"] = model_of(q)
                rows.append(row)

    def pts(r):
        s = r.get("score_data") or {}
        return s.get("spot_peer_score", s.get("peer_score"))

    rows.sort(key=lambda r: (pts(r) is None, pts(r) or 0))
    lines = ["## Diagnostic : questions tranchées", "",
             "| Tournoi | Post | Modèle | Type | Bot | Autres bots | Résultat | Points (spot peer) | Question |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        f = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "—"
        p = pts(r)
        line = (f"| {r['tournament']} | {r['post']} | {r['model']} | {r['type']} | {f(r.get('mine'))} | {f(r.get('others'))} | "
                f"{clean(r.get('resolution'), 30)} | {f(p)} | {clean(r.get('title'), 70)} |")
        lines.append(line)
        print("  " + line)
    total = sum(pts(r) or 0 for r in rows)
    print(f"Total : {len(rows)} questions tranchées, somme des points {total:.1f}")
    by_model = {}
    for r in rows:
        if pts(r) is not None:
            by_model.setdefault((r["tournament"], r["model"]), []).append(pts(r))
    for (t, m), v in sorted(by_model.items()):
        lines.append(f"- {t}, {m} : {len(v)} tranchées, somme {sum(v):.1f}, moyenne {sum(v) / len(v):.1f}")
        print(f"  {t}, {m} : {len(v)} tranchées, somme {sum(v):.1f}, moyenne {sum(v) / len(v):.1f}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
