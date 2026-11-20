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

# Clé du tournoi passée telle quelle à /posts/?tournaments= (identifiant ou nom court), comme dans
# score.py : « minibench » y désigne le cycle MiniBench en cours (projet 33169 au 20/11), 33135 le
# cycle du 02/11, terminé. Un libellé peut contenir espaces et parenthèses, pas de virgule.
FALL = "33121"
DEFAULT_TOURNAMENTS = (f"{FALL}:Fall,minibench:MiniBench (cycle en cours),"
                       "33135:MiniBench (cycle du 02/11),market-pulse-26q4:Market Pulse")


def tournaments(spec):
    """Liste des couples (clé, libellé) lus dans « clé:libellé,clé:libellé »."""
    pairs = []
    for item in spec.split(","):
        key, _, label = item.strip().partition(":")
        if key:
            pairs.append((key.strip(), label.strip() or key.strip()))
    return pairs


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


# Réglage qui a répondu, d'après l'heure de début de la dernière prévision (secondes Unix, UTC) et le
# tournoi. Pas besoin de lire les commentaires privés du bot. Chaque bascule vaut à l'instant donné.
# Passage de gpt-5-mini à Claude Opus 5.5, effort « medium » (commit f9c27e7).
SWITCH_TO_CLAUDE = datetime(2026, 10, 25, 13, 39, tzinfo=timezone.utc).timestamp()
# Passage de l'effort de raisonnement de Claude de « medium » à « high », tous tournois.
SWITCH_TO_HIGH = datetime(2026, 11, 13, 11, 2, tzinfo=timezone.utc).timestamp()
# Mise en service d'AskNews sur Fall seulement (ASKNEWS_ENABLED=1) ; les autres tournois restent sans.
SWITCH_TO_ASKNEWS_FALL = datetime(2026, 11, 17, 18, 3, tzinfo=timezone.utc).timestamp()


def model_of(q, tournament):
    """Libellé du réglage pour la question q du tournoi de clé `tournament`."""
    start = latest(q.get("my_forecasts")).get("start_time")
    if not isinstance(start, (int, float)) or isinstance(start, bool):
        return "?"
    if start < SWITCH_TO_CLAUDE:
        return "gpt-5-mini"
    if start < SWITCH_TO_HIGH:
        return "claude-opus-5-5 medium"
    if str(tournament) == FALL and start >= SWITCH_TO_ASKNEWS_FALL:
        return "claude-opus-5-5 high + AskNews"
    return "claude-opus-5-5 high"


# ---------------------------------------------------------------------------------------------------
# Plage de l'auteur (hypothèse R1, étape 1, pré-enregistrée le 20/11/2026 dans le dépôt de l'opérateur :
# experiments/E001-metaculus-bot.md et lab/robot-plage/PROJET_PROTOCOLE.md § 4.1). Les seuils ci-dessous
# sont recopiés du pré-enregistrement : ce code les applique, il ne les choisit pas.
# ---------------------------------------------------------------------------------------------------
R1_TYPES = ("numeric", "discrete")
# Résolution connue au plus tard à cet instant (relevé du 20/11 à 08:25 UTC).
R1_CUTOFF = datetime(2026, 11, 20, 8, 25, tzinfo=timezone.utc).timestamp()
# Posts qui ont servi à concevoir l'hypothèse (46016, 46284) et post annulé (46010).
R1_EXCLUDED_POSTS = frozenset({46016, 46284, 46010})
# Réglages réservés à l'étape 2 : jamais mesurés ni affichés ici.
R1_RESERVED_MODELS = frozenset({"claude-opus-5-5 high", "claude-opus-5-5 high + AskNews"})
# Réglage tenu à part : affiché et compté pour information, hors verdict.
R1_APART_MODEL = "gpt-5-mini"
R1_DEFECT_THRESHOLD = 0.30       # « en défaut » si M_hors >= 0,30
R1_PASS_MIN_DEFECTS = 4          # passe : au moins 4 questions en défaut...
R1_PASS_MIN_POSTS = 3            # ... venant d'au moins 3 posts
R1_PASS_MIN_INSIDE_SHARE = 0.75  # ... au moins les trois quarts tranchées dans la plage
R1_PASS_MAX_POINTS = -60.0       # ... et leurs points cumulés <= -60
R1_STOP_MIN_DEFECTS = 3          # arrêt : moins de 3 questions en défaut
# Champs de date de résolution essayés dans l'ordre (le premier présent et lisible l'emporte).
R1_RESOLUTION_TIME_FIELDS = ("resolution_set_time", "actual_resolve_time")

OUT_OF_RANGE = {"above_upper_bound": "hors", "below_lower_bound": "hors"}


def points_of(row):
    """Points (spot peer) d'une ligne du diagnostic, ou None."""
    s = row.get("score_data") or {}
    return s.get("spot_peer_score", s.get("peer_score"))


