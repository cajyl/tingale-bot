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


if __name__ == "__main__":
    unittest.main()
