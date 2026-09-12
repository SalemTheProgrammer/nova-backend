"""Badge employé : qui est au poste, d'après les métriques de l'automate."""
from __future__ import annotations

from app.protocols.sparkplug_b.codec import DataType, Metric, Payload
from app.protocols.sparkplug_b.contract import METRIC_OPERATOR_ID, METRIC_OPERATOR_NAME
from app.protocols.sparkplug_b.mapper import _suivre_operateur
from app.services.machine_read_service import operateur_depuis_valeurs


def test_operateur_lu_depuis_les_dernieres_valeurs() -> None:
    assert operateur_depuis_valeurs(None) is None
    assert operateur_depuis_valeurs({METRIC_OPERATOR_ID: ""}) is None
    o = operateur_depuis_valeurs({METRIC_OPERATOR_ID: "EMP-1001", METRIC_OPERATOR_NAME: "Amine Ben Salah"})
    assert o is not None and (o.matricule, o.nom) == ("EMP-1001", "Amine Ben Salah")


def test_changement_d_operateur_journalise() -> None:
    prise = Payload(
        metrics=[
            Metric(METRIC_OPERATOR_ID, DataType.STRING, "EMP-1001"),
            Metric(METRIC_OPERATOR_NAME, DataType.STRING, "Amine Ben Salah"),
        ]
    )
    journal: list[str] = []
    _suivre_operateur(prise, {}, journal)
    assert journal == ["opérateur au poste : Amine Ben Salah"]

    journal = []
    _suivre_operateur(prise, {METRIC_OPERATOR_ID: "EMP-1001"}, journal)  # inchangé
    assert journal == []

    fin = Payload(metrics=[Metric(METRIC_OPERATOR_ID, DataType.STRING, "")])
    _suivre_operateur(fin, {METRIC_OPERATOR_ID: "EMP-1001"}, journal)
    assert journal == ["poste libéré"]
