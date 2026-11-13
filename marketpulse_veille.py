"""Market Pulse : choix des sous-questions à reprévoir la veille de leur instant de notation.

Pré-enregistrement E001 du 13/11/2026 (dépôt augustin-gale) : sur Market Pulse seulement, le robot refait
une fois sa prévision de chaque sous-question dans les 24 heures qui précèdent l'instant de notation
(`spot_scoring_time`, à défaut la clôture prévue `scheduled_close_time`), si sa dernière prévision est
antérieure à (instant de notation moins 24 heures). Six au plus par passage, les plus proches de la
notation d'abord ; une sous-question dont l'instant de notation est passé n'est jamais refaite.

Une seule prévision refaite par sous-question, sans état à conserver : après la nouvelle prévision, la
dernière prévision du robot est postérieure à (notation moins 24 heures), la condition devient fausse.

Bibliothèque standard seulement, pour être testé sans les dépendances du robot
(`python3 -m unittest test_marketpulse_veille`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

MARKET_PULSE_ID = "market-pulse-26q4"
FENETRE = timedelta(hours=24)
MAXIMUM_PAR_PASSAGE = 6

# Noms des trois dates attendues par la fonction de sélection (clés d'un dictionnaire ou attributs).
CLE_NOTATION = "spot_scoring_time"
CLE_CLOTURE = "scheduled_close_time"
CLE_DERNIERE = "derniere_prevision"


def en_date(valeur: Any) -> datetime | None:
    """Datetime UTC depuis un datetime, une chaîne ISO 8601 ou un horodatage Unix ; sinon None."""
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, datetime):
        return valeur if valeur.tzinfo else valeur.replace(tzinfo=timezone.utc)
    if isinstance(valeur, (int, float)):
        return datetime.fromtimestamp(valeur, timezone.utc)
    if isinstance(valeur, str) and valeur.strip():
        try:
            d = datetime.fromisoformat(valeur.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def _lire(element: Any, cle: str) -> Any:
    if isinstance(element, dict):
        return element.get(cle)
    return getattr(element, cle, None)


def instant_de_notation(element: Any) -> datetime | None:
    """`spot_scoring_time`, à défaut la clôture prévue."""
    return en_date(_lire(element, CLE_NOTATION)) or en_date(_lire(element, CLE_CLOTURE))


def choisir_veille(
    elements: Iterable[Any],
    maintenant: datetime,
    fenetre: timedelta = FENETRE,
    maximum: int = MAXIMUM_PAR_PASSAGE,
) -> list[Any]:
    """Fonction pure : renvoie les éléments à reprévoir maintenant, les plus proches de la notation d'abord.

    Chaque élément (dictionnaire ou objet) porte `spot_scoring_time`, `scheduled_close_time` et
    `derniere_prevision` (datetime, chaîne ISO ou horodatage ; None si absent). Un élément est retenu si :
    - son instant de notation T (spot, à défaut clôture) est connu et strictement à venir ;
    - T tombe dans la fenêtre : T - maintenant <= 24 h ;
    - le robot a déjà une prévision, et la dernière est strictement antérieure à T - 24 h.
    Au plus `maximum` éléments ; les autres attendent le passage suivant.
    """
    maintenant = en_date(maintenant)
    if maintenant is None:
        raise ValueError("maintenant : date attendue")
    retenus: list[tuple[datetime, int, Any]] = []
    for rang, element in enumerate(elements):
        notation = instant_de_notation(element)
        derniere = en_date(_lire(element, CLE_DERNIERE))
        if notation is None or derniere is None:
            continue
        if notation <= maintenant:
            continue  # notation passée (ou à l'instant même) : jamais refaite
        if notation - maintenant > fenetre:
            continue  # pas encore dans les 24 heures
        if derniere >= notation - fenetre:
            continue  # déjà prévue dans la fenêtre (refaite, ou ouverte tard)
        retenus.append((notation, rang, element))
    retenus.sort(key=lambda t: (t[0], t[1]))
    return [element for _, _, element in retenus[: max(0, maximum)]]


def dates_de_question(question: Any) -> dict:
    """Les trois dates d'une sous-question de forecasting-tools 0.3.4, lues dans `api_json["question"]`.

    Pour une sous-question de groupe, le client (`MetaculusClient._unpack_group_question`) recopie le JSON
    de la sous-question dans `api_json["question"]` : on y lit `spot_scoring_time`, `scheduled_close_time`
    (aussi exposé en `close_time`) et `my_forecasts.latest.start_time` (lu de même par la propriété
    `timestamp_of_my_last_forecast` du gabarit), à défaut le plus récent `start_time` de
    `my_forecasts.history` (champ dont dépend `already_forecasted`).
    """
    api_json = getattr(question, "api_json", None) or {}
    qj = api_json.get("question") or {}
    mf = qj.get("my_forecasts") or {}
    derniere = en_date((mf.get("latest") or {}).get("start_time")) if isinstance(mf.get("latest"), dict) else None
    if derniere is None:
        debuts = [en_date(h.get("start_time")) for h in (mf.get("history") or []) if isinstance(h, dict)]
        debuts = [d for d in debuts if d is not None]
        derniere = max(debuts) if debuts else None
    return {
        "question": question,
        "id_post": getattr(question, "id_of_post", None),
        "id_question": getattr(question, "id_of_question", None),
        CLE_NOTATION: qj.get(CLE_NOTATION),
        CLE_CLOTURE: qj.get(CLE_CLOTURE) or getattr(question, "close_time", None),
        CLE_DERNIERE: derniere,
    }


def fmt(valeur: Any) -> str:
    d = en_date(valeur)
    return d.strftime("%Y-%m-%d %H:%M UTC") if d else "-"
