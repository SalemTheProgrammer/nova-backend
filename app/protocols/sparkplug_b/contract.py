"""Contrat de métriques entre Nova (hôte Sparkplug B) et les edge nodes.

Deux familles de métriques :

* **KPI configurables** (compteurs, état, cadence, capteurs…) : leur nom côté
  automate est libre, une règle `SparkplugTagMapping` les rattache à un KPI
  MES. Les noms ci-dessous sont seulement ceux proposés par défaut à la
  première naissance (DBIRTH) d'un device.
* **Protocole de commande** (`Command/*`, `Production/OrderNumber`) : noms
  FIXES — c'est l'interface de pilotage de Nova. Toute passerelle qui veut
  être pilotée (simulateur ou vraie passerelle automate) doit les exposer.

Règle de corrélation : un edge node qui traite une commande publie, dans le
MÊME message DDATA, son nouvel état et l'accusé (`Command/LastId` +
`Command/LastResult`). L'hôte applique l'état avec le contexte de la commande
(OF, type de maintenance, commentaire) puis libère l'appelant qui attendait.

Fichier dupliqué à l'identique dans `simulator/nova_sim/sparkplug/contract.py`
(vérifié par `tests/test_contract_sync.py`) : toute modification doit être
reportée des deux côtés.
"""
from __future__ import annotations

from enum import StrEnum


class MachineState(StrEnum):
    """Valeurs de `Machine/State` — identiques à `StatutMachine` côté MES."""

    MARCHE = "MARCHE"
    ARRET = "ARRET"
    PAUSE = "PAUSE"
    PANNE = "PANNE"
    MAINTENANCE = "MAINTENANCE"

# --- KPI : noms proposés par défaut (mapping configurable) ---------------- #
METRIC_STATE = "Machine/State"  # String : valeur de StatutMachine
METRIC_FAULT_CAUSE = "Machine/FaultCause"  # String : valeur de CauseArret
METRIC_GOOD_COUNT = "Counters/Good"  # Int64 : total monotone
METRIC_REJECT_COUNT = "Counters/Reject"  # Int64 : total monotone
METRIC_REJECT_CAUSE = "Quality/RejectCause"  # String : valeur de CauseRebut
METRIC_RATE = "Production/Rate"  # Float : unités / minute
METRIC_TEMPERATURE = "Sensors/Temperature"  # Float : °C
METRIC_OPERATOR_CARD = "Operator/Card"  # String : code de badge RFID

# --- Protocole de commande : noms fixes ----------------------------------- #
METRIC_ORDER_NUMBER = "Production/OrderNumber"  # String : OF chargé dans l'automate
METRIC_OPERATOR_ID = "Operator/Id"  # String : matricule de l'employé au poste ("" = personne)
METRIC_OPERATOR_NAME = "Operator/Name"  # String : nom inscrit sur son badge
CMD_ID = "Command/Id"  # String (DCMD) : identifiant de corrélation
CMD_ACTION = "Command/Action"  # String (DCMD) : CommandAction
CMD_ORDER_NUMBER = "Command/OrderNumber"  # String (DCMD START)
CMD_TARGET_QUANTITY = "Command/TargetQuantity"  # Int64 (DCMD START) : reliquat à produire
CMD_LAST_ID = "Command/LastId"  # String (DDATA) : dernière commande traitée
CMD_LAST_RESULT = "Command/LastResult"  # String (DDATA) : "ACCEPTED" | "REJECTED: <raison>"

# --- Métriques normalisées par la spécification Sparkplug B -------------- #
BD_SEQ = "bdSeq"
NODE_REBIRTH = "Node Control/Rebirth"

RESULT_ACCEPTED = "ACCEPTED"
RESULT_REJECTED = "REJECTED"


class CommandAction(StrEnum):
    START = "START"
    STOP = "STOP"
    PAUSE = "PAUSE"
    RESET_FAULT = "RESET_FAULT"
    START_MAINTENANCE = "START_MAINTENANCE"
    END_MAINTENANCE = "END_MAINTENANCE"


# Badges RFID opérateur → cause d'arrêt (valeurs de `CauseArret`).
OPERATOR_CARD_CAUSES: dict[str, str] = {
    "CARTE_REGLAGE": "REGLAGE_MACHINE",
    "CARTE_PANNE_MECA": "PANNE_MECANIQUE",
    "CARTE_PANNE_ELEC": "PANNE_ELECTRIQUE",
    "CARTE_NETTOYAGE": "NETTOYAGE",
    "CARTE_CHANGEMENT_SERIE": "CHANGEMENT_SERIE",
    "CARTE_ATTENTE_MATIERE": "ATTENTE_MATIERE",
    "CARTE_QUALITE": "QUALITE_BLOQUANTE",
    "CARTE_PRELEVEMENT": "PRELEVEMENT_QUALITE",
    "CARTE_MAINTENANCE": "MAINTENANCE_PLANIFIEE",
    "CARTE_MANQUE_OPERATEUR": "MANQUE_OPERATEUR",
    "CARTE_AUTRE": "AUTRE",
}
OPERATOR_CARD_RESUME = "CARTE_REPRISE"


def format_rejet(raison: str) -> str:
    return f"{RESULT_REJECTED}: {raison}"


def lire_resultat(resultat: str) -> tuple[bool, str | None]:
    """`"ACCEPTED"` → (True, None) ; `"REJECTED: raison"` → (False, "raison")."""
    texte = resultat.strip()
    if texte == RESULT_ACCEPTED:
        return True, None
    raison = texte.split(":", 1)[1].strip() if ":" in texte else texte
    return False, raison or "refus sans motif"
