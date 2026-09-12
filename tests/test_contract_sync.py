"""Le contrat Sparkplug est partagé à l'identique entre le MES et le simulateur."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import StatutMachine
from app.protocols.sparkplug_b.contract import MachineState

BACKEND = Path(__file__).resolve().parents[1] / "app" / "protocols" / "sparkplug_b"
SIMULATEUR = Path(__file__).resolve().parents[2] / "simulator" / "nova_sim" / "sparkplug"
FICHIERS_VENDORES = ("contract.py", "codec.py", "topic.py", "sparkplug_b.proto", "sparkplug_b_pb2.py")


def test_vocabulaire_d_etat_identique_au_mes() -> None:
    assert {s.value for s in MachineState} == {s.value for s in StatutMachine}


@pytest.mark.skipif(not SIMULATEUR.exists(), reason="simulateur absent (image Docker du backend)")
@pytest.mark.parametrize("nom", FICHIERS_VENDORES)
def test_fichiers_vendores_identiques(nom: str) -> None:
    assert (BACKEND / nom).read_bytes() == (SIMULATEUR / nom).read_bytes(), (
        f"{nom} a divergé entre backend et simulateur : recopiez-le."
    )