def bound_flag(q, name):
    """Drapeau `open_lower_bound` ou `open_upper_bound`, lu au niveau de la question, sinon sous
    `scaling` ; None s'il manque ou n'est pas un booléen."""
    for source in (q, q.get("scaling") or {}):
        value = source.get(name) if isinstance(source, dict) else None
        if isinstance(value, bool):
            return value
    return None


def bounds_label(lower, upper):
    """« bas », « haut », « bas+haut », « aucune », ou « ? » si un drapeau manque."""
    if lower is None or upper is None:
        return "?"
    return {(True, True): "bas+haut", (True, False): "bas", (False, True): "haut"}.get((lower, upper), "aucune")


def mass_outside(cdf, lower, upper):
    """M_hors : masse de la fonction de répartition `cdf` au-delà des bornes ouvertes (cdf[0] pour la
    borne basse, 1 - cdf[-1] pour la haute). None si un drapeau manque ou si `cdf` est illisible."""
    if lower is None or upper is None or not isinstance(cdf, (list, tuple)) or len(cdf) < 2:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (cdf[0], cdf[-1])):
        return None
    m = (float(cdf[0]) if lower else 0.0) + ((1.0 - float(cdf[-1])) if upper else 0.0)
    return max(0.0, m)


def range_place(resolution):
    """« hors » si la résolution est au-delà d'une borne, « dans » si elle est chiffrée, sinon None
    (annulée, ambiguë, absente)."""
    if resolution in OUT_OF_RANGE:
        return OUT_OF_RANGE[resolution]
    if isinstance(resolution, bool) or resolution is None:
        return None
    try:
        float(resolution)
    except (TypeError, ValueError):
        return None
    return "dans"


def resolution_time(q):
    """Instant (secondes Unix, UTC) où la résolution a été connue, ou None si aucun champ lisible."""
    for name in R1_RESOLUTION_TIME_FIELDS:
        value = q.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t.timestamp()
    return None


def r1_rows(items):
    """Lignes du tableau R1. `items` : dictionnaires {tournament, post (identifiant), q (objet question),
    model, points}. Les réglages réservés et les types non chiffrés sont écartés AVANT toute lecture de
    la prévision. Chaque ligne porte `role` : « jugement », « exclu », « à part » ou « hors jugement »."""
    rows = []
    for it in items:
        q = it.get("q") or {}
        model = it.get("model")
        if model in R1_RESERVED_MODELS or q.get("type") not in R1_TYPES:
            continue
        lower, upper = bound_flag(q, "open_lower_bound"), bound_flag(q, "open_upper_bound")
        cdf = latest(q.get("my_forecasts")).get("forecast_values")
        when = resolution_time(q)
        row = {"tournament": it.get("tournament"), "post": it.get("post"), "question": q.get("id"),
               "type": q.get("type"), "model": model, "bounds": bounds_label(lower, upper),
               "m_hors": mass_outside(cdf, lower, upper), "place": range_place(q.get("resolution")),
               "points": it.get("points"), "resolved_at": when}
        notes = []
        try:
            excluded = int(row["post"]) in R1_EXCLUDED_POSTS
        except (TypeError, ValueError):
            excluded = False
        if excluded:
            role, notes = "exclu", ["exclu"]
        elif model == R1_APART_MODEL:
            role, notes = "à part", ["gpt-5-mini, à part"]
        elif model not in ("claude-opus-5-5 medium",):
            role, notes = "hors jugement", ["réglage inconnu, hors jugement"]
        elif when is not None and when > R1_CUTOFF:
            role, notes = "hors jugement", ["tranchée après le 20/11 08:25 UTC, hors jugement"]
        elif row["place"] is None:
            role, notes = "hors jugement", ["résolution non chiffrée, hors jugement"]
        elif row["m_hors"] is None:
            role, notes = "hors jugement", ["M_hors illisible, hors jugement"]
        else:
            role = "jugement"
        if when is None:
            notes.append("date de résolution inconnue")
        row["role"], row["note"] = role, ", ".join(notes)
        rows.append(row)
    rows.sort(key=lambda r: (str(r["tournament"]), str(r["post"]), str(r["question"])))
    return rows


