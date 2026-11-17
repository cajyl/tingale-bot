"""Tests du branchement AskNews de Fall FutureEval (faux client, aucun appel réseau).

Lancement : python3 -m unittest test_asknews_fall
"""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import asknews_fall
from asknews_fall import (
    TITRE_ASKNEWS,
    TITRE_SONAR,
    AskNewsFall,
    CompteurMensuel,
    assembler,
    identifiants,
    interrupteur_allume,
    tournois_autorises,
)

FALL = "32813"
NOV = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
DEC = datetime(2026, 12, 1, 0, 5, tzinfo=timezone.utc)
ENV_OK = {"ASKNEWS_ENABLED": "1", "ASKNEWS_CLIENT_ID": "id-factice", "ASKNEWS_SECRET": "secret-factice"}


def article(titre, jour):
    return SimpleNamespace(
        eng_title=titre,
        summary=f"Résumé {titre}",
        language="en",
        pub_date=datetime(2026, 11, jour, 9, 0, tzinfo=timezone.utc),
        source_id="source",
        article_url=f"https://example.org/{titre}",
    )


class FauxClient:
    """Imite `AsyncAskNewsSDK` : gestionnaire de contexte asynchrone avec `news.search_news(...)`."""

    def __init__(self, articles=None, exception=None, attente=0.0):
        self.articles = articles if articles is not None else []
        self.exception = exception
        self.attente = attente
        self.appels = []
        self.ids_recus = None
        self.news = self

    def fabrique(self, ids):
        self.ids_recus = ids
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def search_news(self, **kwargs):
        self.appels.append(kwargs)
        if self.attente:
            await asyncio.sleep(self.attente)
        if self.exception is not None:
            raise self.exception
        return SimpleNamespace(as_dicts=self.articles)


