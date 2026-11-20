"""Tests de l'outil de relevé diagnostic.py (aucun appel réseau, aucun jeton).

Lancement : python3 -m unittest test_diagnostic
"""

import unittest

import diagnostic
from diagnostic import (
    DEFAULT_TOURNAMENTS,
    FALL,
    SWITCH_TO_ASKNEWS_FALL,
    SWITCH_TO_CLAUDE,
    SWITCH_TO_HIGH,
    model_of,
    tournaments,
)

AUTRE = "minibench"


def question(start):
    """Question factice dont la dernière prévision du bot commence à `start`."""
    return {"my_forecasts": {"latest": {"start_time": start}}}


class TestBascules(unittest.TestCase):
    def test_dates_en_utc(self):
        self.assertEqual(SWITCH_TO_HIGH, 1794567720)  # 2026-11-13 11:02:00 UTC
        self.assertEqual(SWITCH_TO_ASKNEWS_FALL, 1794938580)  # 2026-11-17 18:03:00 UTC
        self.assertLess(SWITCH_TO_CLAUDE, SWITCH_TO_HIGH)
        self.assertLess(SWITCH_TO_HIGH, SWITCH_TO_ASKNEWS_FALL)

    def test_avant_claude(self):
        for t in (FALL, AUTRE):
            self.assertEqual(model_of(question(SWITCH_TO_CLAUDE - 1), t), "gpt-5-mini")

    def test_claude_medium(self):
        for t in (FALL, AUTRE):
            self.assertEqual(model_of(question(SWITCH_TO_CLAUDE), t), "claude-opus-5-5 medium")
            self.assertEqual(model_of(question(SWITCH_TO_HIGH - 1), t), "claude-opus-5-5 medium")

    def test_claude_high(self):
        for t in (FALL, AUTRE):
            self.assertEqual(model_of(question(SWITCH_TO_HIGH), t), "claude-opus-5-5 high")
            self.assertEqual(model_of(question(SWITCH_TO_ASKNEWS_FALL - 1), t), "claude-opus-5-5 high")

    def test_asknews_fall_seulement(self):
        self.assertEqual(model_of(question(SWITCH_TO_ASKNEWS_FALL), FALL), "claude-opus-5-5 high + AskNews")
        self.assertEqual(model_of(question(SWITCH_TO_ASKNEWS_FALL + 86400), FALL), "claude-opus-5-5 high + AskNews")
        for t in (AUTRE, "33135", "market-pulse-26q4"):
            self.assertEqual(model_of(question(SWITCH_TO_ASKNEWS_FALL), t), "claude-opus-5-5 high")

    def test_cle_fall_entiere(self):
        self.assertEqual(model_of(question(SWITCH_TO_ASKNEWS_FALL), int(FALL)), "claude-opus-5-5 high + AskNews")

    def test_start_time_absent_ou_invalide(self):
        for q in ({}, {"my_forecasts": None}, {"my_forecasts": {}}, {"my_forecasts": {"latest": None}},
                  {"my_forecasts": {"latest": {}}}, question(None), question("1794567720"), question(True)):
            self.assertEqual(model_of(q, FALL), "?")
            self.assertEqual(model_of(q, AUTRE), "?")


class TestTournois(unittest.TestCase):
    def test_defaut(self):
        self.assertEqual(tournaments(DEFAULT_TOURNAMENTS), [
            ("33121", "Fall"),
            ("minibench", "MiniBench (cycle en cours)"),
            ("33135", "MiniBench (cycle du 02/11)"),
            ("market-pulse-26q4", "Market Pulse"),
        ])

    def test_espaces_et_libelle_absent(self):
        self.assertEqual(tournaments(" 33121 : Fall , minibench,"), [("33121", "Fall"), ("minibench", "minibench")])

    def test_fall_est_la_cle_par_defaut(self):
        self.assertEqual(tournaments(DEFAULT_TOURNAMENTS)[0][0], diagnostic.FALL)


# --- Plage de l'auteur (hypothèse R1, étape 1) : données inventées ---------------------------------
from diagnostic import (  # noqa: E402
    R1_CUTOFF,
    bound_flag,
    mass_outside,
    r1_report,
    r1_rows,
    r1_verdict,
    range_place,
)

MEDIUM = "claude-opus-5-5 medium"