def r1_verdict(sample):
    """Bilan et verdict mécanique sur l'échantillon du jugement (lignes dont M_hors et la place sont
    connus). Renvoie un dictionnaire de nombres, de booléens et la conclusion."""
    n = len(sample)
    defects = [r for r in sample if r["m_hors"] >= R1_DEFECT_THRESHOLD]
    nd = len(defects)
    posts = len({r["post"] for r in defects})
    inside = sum(1 for r in defects if r["place"] == "dans")
    pts = sum(r["points"] for r in defects if isinstance(r.get("points"), (int, float)))
    mean = sum(r["m_hors"] for r in sample) / n if n else None
    share_out = sum(1 for r in sample if r["place"] == "hors") / n if n else None
    v = {"n": n, "defects": nd, "posts": posts, "inside": inside, "points": pts,
         "mean_m_hors": mean, "share_out": share_out}
    v["pass_count"] = nd >= R1_PASS_MIN_DEFECTS and posts >= R1_PASS_MIN_POSTS
    v["pass_inside"] = nd > 0 and inside / nd >= R1_PASS_MIN_INSIDE_SHARE
    v["pass_points"] = nd > 0 and pts <= R1_PASS_MAX_POINTS
    v["stop_few"] = nd < R1_STOP_MIN_DEFECTS
    v["stop_mean"] = n > 0 and mean <= share_out
    v["stop_points"] = pts > 0
    v["passe"] = v["pass_count"] and v["pass_inside"] and v["pass_points"]
    v["arret"] = v["stop_few"] or v["stop_mean"] or v["stop_points"]
    if n == 0:
        v["conclusion"] = "ÉCHANTILLON VIDE, rien à juger"
    elif v["passe"] and v["arret"]:
        v["conclusion"] = "PASSE et ARRÊT : contradictoire, à lire par l'opérateur"
    elif v["passe"]:
        v["conclusion"] = "PASSE"
    elif v["arret"]:
        v["conclusion"] = "ARRÊT"
    else:
        v["conclusion"] = "NON CONCLUANT"
    return v


def r1_report(rows):
    """Lignes de texte (Markdown) du tableau R1 et de son bilan."""
    def num(v):
        return f"{v:.2f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else "?"

    def yn(b):
        return "oui" if b else "non"

    out = ["## Plage de l'auteur (hypothèse R1, étape 1)", "",
           "| Tournoi | Post | Question | Type | Bornes ouvertes | M_hors | Résultat | Points | Mention |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {clean(r['tournament'], 40)} | {r['post']} | {r['question'] if r['question'] is not None else '?'} | "
                   f"{r['type']} | {r['bounds']} | {num(r['m_hors'])} | {r['place'] or '?'} | {num(r['points'])} | "
                   f"{r['note']} |")
    out.append("")
    sample = [r for r in rows if r["role"] == "jugement"]
    v = r1_verdict(sample)
    out.append(f"- Échantillon du jugement : {v['n']} questions (hors exclus, hors gpt-5-mini, hors réservées)")
    out.append(f"- En défaut (M_hors >= {R1_DEFECT_THRESHOLD:.2f}) : {v['defects']}, venant de {v['posts']} posts ; "
               f"tranchées dans la plage : {v['inside']} ; somme de leurs points : {v['points']:.1f}")
    out.append(f"- Moyenne de M_hors : {num(v['mean_m_hors'])} ; part des résultats hors plage : {num(v['share_out'])}")
    out.append(f"- Passe, critère 1 (au moins {R1_PASS_MIN_DEFECTS} en défaut, au moins {R1_PASS_MIN_POSTS} posts) : "
               f"{yn(v['pass_count'])}")
    out.append(f"- Passe, critère 2 (au moins les trois quarts dans la plage) : {yn(v['pass_inside'])}")
    out.append(f"- Passe, critère 3 (points cumulés <= {R1_PASS_MAX_POINTS:.0f}) : {yn(v['pass_points'])}")
    out.append(f"- Arrêt : {yn(v['arret'])} (moins de {R1_STOP_MIN_DEFECTS} en défaut : {yn(v['stop_few'])} ; "
               f"moyenne de M_hors <= part hors plage : {yn(v['stop_mean'])} ; points cumulés > 0 : {yn(v['stop_points'])})")
    out.append(f"- Conclusion : {v['conclusion']}")
    apart = [r for r in rows if r["role"] == "à part"]
    apart_defects = sum(1 for r in apart if isinstance(r["m_hors"], float) and r["m_hors"] >= R1_DEFECT_THRESHOLD)
    out.append(f"- Pour information, gpt-5-mini : {len(apart)} questions, {apart_defects} en défaut")
    others = [r for r in rows if r["role"] == "hors jugement"]
    if others:
        out.append(f"- Affichées hors jugement (réglage, date, résolution ou M_hors) : {len(others)}")
    return out


def main():
    token = os.environ.get("METACULUS_TOKEN", "").strip()
    if not token:
        print("METACULUS_TOKEN manquant : rien à faire.")
        return 0
    me = api("/users/me/", token=token)
    my_id = me["id"]
    rows, shown_keys = [], False
    r1_items = []
    for key, label in tournaments(os.environ.get("TOURNAMENTS", DEFAULT_TOURNAMENTS)):
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
                row["model"] = model_of(q, key)
                rows.append(row)
                r1_items.append({"tournament": label, "post": post["id"], "q": q, "model": row["model"],
                                 "points": points_of(row)})

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
    r1_lines = r1_report(r1_rows(r1_items))
    print("")
    for line in r1_lines:
        print("  " + line if line else "")
    lines += [""] + r1_lines
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
