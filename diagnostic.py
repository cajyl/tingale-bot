#!/usr/bin/env python3
"""Diagnostic du bot : pour chaque question TRANCHÉE qu'il a prévue, sa réponse, celle des autres
bots, le résultat et les points gagnés ou perdus. Lecture seule : rien n'est publié.

Pourquoi : le classement de Fall du 06/11 (233e sur 454, score négatif) ne reposait que sur deux
questions, prévues par l'ancien modèle ; avant de toucher au bot, il faut savoir quel modèle a
répondu à quoi, et ce que chaque question a rapporté.

Le dépôt est public, donc son journal aussi : seules les questions et sous-questions TRANCHÉES
sont écrites, et rien du commentaire privé du bot n'en sort, hormis le nom du modèle qui a
répondu. Aucun fichier n'est conservé.

Usage (variables d'environnement) :
    METACULUS_TOKEN   jeton du compte bot (obligatoire)
    TOURNAMENTS       liste "id-ou-slug:libellé" séparés par des virgules (facultatif)
"""

import os
import re
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


def author_id(comment):
    a = comment.get("author")
    return a.get("id") if isinstance(a, dict) else a if isinstance(a, int) else comment.get("author_id")


def model_of(token, post_id, my_id):
    """Modèle qui a répondu, lu dans le dernier commentaire privé du bot sur ce post. Seuls les
    commentaires dont l'auteur est le bot et le post celui demandé sont retenus ; le texte n'est
    jamais écrit ni conservé."""
    try:
        data = api("/comments/", {"post": post_id, "author": my_id, "limit": 5}, token)
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    items = data.get("results", []) if isinstance(data, dict) else data
    mine = [c for c in items if isinstance(c, dict) and author_id(c) == my_id
            and c.get("on_post", post_id) == post_id]
    if not mine:
        seen = [(type(c.get("author")).__name__, author_id(c) == my_id, c.get("on_post")) for c in items if isinstance(c, dict)]
        print(f"  (post {post_id} : aucun commentaire du bot parmi {clean(seen, 200)})")
        return "?"
    mine.sort(key=lambda c: c.get("created_at") or "")
    m = re.search(r"'default': \{'original_model': '([^']+)'", mine[-1].get("text") or "")
    return clean(m.group(1).rsplit("/", 1)[-1], 40) if m else "?"


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
            model = None
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
                model = model or model_of(token, post["id"], my_id)
                row["model"] = model
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
