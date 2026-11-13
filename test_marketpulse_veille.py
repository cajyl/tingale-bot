"""Tests de la sélection « veille de la notation » de Market Pulse (dates fictives).

Lancement : python3 -m unittest test_marketpulse_veille
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from marketpulse_veille import choisir_veille, cle_compteur, dates_de_question, marquer, non_epuisees

MAINTENANT = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
J = timedelta(days=1)


def sq(nom, notation=None, cloture=None, derniere=None):
    return {"nom": nom, "spot_scoring_time": notation, "scheduled_close_time": cloture, "derniere_prevision": derniere}


def noms(liste):
    return [e["nom"] for e in liste]


class ChoisirVeille(unittest.TestCase):
    def test_notation_dans_23h_prevision_vieille_de_10_jours(self):
        e = sq("a", notation=MAINTENANT + 23 * H, cloture=MAINTENANT + 5 * J, derniere=MAINTENANT - 10 * J)
        self.assertEqual(noms(choisir_veille([e], MAINTENANT)), ["a"])

    def test_notation_dans_30h_pas_encore(self):
        e = sq("a", notation=MAINTENANT + 30 * H, derniere=MAINTENANT - 10 * J)
        self.assertEqual(choisir_veille([e], MAINTENANT), [])

    def test_deja_refaite_il_y_a_2h(self):
        e = sq("a", notation=MAINTENANT + 20 * H, derniere=MAINTENANT - 2 * H)
        self.assertEqual(choisir_veille([e], MAINTENANT), [])

    def test_notation_passee(self):
        e = sq("a", notation=MAINTENANT - 1 * H, cloture=MAINTENANT + 3 * J, derniere=MAINTENANT - 10 * J)
        self.assertEqual(choisir_veille([e], MAINTENANT), [])
        pile = sq("b", notation=MAINTENANT, derniere=MAINTENANT - 10 * J)
        self.assertEqual(choisir_veille([pile], MAINTENANT), [])

    def test_spot_absent_repli_sur_la_cloture(self):
        dans = sq("a", notation=None, cloture=MAINTENANT + 10 * H, derniere=MAINTENANT - 7 * J)
        hors = sq("b", notation=None, cloture=MAINTENANT + 40 * H, derniere=MAINTENANT - 7 * J)
        self.assertEqual(noms(choisir_veille([dans, hors], MAINTENANT)), ["a"])

    def test_spot_anterieur_a_la_cloture(self):
        # La notation (spot) tombe dans 12 h alors que la clôture est dans 6 jours : c'est le spot qui compte.
        e = sq("a", notation=MAINTENANT + 12 * H, cloture=MAINTENANT + 6 * J, derniere=MAINTENANT - 9 * J)
        self.assertEqual(noms(choisir_veille([e], MAINTENANT)), ["a"])
        # Spot passé, clôture à venir dans 20 h : jamais refaite (pas de repli sur la clôture).
        f = sq("b", notation=MAINTENANT - 2 * H, cloture=MAINTENANT + 20 * H, derniere=MAINTENANT - 9 * J)
        self.assertEqual(choisir_veille([f], MAINTENANT), [])

    def test_plus_de_six_candidates(self):
        heures = [20, 3, 15, 1, 22, 8, 11, 5]
        elements = [sq(f"h{h}", notation=MAINTENANT + h * H, derniere=MAINTENANT - 10 * J) for h in heures]
        self.assertEqual(noms(choisir_veille(elements, MAINTENANT)), ["h1", "h3", "h5", "h8", "h11", "h15"])

    def test_jamais_prevue_ignoree(self):
        e = sq("a", notation=MAINTENANT + 5 * H, derniere=None)
        self.assertEqual(choisir_veille([e], MAINTENANT), [])

    def test_une_seule_fois(self):
        # Retenue à ce passage ; après la nouvelle prévision (maintenant), plus jamais retenue.
        notation = MAINTENANT + 23 * H
        avant = sq("a", notation=notation, derniere=MAINTENANT - 10 * J)
        self.assertEqual(len(choisir_veille([avant], MAINTENANT)), 1)
        apres = sq("a", notation=notation, derniere=MAINTENANT)
        for passage in range(0, 23 * 3):
            self.assertEqual(choisir_veille([apres], MAINTENANT + passage * timedelta(minutes=20)), [])

    def test_formats_de_date_et_objets(self):
        # Chaînes ISO (comme l'API), horodatage Unix (comme my_forecasts.start_time) et objets à attributs.
        e = SimpleNamespace(
            spot_scoring_time="2026-11-17T11:00:00Z",
            scheduled_close_time="2026-11-20T11:00:00Z",
            derniere_prevision=(MAINTENANT - 10 * J).timestamp(),
        )
        self.assertEqual(choisir_veille([e], MAINTENANT), [e])


class DatesDeQuestion(unittest.TestCase):
    def test_lecture_api_json(self):
        debut = (MAINTENANT - 10 * J).timestamp()
        q = SimpleNamespace(
            id_of_post=1, id_of_question=2, close_time=None,
            api_json={"question": {
                "spot_scoring_time": "2026-11-17T11:00:00Z",
                "scheduled_close_time": "2026-11-20T11:00:00Z",
                "my_forecasts": {"latest": {"start_time": debut}, "history": [{"start_time": debut - 5}]},
            }},
        )
        d = dates_de_question(q)
        self.assertEqual(d["spot_scoring_time"], "2026-11-17T11:00:00Z")
        self.assertEqual(d["derniere_prevision"], datetime.fromtimestamp(debut, timezone.utc))
        self.assertEqual(choisir_veille([d], MAINTENANT), [d])

    def test_repli_sur_history_puis_absence(self):
        q = SimpleNamespace(api_json={"question": {"my_forecasts": {"latest": None, "history": [
            {"start_time": 100.0}, {"start_time": 300.0}, {"start_time": 200.0}]}}}, close_time=None)
        self.assertEqual(dates_de_question(q)["derniere_prevision"], datetime.fromtimestamp(300.0, timezone.utc))
        vide = SimpleNamespace(api_json={}, close_time=None)
        self.assertIsNone(dates_de_question(vide)["derniere_prevision"])



class Compteur(unittest.TestCase):
    """Plafond persistant : deux choix au plus par sous-question, quoi que dise l'API."""

    def test_deux_choix_puis_plus_rien_meme_si_l_api_reste_perimee(self):
        compteur = {}
        e = sq("a", notation=MAINTENANT + 23 * H, cloture=MAINTENANT + 5 * J, derniere=MAINTENANT - 10 * J)
        e["id_question"] = 46248
        passages = 0
        for i in range(72):  # un passage toutes les 20 minutes pendant 24 heures, date de l'API jamais mise à jour
            choisies = choisir_veille(non_epuisees([e], compteur), MAINTENANT + i * 20 * timedelta(minutes=1))
            for c in choisies:
                marquer(compteur, c["id_question"], MAINTENANT)
                passages += 1
        self.assertEqual(passages, 2)

    def test_un_succes_ferme_la_sous_question(self):
        compteur = {}
        e = sq("a", notation=MAINTENANT + 23 * H, derniere=MAINTENANT - 10 * J)
        e["id_question"] = 1
        marquer(compteur, 1, MAINTENANT)
        self.assertEqual(noms(non_epuisees([e], compteur)), ["a"])
        marquer(compteur, 1, MAINTENANT, termine=True)
        self.assertEqual(non_epuisees([e], compteur), [])

    def test_sans_identifiant_jamais_choisie(self):
        e = sq("a", notation=MAINTENANT + 23 * H, derniere=MAINTENANT - 10 * J)
        self.assertEqual(non_epuisees([e], {}), [])

    def test_le_compteur_a_la_forme_du_fichier_de_tentatives(self):
        compteur = {"46016": {"attempts": 1, "last": "2026-11-16T12:00:00+00:00"}}
        marquer(compteur, 46248, MAINTENANT)
        self.assertEqual(compteur[cle_compteur(46248)]["attempts"], 1)
        self.assertIn("last", compteur[cle_compteur(46248)])
        self.assertEqual(compteur["46016"]["attempts"], 1)  # les clés du garde-fou ordinaire ne bougent pas

if __name__ == "__main__":
    unittest.main()