class Base(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = Path(self.dossier.name) / "state" / "asknews.json"
        self.maintenant = NOV
        self.compteur = CompteurMensuel(self.chemin, plafond=300, horloge=lambda: self.maintenant)

    def tearDown(self):
        self.dossier.cleanup()

    def fichier(self):
        return json.loads(self.chemin.read_text(encoding="utf-8"))

    def branchement(self, client, env=ENV_OK, tournois=(FALL,), delai_s=30.0):
        return AskNewsFall(
            env, set(tournois), "essai", compteur=self.compteur, fabrique=client.fabrique, delai_s=delai_s
        )

    def lancer(self, a, url="https://www.metaculus.com/questions/1/"):
        with self.assertLogs(asknews_fall.logger, level="INFO") as journal:
            texte = asyncio.run(a.actualites(url, "Will X happen?"))
        lignes = [l for l in journal.output if "ASKNEWS question=" in l]
        self.assertEqual(len(lignes), 1)
        return texte, lignes[0]


class Interrupteur(Base):
    def test_valeurs(self):
        self.assertTrue(interrupteur_allume({"ASKNEWS_ENABLED": "1"}))
        for v in ("", "0", "true", "oui", "11"):
            self.assertFalse(interrupteur_allume({"ASKNEWS_ENABLED": v}))
        self.assertFalse(interrupteur_allume({}))

    def test_eteint_aucun_appel(self):
        client = FauxClient([article("a", 17)])
        a = self.branchement(client, env={**ENV_OK, "ASKNEWS_ENABLED": "0"})
        self.assertFalse(a.actif)
        self.assertFalse(a.concerne(FALL))
        self.assertIn("éteint", a.etat_au_demarrage())
        self.assertEqual(client.appels, [])
        self.assertFalse(self.chemin.exists())

    def test_identifiants_absents(self):
        a = self.branchement(FauxClient(), env={"ASKNEWS_ENABLED": "1"})
        self.assertFalse(a.actif)
        self.assertFalse(a.concerne(FALL))
        self.assertEqual(a.etat_au_demarrage(), "AskNews : éteint (aucun identifiant AskNews)")
        # Une moitié de paire seule ne suffit pas.
        self.assertIsNone(identifiants({"ASKNEWS_CLIENT_ID": "x"}))

    def test_la_paire_l_emporte_sur_la_cle(self):
        ids = identifiants({"ASKNEWS_CLIENT_ID": "i", "ASKNEWS_SECRET": "s", "ASKNEWS_API_KEY": "k"})
        self.assertEqual(ids, {"client_id": "i", "client_secret": "s", "api_key": None})
        ids = identifiants({"ASKNEWS_API_KEY": "k"})
        self.assertEqual(ids, {"client_id": None, "client_secret": None, "api_key": "k"})

    def test_etat_au_demarrage_sans_secret(self):
        ligne = self.branchement(FauxClient()).etat_au_demarrage()
        self.assertIn("allumé", ligne)
        self.assertIn("paire client", ligne)
        self.assertNotIn("secret-factice", ligne)
        self.assertNotIn("id-factice", ligne)


class FiltreDuTournoi(Base):
    def test_autre_tournoi_que_fall(self):
        a = self.branchement(FauxClient())
        self.assertTrue(a.concerne(FALL))
        self.assertTrue(a.concerne(int(FALL)))
        for autre in ("market-pulse-26q4", "32814", "bot-testing-area", None):
            self.assertFalse(a.concerne(autre), autre)

    def test_tournois_autorises_par_mode(self):
        self.assertEqual(tournois_autorises("tournament", 32813, True, None)[0], {"32813"})
        self.assertEqual(tournois_autorises("metaculus_cup", 32813, True, None)[0], set())
        self.assertEqual(tournois_autorises("test_questions", 32813, False, 2)[0], {"bot-testing-area"})
        self.assertEqual(tournois_autorises("test_questions", 32813, False, 1)[0], {"bot-testing-area"})
        # Essai seulement : sans publication, deux questions au plus.
        self.assertEqual(tournois_autorises("test_questions", 32813, True, 2)[0], set())
        self.assertEqual(tournois_autorises("test_questions", 32813, False, 3)[0], set())
        self.assertEqual(tournois_autorises("test_questions", 32813, False, None)[0], set())

    def test_sans_tournoi_autorise_eteint(self):
        a = AskNewsFall(ENV_OK, set(), "mode metaculus_cup non concerné", compteur=self.compteur)
        self.assertFalse(a.actif)
        self.assertIn("éteint", a.etat_au_demarrage())


class Appel(Base):
    def test_succes(self):
        client = FauxClient([article("ancien", 15), article("recent", 17)])
        a = self.branchement(client)
        texte, ligne = self.lancer(a)
        self.assertEqual(len(client.appels), 1)
        self.assertEqual(
            client.appels[0],
            {"query": "Will X happen?", "n_articles": 10, "return_type": "both", "strategy": "latest news"},
        )
        self.assertEqual(client.ids_recus["client_id"], "id-factice")
        self.assertLess(texte.index("recent"), texte.index("ancien"))  # du plus récent au plus ancien
        self.assertEqual(self.fichier(), {"mois": "2026-11", "appels": 1})
        self.assertIn("statut=ok articles=2", ligne)
        self.assertIn("appels_mois=1", ligne)
        self.assertNotIn("Résumé", ligne)  # aucun contenu d'article dans la ligne de journal
        rapport = assembler(texte, "SYNTHESE SONAR")
        self.assertTrue(rapport.startswith(TITRE_ASKNEWS))
        self.assertTrue(rapport.endswith(f"{TITRE_SONAR}\n\nSYNTHESE SONAR"))
        self.assertLess(rapport.index("recent"), rapport.index("SYNTHESE SONAR"))

    def test_succes_sans_article(self):
        a = self.branchement(FauxClient([]))
        texte, ligne = self.lancer(a)
        self.assertIn("No articles", texte)
        self.assertIn("statut=ok articles=0", ligne)

    def test_compteur_augmente_d_un_passage_a_l_autre(self):
        self.chemin.parent.mkdir(parents=True)
        self.chemin.write_text(json.dumps({"mois": "2026-11", "appels": 41}), encoding="utf-8")
        texte, ligne = self.lancer(self.branchement(FauxClient([article("a", 17)])))
        self.assertIsNotNone(texte)
        self.assertEqual(self.fichier()["appels"], 42)
        self.assertIn("appels_mois=42", ligne)

    def test_plafond_atteint(self):
        self.chemin.parent.mkdir(parents=True)
        self.chemin.write_text(json.dumps({"mois": "2026-11", "appels": 300}), encoding="utf-8")
        client = FauxClient([article("a", 17)])
        texte, ligne = self.lancer(self.branchement(client))
        self.assertIsNone(texte)
        self.assertEqual(client.appels, [])
        self.assertEqual(self.fichier()["appels"], 300)
        self.assertIn("statut=plafond", ligne)
        self.assertEqual(assembler(texte, "SONAR"), "SONAR")

    def test_exception_donc_repli(self):
        client = FauxClient(exception=RuntimeError("compte en pause " + "x" * 500))
        texte, ligne = self.lancer(self.branchement(client))
        self.assertIsNone(texte)
        self.assertIn("statut=repli", ligne)
        self.assertIn("erreur=RuntimeError", ligne)
        self.assertNotIn("compte en pause", ligne)  # type seulement, pas le message
        self.assertEqual(self.fichier()["appels"], 1)  # compté avant l'appel
        self.assertEqual(len(client.appels), 1)  # sans nouvel essai

    def test_delai_depasse_donc_repli(self):
        client = FauxClient([article("a", 17)], attente=1.0)
        texte, ligne = self.lancer(self.branchement(client, delai_s=0.05))
        self.assertIsNone(texte)
        self.assertIn("statut=repli", ligne)
        self.assertIn("erreur=TimeoutError", ligne)
        self.assertEqual(len(client.appels), 1)
        self.assertEqual(self.fichier()["appels"], 1)

    def test_fabrique_qui_echoue_donc_repli(self):
        def fabrique(ids):
            raise ImportError("asknews_sdk absent")

        a = AskNewsFall(ENV_OK, {FALL}, "essai", compteur=self.compteur, fabrique=fabrique)
        texte, ligne = self.lancer(a)
        self.assertIsNone(texte)
        self.assertIn("erreur=ImportError", ligne)

    def test_compteur_illisible_repart_de_zero(self):
        self.chemin.parent.mkdir(parents=True)
        self.chemin.write_text("pas du json", encoding="utf-8")
        self.lancer(self.branchement(FauxClient([article("a", 17)])))
        self.assertEqual(self.fichier(), {"mois": "2026-11", "appels": 1})

    def test_ecriture_impossible_donc_repli_sans_appel(self):
        self.chemin.parent.mkdir(parents=True)
        self.chemin.mkdir()  # un dossier à la place du fichier : l'écriture lève OSError
        client = FauxClient([article("a", 17)])
        texte, ligne = self.lancer(self.branchement(client))
        self.assertIsNone(texte)
        self.assertEqual(client.appels, [])
        self.assertIn("statut=repli", ligne)


class ChangementDeMois(Base):
    def test_nouveau_mois_repart_de_zero(self):
        self.chemin.parent.mkdir(parents=True)
        self.chemin.write_text(json.dumps({"mois": "2026-11", "appels": 300}), encoding="utf-8")
        self.assertEqual(self.compteur.reserver(), (False, 300))
        self.maintenant = DEC
        self.assertEqual(self.compteur.lire(), 0)
        self.assertEqual(self.compteur.reserver(), (True, 1))
        self.assertEqual(self.fichier(), {"mois": "2026-12", "appels": 1})

    def test_mois_en_utc(self):
        from datetime import timedelta

        # 30/11 à 23:30 à UTC-2 = 01/12 à 01:30 UTC : décembre.
        self.maintenant = datetime(2026, 11, 30, 23, 30, tzinfo=timezone(timedelta(hours=-2)))
        self.compteur.reserver()
        self.assertEqual(self.fichier()["mois"], "2026-12")


class Assemblage(unittest.TestCase):
    def test_sans_bloc_sonar_inchange(self):
        self.assertEqual(assembler(None, "texte sonar\n"), "texte sonar\n")


if __name__ == "__main__":
    unittest.main()
