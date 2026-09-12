"""Traduction des métriques d'un device Sparkplug B en événements MES.

Toute mutation d'état passe par `event_service.enregistrer_evenement` : le
journal d'événements reste la source de vérité (TRS recalculé à la lecture).

Règles :
- **Compteurs** (bonnes pièces, rejets) : l'automate publie des TOTAUX
  monotones ; le MES compte le DELTA depuis la dernière valeur connue,
  persistée dans `SparkplugDevice.last_values`. Première lecture = simple
  référence ; une régression (remise à zéro de l'automate) = nouvelle
  référence, sans rien compter. Une naissance après une coupure de l'hôte
  compte donc bien la production réalisée pendant la coupure.
- **État** : comparé à l'état MES, chaque transition est traduite en
  événements (`_appliquer_transition`). Le contexte d'une commande acceptée
  (OF, type de maintenance, commentaire) enrichit ces événements.
- **Ordre** : une transition vers MARCHE s'applique AVANT les compteurs (la
  production suit le démarrage) ; toute autre transition APRÈS (dernières
  pièces avant l'arrêt ou la panne).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Machine, OrdreFabrication
from app.models.enums import (
    CauseArret,
    CauseRebut,
    StatutMachine,
    StatutOF,
    TypeEvenementMachine,
    TypeMaintenance,
)
from app.models.sparkplug import SparkplugDevice, SparkplugTagMapping, TagTransformation, TargetKpi
from app.protocols.sparkplug_b.codec import MetricValue, Payload
from app.protocols.sparkplug_b.contract import (
    CMD_LAST_ID,
    CMD_LAST_RESULT,
    METRIC_FAULT_CAUSE,
    METRIC_GOOD_COUNT,
    METRIC_OPERATOR_CARD,
    METRIC_OPERATOR_ID,
    METRIC_OPERATOR_NAME,
    METRIC_ORDER_NUMBER,
    METRIC_RATE,
    METRIC_REJECT_CAUSE,
    METRIC_REJECT_COUNT,
    METRIC_STATE,
    METRIC_TEMPERATURE,
    OPERATOR_CARD_CAUSES,
    OPERATOR_CARD_RESUME,
    lire_resultat,
)
from app.services import downtime_service, event_service

if TYPE_CHECKING:
    from app.services.machine_command_service import CommandRegistry

logger = get_logger(__name__)

DEFAULT_MAPPINGS: tuple[tuple[str, TargetKpi, TagTransformation], ...] = (
    (METRIC_STATE, TargetKpi.STATUT_MACHINE, TagTransformation.DIRECT),
    (METRIC_FAULT_CAUSE, TargetKpi.CAUSE_ARRET, TagTransformation.DIRECT),
    (METRIC_GOOD_COUNT, TargetKpi.BONNES_PIECES, TagTransformation.DIRECT),
    (METRIC_REJECT_COUNT, TargetKpi.REJETS, TagTransformation.DIRECT),
    (METRIC_REJECT_CAUSE, TargetKpi.CAUSE_REBUT, TagTransformation.DIRECT),
    (METRIC_RATE, TargetKpi.CADENCE, TagTransformation.DIRECT),
    (METRIC_TEMPERATURE, TargetKpi.TEMPERATURE, TagTransformation.DIRECT),
    (METRIC_OPERATOR_CARD, TargetKpi.OPERATOR_CARD, TagTransformation.OPERATOR_CARD),
)

# KPI capteur → nom du tag dans le journal MES (SENSOR_TAG_UPDATED).
_CAPTEURS: dict[TargetKpi, str] = {
    TargetKpi.TEMPERATURE: "temperature_C",
    TargetKpi.PUISSANCE: "puissance_kW",
}
# En dessous de 1 % d'écart, un nouveau temps de cycle n'est pas un événement.
SEUIL_VARIATION_CADENCE = 0.01

_E = TypeVar("_E", bound=Enum)


@dataclass(frozen=True)
class _Lecture:
    tag: str
    brute: MetricValue
    valeur: Any  # après transformation


@dataclass(frozen=True)
class ResultatMapping:
    machine_id: int | None
    ack: tuple[str, bool, str | None] | None
    evenements: tuple[str, ...]


def valeur_json(valeur: MetricValue) -> Any:
    return valeur.hex() if isinstance(valeur, bytes) else valeur


def lire_ack(payload: Payload) -> tuple[str, bool, str | None] | None:
    """Accusé de commande porté par le message : (commande_id, acceptée, raison)."""
    commande_id = payload.value(CMD_LAST_ID)
    resultat = payload.value(CMD_LAST_RESULT)
    if not commande_id or not isinstance(resultat, str):
        return None
    acceptee, raison = lire_resultat(resultat)
    return str(commande_id), acceptee, raison


def creer_mappings_par_defaut(db: Session, device: SparkplugDevice, payload: Payload) -> int:
    """Règles du contrat Nova pour les métriques présentes dans la naissance."""
    presents = {m.name for m in payload.metrics if m.name}
    crees = 0
    for tag, kpi, transformation in DEFAULT_MAPPINGS:
        if tag in presents:
            device.mappings.append(
                SparkplugTagMapping(
                    tag_name=tag,
                    target_kpi=kpi,
                    transformation=transformation,
                    description="Règle par défaut (contrat Nova)",
                )
            )
            crees += 1
    db.flush()
    return crees


def transformer_valeur(valeur: MetricValue, regle: SparkplugTagMapping) -> Any:
    """Applique la transformation configurée. None = valeur inexploitable."""
    transformation = regle.transformation
    try:
        if transformation == TagTransformation.SCALE_FACTOR:
            return float(valeur) * float(regle.formula_param or "1")  # type: ignore[arg-type]
        if transformation == TagTransformation.THRESHOLD_STATE:
            seuil = float(regle.formula_param or "0")
            actif = float(valeur) > seuil  # type: ignore[arg-type]
            return (StatutMachine.MARCHE if actif else StatutMachine.ARRET).value
        if transformation == TagTransformation.OPERATOR_CARD:
            return str(valeur).strip().upper()
    except (TypeError, ValueError):
        logger.warning(
            "sparkplug_transformation_impossible",
            tag=regle.tag_name,
            transformation=transformation.value,
            valeur=valeur,
        )
        return None
    return valeur


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #
def appliquer(
    db: Session,
    device: SparkplugDevice,
    payload: Payload,
    *,
    naissance: bool,
    commandes: CommandRegistry,
) -> ResultatMapping:
    ack = lire_ack(payload)
    contexte = commandes.contexte(ack[0]) if ack is not None and ack[1] else {}
    precedentes = dict(device.last_values or {})
    journal: list[str] = []

    machine = db.get(Machine, device.machine_id) if device.machine_id is not None else None
    if machine is not None and machine.actif:
        kpis = _kpis(device, payload)
        etat = _lire_statut(kpis.get(TargetKpi.STATUT_MACHINE))
        cause = _valeur_enum(
            CauseArret,
            _derniere_valeur(device, TargetKpi.CAUSE_ARRET, kpis, precedentes),
            CauseArret.PANNE_MECANIQUE,
        )
        numero_of = payload.value(METRIC_ORDER_NUMBER)

        def transition() -> None:
            if etat is not None:
                _appliquer_transition(
                    db, machine, etat, cause=cause, contexte=contexte,
                    numero_of_automate=numero_of, journal=journal,
                )

        if etat == StatutMachine.MARCHE:
            transition()
        _appliquer_compteurs(db, machine, device, kpis, precedentes, journal)
        if etat != StatutMachine.MARCHE:
            transition()
        _appliquer_cadence(db, machine, kpis, journal)
        _appliquer_capteurs(db, machine, kpis, precedentes, journal)
        if not naissance:
            _appliquer_badge(db, machine, kpis, journal)
        _suivre_operateur(payload, precedentes, journal)

    # Nouvelle référence pour les compteurs et la détection de changement.
    device.last_values = {
        **precedentes,
        **{nom: valeur_json(v) for nom, v in payload.as_dict().items()},
    }
    return ResultatMapping(
        machine_id=machine.id if machine is not None else None,
        ack=ack,
        evenements=tuple(journal),
    )


def _suivre_operateur(payload: Payload, precedentes: dict[str, Any], journal: list[str]) -> None:
    """Badge employé : l'automate publie qui est au poste (`Operator/Id`).

    Rien n'est écrit en base : la valeur vit dans `last_values`, lue par
    `machine_read_service`. Un changement est journalisé, ce qui déclenche la
    diffusion de la machine aux clients.
    """
    valeurs = payload.as_dict()
    if METRIC_OPERATOR_ID not in valeurs:
        return
    nouveau = str(valeurs.get(METRIC_OPERATOR_ID) or "")
    if nouveau == str(precedentes.get(METRIC_OPERATOR_ID) or ""):
        return
    nom = str(valeurs.get(METRIC_OPERATOR_NAME) or "")
    journal.append(f"opérateur au poste : {nom or nouveau}" if nouveau else "poste libéré")


# --------------------------------------------------------------------------- #
# Lecture des KPI
# --------------------------------------------------------------------------- #
def _kpis(device: SparkplugDevice, payload: Payload) -> dict[TargetKpi, _Lecture]:
    valeurs = payload.as_dict()
    lectures: dict[TargetKpi, _Lecture] = {}
    for regle in device.mappings:
        if not regle.actif or regle.tag_name not in valeurs:
            continue
        brute = valeurs[regle.tag_name]
        if brute is None:
            continue
        transformee = transformer_valeur(brute, regle)
        if transformee is not None:
            lectures[regle.target_kpi] = _Lecture(regle.tag_name, brute, transformee)
    return lectures


def _derniere_valeur(
    device: SparkplugDevice, kpi: TargetKpi, kpis: dict[TargetKpi, _Lecture], precedentes: dict
) -> Any:
    """Valeur du KPI dans ce message, à défaut la dernière valeur connue
    (report par exception : une cause inchangée n'est pas republiée)."""
    lecture = kpis.get(kpi)
    if lecture is not None:
        return lecture.valeur
    for regle in device.mappings:
        if regle.actif and regle.target_kpi == kpi and precedentes.get(regle.tag_name) is not None:
            return precedentes[regle.tag_name]
    return None


def _valeur_enum(enum: type[_E], valeur: Any, defaut: _E) -> _E:
    # Chaîne vide = « pas de cause » (ex. `Machine/FaultCause` hors panne).
    if valeur is None or str(valeur).strip() == "":
        return defaut
    try:
        return enum(str(valeur).strip().upper())
    except ValueError:
        logger.warning("sparkplug_valeur_inconnue", enum=enum.__name__, valeur=valeur)
        return defaut


def _lire_statut(lecture: _Lecture | None) -> StatutMachine | None:
    if lecture is None:
        return None
    try:
        return StatutMachine(str(lecture.valeur).strip().upper())
    except ValueError:
        logger.warning("sparkplug_etat_inconnu", tag=lecture.tag, valeur=lecture.valeur)
        return None


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
def _appliquer_transition(
    db: Session,
    machine: Machine,
    nouveau: StatutMachine,
    *,
    cause: CauseArret,
    contexte: dict,
    numero_of_automate: MetricValue,
    journal: list[str],
) -> None:
    ancien = machine.statut
    if nouveau == ancien:
        return
    meta = {"source": "sparkplug", **{k: contexte[k] for k in ("commande_id", "commande") if k in contexte}}
    commentaire = contexte.get("comment")

    def evenement(type_: TypeEvenementMachine, donnees: dict | None = None) -> None:
        event_service.enregistrer_evenement(
            db, machine=machine, type_evenement=type_, payload={**(donnees or {}), **meta}
        )
        journal.append(type_.value)

    if nouveau in (StatutMachine.MARCHE, StatutMachine.ARRET):
        if ancien == StatutMachine.PANNE:
            evenement(TypeEvenementMachine.DOWNTIME_RESOLVED, {"comment": commentaire} if commentaire else None)
        elif ancien == StatutMachine.MAINTENANCE:
            prochaine = contexte.get("prochaine_maintenance")
            evenement(
                TypeEvenementMachine.MAINTENANCE_ENDED,
                {"prochaine_maintenance": prochaine} if prochaine else None,
            )
        if nouveau == StatutMachine.MARCHE and machine.statut != StatutMachine.MARCHE:
            of_id = contexte.get("ordre_fabrication_id") or _of_charge(db, machine, numero_of_automate)
            evenement(
                TypeEvenementMachine.MACHINE_STARTED,
                {"ordre_fabrication_id": of_id} if of_id else None,
            )
        elif nouveau == StatutMachine.ARRET and machine.statut != StatutMachine.ARRET:
            evenement(TypeEvenementMachine.MACHINE_STOPPED, {"comment": commentaire} if commentaire else None)
    elif nouveau == StatutMachine.PAUSE:
        evenement(TypeEvenementMachine.MACHINE_IDLE, {"pause": True})
    elif nouveau == StatutMachine.PANNE:
        evenement(
            TypeEvenementMachine.DOWNTIME_STARTED,
            {"cause": cause.value, "comment": commentaire or "Défaut signalé par l'automate"},
        )
    elif nouveau == StatutMachine.MAINTENANCE:
        evenement(
            TypeEvenementMachine.MAINTENANCE_STARTED,
            {
                "type": contexte.get("type", TypeMaintenance.CORRECTIVE.value),
                "description": contexte.get("description"),
            },
        )


def _of_charge(db: Session, machine: Machine, numero: MetricValue) -> int | None:
    """OF annoncé par l'automate (`Production/OrderNumber`) quand la machine
    démarre sans commande Nova (ex. relance locale au pupitre)."""
    if machine.ordre_fabrication_id is not None or not numero:
        return None
    of = db.execute(
        select(OrdreFabrication).where(
            OrdreFabrication.numero == str(numero),
            OrdreFabrication.ligne_production_id == machine.ligne_production_id,
            OrdreFabrication.statut.in_((StatutOF.BROUILLON, StatutOF.PLANIFIE, StatutOF.EN_COURS)),
        )
    ).scalar_one_or_none()
    if of is None:
        logger.info("sparkplug_of_automate_inconnu", machine=machine.code, numero=numero)
        return None
    porteuse = db.execute(
        select(Machine.id).where(Machine.ordre_fabrication_id == of.id, Machine.id != machine.id)
    ).first()
    return None if porteuse is not None else of.id


def _delta(lecture: _Lecture, precedentes: dict, device_id: str) -> int:
    try:
        valeur = int(lecture.valeur)
    except (TypeError, ValueError):
        logger.warning("sparkplug_compteur_illisible", device=device_id, tag=lecture.tag)
        return 0
    base = precedentes.get(lecture.tag)
    if base is None:
        return 0  # première lecture : simple référence
    try:
        base = int(base)
    except (TypeError, ValueError):
        return 0
    if valeur < base:
        logger.warning(
            "sparkplug_compteur_regression",
            device=device_id,
            tag=lecture.tag,
            precedent=base,
            recu=valeur,
        )
        return 0
    return valeur - base


def _appliquer_compteurs(
    db: Session,
    machine: Machine,
    device: SparkplugDevice,
    kpis: dict[TargetKpi, _Lecture],
    precedentes: dict,
    journal: list[str],
) -> None:
    for kpi, type_ in (
        (TargetKpi.BONNES_PIECES, TypeEvenementMachine.GOOD_UNIT_PRODUCED),
        (TargetKpi.REJETS, TypeEvenementMachine.SCRAP_UNIT_PRODUCED),
    ):
        lecture = kpis.get(kpi)
        if lecture is None:
            continue
        delta = _delta(lecture, precedentes, device.device_id)
        if delta <= 0:
            continue
        donnees: dict = {"quantite": delta, "source": "sparkplug"}
        if kpi == TargetKpi.REJETS:
            donnees["cause"] = _valeur_enum(
                CauseRebut,
                _derniere_valeur(device, TargetKpi.CAUSE_REBUT, kpis, precedentes),
                CauseRebut.AUTRE,
            ).value
        event_service.enregistrer_evenement(db, machine=machine, type_evenement=type_, payload=donnees)
        journal.append(type_.value)


def _appliquer_cadence(
    db: Session, machine: Machine, kpis: dict[TargetKpi, _Lecture], journal: list[str]
) -> None:
    lecture = kpis.get(TargetKpi.CADENCE)
    if lecture is None:
        return
    try:
        cadence = float(lecture.valeur)
    except (TypeError, ValueError):
        return
    if cadence <= 0:
        return  # machine arrêtée : pas de temps de cycle significatif
    cycle = round(60.0 / cadence, 2)
    actuel = float(machine.temps_cycle_actuel_s) if machine.temps_cycle_actuel_s else None
    if actuel and abs(actuel - cycle) / cycle < SEUIL_VARIATION_CADENCE:
        return
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.CYCLE_TIME_CHANGED,
        payload={"temps_cycle_s": cycle, "cadence_u_min": round(cadence, 2), "source": "sparkplug"},
    )
    journal.append(TypeEvenementMachine.CYCLE_TIME_CHANGED.value)


