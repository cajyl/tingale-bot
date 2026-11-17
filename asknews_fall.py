"""AskNews sur Fall FutureEval : un appel « latest news » par question, en tête du rapport de recherche.

Pré-enregistrement E001 du 17/11/2026 (dépôt augustin-gale), avis risk-compliance du 16/11 (conditions 8 et 9) :

- interrupteur `ASKNEWS_ENABLED` (variable du dépôt), allumé seulement si sa valeur vaut "1" ; éteint, ou sans
  identifiants AskNews, le robot se comporte exactement comme avant : aucun appel, aucune ligne par question ;
- Fall FutureEval seulement (ni Market Pulse, ni MiniBench, ni la reprise de Market Pulse) ; la zone de test
  n'est admise que pour un passage d'essai sans publication, deux questions au plus ;
- un appel par question, stratégie « latest news » (dernières 48 heures), 10 articles, 30 secondes au plus, sans
  nouvel essai ; aucune recherche dans les archives (« news knowledge »), aucun DeepNews ;
- compteur d'appels par mois civil (UTC) dans `state/asknews.json`, compté AVANT l'appel ; plafond de 300 par
  mois, au plafond sonar seul jusqu'au mois suivant ;
- toute erreur ou tout dépassement de délai : sonar seul pour cette question ; rien ne remonte au robot.

Bibliothèque standard seulement, pour être testé sans les dépendances du robot
(`python3 -m unittest test_asknews_fall`). Le SDK `asknews_sdk` n'est importé qu'au moment d'un vrai appel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

VARIABLE_INTERRUPTEUR = "ASKNEWS_ENABLED"
PLAFOND_MENSUEL = 300
DELAI_S = 30.0
N_ARTICLES = 10
STRATEGIE = "latest news"
ZONE_DE_TEST = "bot-testing-area"
MAX_QUESTIONS_ESSAI = 2
CHEMIN_COMPTEUR = Path(__file__).resolve().parent / "state" / "asknews.json"

# Titres du rapport de recherche. Ils sont lus par le modèle de prévision, dont les consignes (gabarit) sont en
# anglais : ils restent en anglais pour ne pas mélanger les langues dans l'invite.
TITRE_ASKNEWS = "## Latest news (AskNews, past 48 hours)"
TITRE_SONAR = "## Research summary"


##################################### INTERRUPTEUR ET IDENTIFIANTS #####################################


def interrupteur_allume(env: Mapping[str, str]) -> bool:
    """Même règle que MARKET_PULSE_ENABLED et MINIBENCH_ENABLED : allumé si la valeur vaut "1"."""
    return (env.get(VARIABLE_INTERRUPTEUR) or "").strip() == "1"


def identifiants(env: Mapping[str, str]) -> dict | None:
    """Arguments du client AskNews, selon la règle de forecasting-tools 0.3.4 : la paire
    ASKNEWS_CLIENT_ID + ASKNEWS_SECRET l'emporte sur ASKNEWS_API_KEY ; rien du tout donne None."""
    client_id = (env.get("ASKNEWS_CLIENT_ID") or "").strip()
    secret = (env.get("ASKNEWS_SECRET") or "").strip()
    cle = (env.get("ASKNEWS_API_KEY") or "").strip()
    if client_id and secret:
        return {"client_id": client_id, "client_secret": secret, "api_key": None}
    if cle:
        return {"client_id": None, "client_secret": None, "api_key": cle}
    return None


def nature_identifiants(ids: dict | None) -> str:
    """Pour le journal : la forme des identifiants, jamais leur valeur."""
    if not ids:
        return "aucun"
    return "paire client" if ids.get("client_id") else "clé d'API"