def qn(cdf=(0.1, 0.5, 0.8), lower=True, upper=True, res="12.5", typ="numeric", when="2026-11-19T10:00:00Z",
       in_scaling=False, qid=1):
    q = {"id": qid, "type": typ, "resolution": res, "my_forecasts": {"latest": {"forecast_values": list(cdf)}}}
    flags = {}
    if lower is not None:
        flags["open_lower_bound"] = lower
    if upper is not None:
        flags["open_upper_bound"] = upper
    if in_scaling:
        q["scaling"] = flags
    else:
        q.update(flags)
    if when is not None:
        q["resolution_set_time"] = when
    return q


def item(post, q, model=MEDIUM, points=-10.0, tournament="MiniBench"):
    return {"tournament": tournament, "post": post, "q": q, "model": model, "points": points}


def jug(post, m, place="dans", points=-10.0):
    return {"post": post, "m_hors": m, "place": place, "points": points}


class TestMHors(unittest.TestCase):
    CDF = [0.2, 0.5, 0.9]

    def test_quatre_cas_de_bornes(self):
        self.assertAlmostEqual(mass_outside(self.CDF, False, False), 0.0)
        self.assertAlmostEqual(mass_outside(self.CDF, True, False), 0.2)
        self.assertAlmostEqual(mass_outside(self.CDF, False, True), 0.1)
        self.assertAlmostEqual(mass_outside(self.CDF, True, True), 0.3)

    def test_champ_manquant(self):
        self.assertIsNone(mass_outside(self.CDF, None, True))
        self.assertIsNone(mass_outside(None, True, True))
        self.assertIsNone(mass_outside([], True, True))
        self.assertIsNone(bound_flag({}, "open_lower_bound"))
        self.assertIsNone(bound_flag({"open_lower_bound": "oui"}, "open_lower_bound"))
        rows = r1_rows([item(1, qn(lower=None))])
        self.assertEqual(rows[0]["bounds"], "?")
        self.assertIsNone(rows[0]["m_hors"])
        self.assertEqual(rows[0]["role"], "hors jugement")
        self.assertIn("| ? |", "\n".join(r1_report(rows)))

    def test_drapeaux_sous_scaling(self):
        self.assertTrue(bound_flag({"scaling": {"open_upper_bound": True}}, "open_upper_bound"))
        rows = r1_rows([item(1, qn(lower=False, upper=True, in_scaling=True))])
        self.assertEqual(rows[0]["bounds"], "haut")
        self.assertAlmostEqual(rows[0]["m_hors"], 0.2)


class TestPlace(unittest.TestCase):
    def test_dans_ou_hors(self):
        self.assertEqual(range_place("above_upper_bound"), "hors")
        self.assertEqual(range_place("below_lower_bound"), "hors")
        self.assertEqual(range_place("12.5"), "dans")
        self.assertEqual(range_place(3), "dans")
        for r in ("annulled", "ambiguous", None, True, ""):
            self.assertIsNone(range_place(r))


class TestEchantillon(unittest.TestCase):
    def test_post_exclu_en_high_affiche_comme_exclu(self):
        rows = r1_rows([item(46016, qn(), model="claude-opus-5-5 high"), item(7001, qn(), model="claude-opus-5-5 high")])
        self.assertEqual([(r["post"], r["role"]) for r in rows], [(46016, "exclu")])

    def test_high_jamais_affiche(self):
        items = [item(7001, qn(), model="claude-opus-5-5 high"),
                 item(7002, qn(), model="claude-opus-5-5 high + AskNews"), item(7003, qn())]
        rows = r1_rows(items)
        self.assertEqual([r["post"] for r in rows], [7003])
        text = "\n".join(r1_report(rows))
        self.assertNotIn("7001", text)
        self.assertNotIn("7002", text)

    def test_types_non_chiffres_ignores(self):
        self.assertEqual(r1_rows([item(1, qn(typ="binary")), item(2, qn(typ="multiple_choice"))]), [])
        self.assertEqual(len(r1_rows([item(3, qn(typ="discrete"))])), 1)

    def test_posts_exclus(self):
        rows = r1_rows([item(p, qn()) for p in (46016, 46284, 46010)] + [item(5, qn())])
        roles = {r["post"]: (r["role"], r["note"]) for r in rows}
        for p in (46016, 46284, 46010):
            self.assertEqual(roles[p], ("exclu", "exclu"))
        self.assertEqual(roles[5][0], "jugement")

    def test_gpt5_mini_a_part(self):
        rows = r1_rows([item(1, qn(cdf=(0.4, 0.5, 0.6)), model="gpt-5-mini"), item(2, qn())])
        self.assertEqual([r["role"] for r in rows], ["à part", "jugement"])
        text = "\n".join(r1_report(rows))
        self.assertIn("Échantillon du jugement : 1 questions", text)
        self.assertIn("gpt-5-mini : 1 questions, 1 en défaut", text)

    def test_date_limite(self):
        def iso(t):
            from datetime import datetime, timezone
            return datetime.fromtimestamp(t, timezone.utc).isoformat()
        rows = r1_rows([item(1, qn(when=iso(R1_CUTOFF))), item(2, qn(when=iso(R1_CUTOFF + 1))),
                        item(3, qn(when=None)), item(4, qn(when=R1_CUTOFF - 60))])
        roles = {r["post"]: r for r in rows}
        self.assertEqual(roles[1]["role"], "jugement")
        self.assertEqual(roles[2]["role"], "hors jugement")
        self.assertEqual(roles[3]["role"], "jugement")
        self.assertEqual(roles[3]["note"], "date de résolution inconnue")
        self.assertEqual(roles[4]["role"], "jugement")

    def test_reglage_inconnu(self):
        self.assertEqual(r1_rows([item(1, qn(), model="?")])[0]["role"], "hors jugement")