def _appliquer_capteurs(
    db: Session,
    machine: Machine,
    kpis: dict[TargetKpi, _Lecture],
    precedentes: dict,
    journal: list[str],
) -> None:
    for kpi, tag_mes in _CAPTEURS.items():
        lecture = kpis.get(kpi)
        if lecture is None or precedentes.get(lecture.tag) == lecture.brute:
            continue
        try:
            valeur = round(float(lecture.valeur), 2)
        except (TypeError, ValueError):
            continue
        event_service.enregistrer_evenement(
            db,
            machine=machine,
            type_evenement=TypeEvenementMachine.SENSOR_TAG_UPDATED,
            payload={"tag": tag_mes, "valeur": valeur, "source": "sparkplug"},
        )
        journal.append(TypeEvenementMachine.SENSOR_TAG_UPDATED.value)


def _appliquer_badge(
    db: Session, machine: Machine, kpis: dict[TargetKpi, _Lecture], journal: list[str]
) -> None:
    """Badge RFID : qualifie l'arrêt en cours. La reprise (CARTE_REPRISE) est
    exécutée par l'automate lui-même — elle arrive comme un changement d'état."""
    lecture = kpis.get(TargetKpi.OPERATOR_CARD)
    if lecture is None:
        return
    code = str(lecture.valeur).strip().upper()
    if not code or code == OPERATOR_CARD_RESUME:
        return
    cause = OPERATOR_CARD_CAUSES.get(code)
    if cause is None:
        logger.warning("sparkplug_badge_inconnu", machine=machine.code, badge=code)
        return
    arret = downtime_service.qualifier_arret_ouvert(
        db, machine=machine, cause=CauseArret(cause), commentaire=f"Qualifié par badge {code}"
    )
    if arret is None:
        logger.info("sparkplug_badge_sans_arret", machine=machine.code, badge=code)
    else:
        journal.append("DOWNTIME_QUALIFIED")
