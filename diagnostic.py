#!/usr/bin/env python3
"""Diagnostic du bot : pour chaque question TRANCHÉE qu'il a prévue, sa réponse, celle des autres
bots, le résultat et les points gagnés ou perdus. Lecture seule : rien n'est publié.

Pourquoi : le classement de Fall (06/11) place le bot 233e sur 454 avec un score négatif ; pour
l'améliorer, il faut savoir sur quelles questions il perd, et récupérer la recherche et les
raisonnements déjà produits (ses commentaires privés) pour rejouer d'autres réglages dessus
sans refaire de recherche (pas de fuite du résultat dans un test sur le passé).

Seules les questions résolues sont relevées : rien ne renseigne sur une question encore ouverte.

Usage (variables d'environnement) :
    METACULUS_TOKEN   jeton du compte bot (obligatoire)
    TOURNAMENTS       liste "id-ou-slug:libellé" séparés par des virgules (facultatif)
    OUT               fichier JSON de sortie (défaut : diagnostic.json)
"""

import json
import os
import sys
import urllib.error

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
    agg = latest((q.get("aggregations") or {}).get("recency_weighted"))
    res = q.get("resolution")
    row = {"post": post["id"], "question": q.get("id"), "type": kind, "title": q.get("title") or post.get("title"),
           "resolution": res, "open": q.get("open_time"), "close": q.get("actual_close_time") or q.get("scheduled_close_time"),
           "score_data": (q.get("my_forecasts") or {}).get("score_data"),
           "agg_score_data": ((q.get("aggregations") or {}).get("recency_weighted") or {}).get("score_data")}
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
        row["options"] = options
        row["mine_all"] = fv
        row["others_all"] = fa
    else:
        row["mine_centers"] = mine.get("centers")
        row["mine_bounds"] = [mine.get("interval_lower_bounds"), mine.get("interval_upper_bounds")]
        row["others_centers"] = agg.get("centers")
        row["scaling"] = q.get("scaling")
    return row


def comment_of(token, post_id, my_id):
    """Dernier commentaire privé du bot sur la question (recherche + raisonnements)."""
    for params in ({"post": post_id, "author": my_id, "limit": 5},
                   {"on_post": post_id, "author": my_id, "limit": 5}):
        try:
            data = api("/comments/", params, token)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            continue
        items = data.get("results", data) if isinstance(data, dict) else data
        items = [c for c in items if isinstance(c, dict)]
        mine = [c for c in items if (c.get("author") or {}).get("id") in (my_id, None) or c.get("author_id") == my_id]
        if mine:
            mine.sort(key=lambda c: c.get("created_at") or "")
            return {"text": (mine[-1].get("text") or "")[:120000], "created_at": mine[-1].get("created_at")}
        last = f"vide ({len(items)} commentaires, clés {sorted(items[0].keys()) if items else []})"
    return {"error": last}


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
                if not q.get("my_forecasts"):
                    continue
                if not shown_keys:
                    print("  clés my_forecasts : " + clean(sorted(q["my_forecasts"].keys()), 300))
                    print("  clés score_data : " + clean(sorted((q["my_forecasts"].get("score_data") or {}).keys()), 300))
                    print("  clés aggregations : " + clean(sorted((q.get("aggregations") or {}).keys()), 300))
                    shown_keys = True
                row = summarize(post, q)
                row["tournament"] = label
                row["comment"] = comment_of(token, post["id"], my_id)
                rows.append(row)

    out = os.environ.get("OUT", "diagnostic.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    def pts(r):
        s = r.get("score_data") or {}
        return s.get("spot_peer_score", s.get("peer_score"))

    rows.sort(key=lambda r: (pts(r) is None, pts(r) or 0))
    lines = ["## Diagnostic : questions tranchées", "",
             "| Tournoi | Post | Type | Bot | Autres bots | Résultat | Points (spot peer) | Question |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        f = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "—"
        p = pts(r)
        line = (f"| {r['tournament']} | {r['post']} | {r['type']} | {f(r.get('mine'))} | {f(r.get('others'))} | "
                f"{clean(r.get('resolution'), 30)} | {f(p)} | {clean(r.get('title'), 70)} |")
        lines.append(line)
        print("  " + line)
    total = sum(pts(r) or 0 for r in rows)
    comments = sum(1 for r in rows if r["comment"].get("text"))
    print(f"Total : {len(rows)} questions, somme des points {total:.1f}, commentaires récupérés {comments}")
    errors = {r["comment"].get("error") for r in rows if r["comment"].get("error")}
    if errors:
        print("  commentaires non lus : " + clean(sorted(errors), 300))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + f"\n\nTotal : {len(rows)} questions, somme {total:.1f}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