class TestVerdict(unittest.TestCase):
    def test_passe(self):
        sample = [jug(1, 0.5), jug(2, 0.4), jug(3, 0.6), jug(3, 0.3, place="hors")] + \
                 [jug(10 + i, 0.0, points=1.0) for i in range(6)]
        v = r1_verdict(sample)
        self.assertEqual((v["defects"], v["posts"], v["inside"]), (4, 3, 3))
        self.assertAlmostEqual(v["points"], -40.0)
        self.assertFalse(v["passe"])  # -40 > -60
        sample = [jug(1, 0.5, points=-20), jug(2, 0.4, points=-20), jug(3, 0.6, points=-20),
                  jug(3, 0.3, place="hors", points=-5)] + [jug(10 + i, 0.0) for i in range(6)]
        v = r1_verdict(sample)
        self.assertTrue(v["pass_count"] and v["pass_inside"] and v["pass_points"])
        self.assertFalse(v["arret"])
        self.assertEqual(v["conclusion"], "PASSE")

    def test_arret(self):
        v = r1_verdict([jug(1, 0.5), jug(2, 0.1), jug(3, 0.0)])
        self.assertTrue(v["stop_few"])
        self.assertEqual(v["conclusion"], "ARRÊT")
        v = r1_verdict([jug(i, 0.4, points=5.0) for i in range(5)])
        self.assertTrue(v["stop_points"])
        self.assertEqual(v["conclusion"], "ARRÊT")

    def test_non_concluant(self):
        # 3 en défaut sur 2 posts : pas assez pour passer, pas d'arrêt.
        sample = [jug(1, 0.5), jug(1, 0.4), jug(2, 0.6)] + [jug(10 + i, 0.05) for i in range(5)]
        v = r1_verdict(sample)
        self.assertFalse(v["passe"])
        self.assertFalse(v["arret"])
        self.assertEqual(v["conclusion"], "NON CONCLUANT")

    def test_contradictoire(self):
        # Passe sur ses trois critères, mais la moyenne de M_hors ne dépasse pas la part hors plage.
        sample = [jug(1, 0.3, points=-20), jug(2, 0.3, points=-20), jug(3, 0.3, points=-20), jug(4, 0.3, points=-20)] + \
                 [jug(10 + i, 0.0, place="hors", points=1.0) for i in range(4)]
        v = r1_verdict(sample)
        self.assertTrue(v["passe"])
        self.assertTrue(v["stop_mean"])
        self.assertIn("contradictoire, à lire par l'opérateur", v["conclusion"])

    def test_echantillon_vide(self):
        self.assertEqual(r1_verdict([])["conclusion"], "ÉCHANTILLON VIDE, rien à juger")

    def test_rapport_sans_tiret_cadratin(self):
        text = "\n".join(r1_report(r1_rows([item(1, qn()), item(2, qn(lower=None))])))
        self.assertTrue(text.startswith("## Plage de l'auteur (hypothèse R1, étape 1)"))
        self.assertNotIn("\u2014", text)


if __name__ == "__main__":
    unittest.main()