def tournois_autorises(
    mode: str,
    id_fall: Any,
    publie: bool,
    max_questions: int | None,
) -> tuple[set[str], str]:
    """Tournois où AskNews s'applique, et la raison pour le journal.

    - mode « tournament » : Fall FutureEval seulement (condition 8 de l'avis du 16/11) ;
    - mode « test_questions » : la zone de test, seulement pour un passage sans publication de deux questions
      au plus (essai prévu par le pré-enregistrement du 17/11) ;
    - tout autre mode : aucun.
    """
    if mode == "tournament":
        return {str(id_fall)}, f"Fall FutureEval ({id_fall}) seulement"
    if mode == "test_questions":
        if publie or max_questions is None or not (1 <= max_questions <= MAX_QUESTIONS_ESSAI):
            return set(), (
                f"zone de test refusée : essai admis sans publication et avec {MAX_QUESTIONS_ESSAI} questions au plus"
            )
        return {ZONE_DE_TEST}, f"zone de test seulement (essai, {max_questions} question(s) au plus, sans publication)"
    return set(), f"mode {mode} non concerné"


##################################### COMPTEUR MENSUEL #####################################


def mois_de(instant: datetime) -> str:
    """Mois civil UTC, « AAAA-MM »."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).strftime("%Y-%m")


class CompteurMensuel:
    """Appels AskNews du mois civil (UTC), dans un fichier à part : `{"mois": "AAAA-MM", "appels": n}`.

    Le fichier est relu à chaque réservation (un passage qui franchit minuit le dernier jour du mois repart à zéro)
    et réécrit avant l'appel : un passage interrompu après la réservation compte quand même."""

    def __init__(
        self,
        chemin: Path = CHEMIN_COMPTEUR,
        plafond: int = PLAFOND_MENSUEL,
        horloge: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.chemin = Path(chemin)
        self.plafond = plafond
        self.horloge = horloge

    def lire(self) -> int:
        """Appels déjà comptés ce mois-ci (0 si fichier absent, illisible ou d'un autre mois)."""
        mois = mois_de(self.horloge())
        try:
            data = json.loads(self.chemin.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict) or data.get("mois") != mois:
            return 0
        appels = data.get("appels")
        return appels if isinstance(appels, int) and appels >= 0 else 0

    def reserver(self) -> tuple[bool, int]:
        """Compte un appel s'il reste de la place sous le plafond et l'écrit sur disque.
        Rend (réservé, appels du mois après l'opération). Lève OSError si l'écriture échoue."""
        mois = mois_de(self.horloge())
        appels = self.lire()
        if appels >= self.plafond:
            return False, appels
        appels += 1
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self.chemin.write_text(json.dumps({"mois": mois, "appels": appels}) + "\n", encoding="utf-8")
        return True, appels


##################################### MISE EN FORME #####################################


def _champ(article: Any, nom: str) -> Any:
    if isinstance(article, dict):
        return article.get(nom)
    return getattr(article, nom, None)


def formater_articles(articles: list) -> str:
    """Même présentation que forecasting-tools 0.3.4 (`AskNewsSearcher._format_articles`), du plus récent au
    plus ancien ; tolère un champ manquant plutôt que d'échouer."""

    def cle_tri(a: Any):
        d = _champ(a, "pub_date")
        if isinstance(d, datetime):
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        return datetime.min.replace(tzinfo=timezone.utc)

    texte = ""
    for a in sorted(articles, key=cle_tri, reverse=True):
        d = _champ(a, "pub_date")
        date = d.strftime("%B %d, %Y %I:%M %p") if isinstance(d, datetime) else "unknown"
        texte += (
            f"**{_champ(a, 'eng_title') or ''}**\n{_champ(a, 'summary') or ''}\n"
            f"Original language: {_champ(a, 'language') or ''}\nPublish date: {date}\n"
            f"Source:[{_champ(a, 'source_id') or ''}]({_champ(a, 'article_url') or ''})\n\n"
        )
    return texte


def assembler(bloc_asknews: str | None, recherche_sonar: str) -> str:
    """Bloc AskNews en tête, sous son titre, puis la synthèse de sonar inchangée. Sans bloc : sonar tel quel."""
    if bloc_asknews is None:
        return recherche_sonar
    return f"{TITRE_ASKNEWS}\n\n{bloc_asknews.strip()}\n\n{TITRE_SONAR}\n\n{recherche_sonar}"


##################################### APPEL #####################################


def fabrique_sdk(ids: dict):
    """Client asynchrone réel, construit comme dans forecasting-tools 0.3.4 (portée « news » seulement).
    Importé ici et pas en tête de module : l'import du module ne dépend jamais du SDK."""
    from asknews_sdk import AsyncAskNewsSDK

    return AsyncAskNewsSDK(
        client_id=ids.get("client_id"),
        client_secret=ids.get("client_secret"),
        api_key=ids.get("api_key"),
        scopes={"news"},
    )


class AskNewsFall:
    """Branchement AskNews du robot. `actif` est faux si l'interrupteur est éteint ou sans identifiants ;
    `actualites` ne lève jamais d'exception ordinaire (repli sur sonar seul)."""

    def __init__(
        self,
        env: Mapping[str, str],
        tournois: set[str],
        raison: str = "",
        compteur: CompteurMensuel | None = None,
        fabrique: Callable[[dict], Any] = fabrique_sdk,
        delai_s: float = DELAI_S,
        chrono: Callable[[], float] = time.monotonic,
    ):
        self.allume = interrupteur_allume(env)
        self.ids = identifiants(env)
        self.tournois = {str(t) for t in tournois}
        self.raison = raison
        self.compteur = compteur or CompteurMensuel()
        self.fabrique = fabrique
        self.delai_s = delai_s
        self.chrono = chrono

    @property
    def actif(self) -> bool:
        return self.allume and self.ids is not None and bool(self.tournois)

    def concerne(self, tournoi: Any) -> bool:
        """Vrai seulement si AskNews est actif et que le tournoi en cours est autorisé."""
        return self.actif and tournoi is not None and str(tournoi) in self.tournois

    def etat_au_demarrage(self) -> str:
        """Une ligne pour le journal du passage."""
        if not self.allume:
            return f"AskNews : éteint ({VARIABLE_INTERRUPTEUR} différent de 1)"
        if self.ids is None:
            return "AskNews : éteint (aucun identifiant AskNews)"
        if not self.tournois:
            return f"AskNews : éteint ({self.raison})"
        return (
            f"AskNews : allumé, {self.raison} ; {self.compteur.lire()} appel(s) ce mois-ci sur "
            f"{self.compteur.plafond} ; identifiants : {nature_identifiants(self.ids)}"
        )

    async def _appel(self, requete: str) -> list:
        async with self.fabrique(self.ids) as ask:
            reponse = await ask.news.search_news(
                query=requete,
                n_articles=N_ARTICLES,
                return_type="both",
                strategy=STRATEGIE,
            )
        return list(reponse.as_dicts or [])

    async def actualites(self, url: str, requete: str) -> str | None:
        """Texte des articles à placer en tête du rapport, ou None (sonar seul). Une ligne ASKNEWS au journal."""
        debut = self.chrono()
        statut, n, erreur, appels = "repli", 0, "", 0
        texte: str | None = None
        try:
            reserve, appels = self.compteur.reserver()
            if not reserve:
                statut = "plafond"
            else:
                articles = await asyncio.wait_for(self._appel(requete), timeout=self.delai_s)
                n = len(articles)
                texte = formater_articles(articles) if articles else "No articles were found.\n"
                statut = "ok"
        except Exception as exc:  # repli : la question continue avec sonar seul
            statut, texte, erreur = "repli", None, type(exc).__name__
        duree = self.chrono() - debut
        logger.info(
            f"ASKNEWS question={url} statut={statut} articles={n} duree_s={duree:.1f} appels_mois={appels}"
            + (f" erreur={erreur}" if erreur else "")
        )
        return texte
