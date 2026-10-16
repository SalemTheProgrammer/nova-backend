"""Base DIVA factice (SQLite en mémoire) partagée par les tests Tunicotex.

Le schéma est un sous-ensemble de colonnes des vraies tables, avec les mêmes
noms. Date de référence des données : 2026-08-28.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.pool import StaticPool

from app.integrations.divatex.connexion import set_engine, vider_caches

REF_DATE = date(2026, 8, 28)
JOUR_SANS_LECTURE = date(2026, 8, 30)

# Groupes
ID_GROUPE_TRICOTAGE = 6
ID_GROUPE_CHAINE01 = 7
ID_GROUPE_REPASSAGE = 12

# Lignes (chainemontage)
ID_LIGNE_A3 = 3
ID_LIGNE_CH1_L1 = 9
ID_LIGNE_CH1_L2 = 10
ID_LIGNE_ST1 = 26

# Phases
ID_PHASE_TRICOTAGE = 1
ID_PHASE_SORTIE_CONFECTION = 5
ID_PHASE_REPASSAGE = 12

# Ordres de fabrication
ID_OF_91294_1 = 1
ID_OF_91294_2 = 2
ID_OF_90253 = 3
ID_OF_DONE = 4
NOM_OF_91294_1 = "91294_3879712_AF599-10_LCS4G-1"
NOM_OF_91294_2 = "91294_3879712_AF599-10_LCS4G-2"
NOM_OF_90253 = "90253_840534_METAVERSE-SET-G_HB001-1"
NOM_OF_DONE = "88000_X_DONE"
QTE_OF_91294_1 = 200
QTE_OF_91294_2 = 100
QTE_OF_90253 = 300
QTE_OF_DONE = 50

# Employés
ID_EMP_BEN_SALAH_AHMED = 101  # ligne 9
ID_EMP_BEN_SALAH_MOHAMED = 102  # ligne 10
ID_EMPS_CH1_L2 = (201, 202, 203)  # scannent sur la ligne 10 le 2026-08-28, sans présence

# Volumes de lectures du 2026-08-28
NB_PASSAGES_PHASE5_CH1_L2 = 30
NB_SCANS_OPERATION_PAR_EMPLOYE = 10
TEMPS_SCAN_OPERATION = 20
MAJORATION_SCAN_OPERATION = 1
OBJECTIF_CH1_L1 = 120
OBJECTIF_CH1_L2 = 382

_SCHEMA = [
    "CREATE TABLE groupechaine (IDGroupe INTEGER PRIMARY KEY, Groupe TEXT)",
    "CREATE TABLE chainemontage (IDChaineMontage INTEGER PRIMARY KEY, Code TEXT, "
    "ChaineMontage TEXT, Etat INTEGER, IDGroupe INTEGER)",
    "CREATE TABLE phase (IDPhase INTEGER PRIMARY KEY, Phase TEXT, Etat INTEGER, "
    "isProduction INTEGER)",
    "CREATE TABLE objectifchaine (IDObjectifChaine INTEGER PRIMARY KEY, Date TEXT, "
    "IDChaineMontage INTEGER, IDPhaseSortie INTEGER, ObjectifSortie INTEGER, "
    "Effectif INTEGER)",
    "CREATE TABLE employe (IDEmploye INTEGER PRIMARY KEY, Matricule TEXT, Nom TEXT, "
    "Prenom TEXT, IDChaineMontage INTEGER, IDPosteTravail INTEGER, NumCIN TEXT, "
    "NumCNSS TEXT)",
    "CREATE TABLE presence (IDPresence INTEGER PRIMARY KEY, Date TEXT, IDEmploye INTEGER, "
    "minutes INTEGER)",
    "CREATE TABLE operation (IDOperation INTEGER PRIMARY KEY, Operation TEXT)",
    "CREATE TABLE client (IDClient INTEGER PRIMARY KEY, Client TEXT)",
    "CREATE TABLE article (IDArticle INTEGER PRIMARY KEY, Code TEXT, Article TEXT)",
    "CREATE TABLE commande (IDCommande INTEGER PRIMARY KEY, Commande TEXT, Date TEXT, "
    "IDClient INTEGER, Qtte INTEGER, DateFinVise TEXT)",
    "CREATE TABLE ofabrication (IDOFabrication INTEGER PRIMARY KEY, OFAbrication TEXT, "
    "IDArticle INTEGER, IDClient INTEGER, IDCommande INTEGER, Quantite INTEGER, "
    "Etat INTEGER, DateExportPrevue TEXT, DtDebut TEXT, NumCmdClient TEXT, PO TEXT)",
    "CREATE TABLE lectures (IDLectures INTEGER PRIMARY KEY, Date TEXT, SaisiLe TEXT, "
    "IDChaineMontage INTEGER, IDEmploye INTEGER, IDOFabrication INTEGER, "
    "IDOperation INTEGER, IdPhase INTEGER, Quantite INTEGER, Temps REAL, "
    "Majoration REAL, LibTaille TEXT)",
    "CREATE INDEX idx_lectures_chaine_date ON lectures (IDChaineMontage, Date)",
    "CREATE INDEX idx_lectures_of_date ON lectures (IDOFabrication, Date)",
    "CREATE INDEX idx_lectures_date ON lectures (Date)",
    "CREATE INDEX idx_lectures_employe ON lectures (IDEmploye)",
]


def creer_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))


def _inserer(conn, table: str, lignes: list[dict]) -> None:
    if not lignes:
        return
    colonnes = list(lignes[0])
    sql = (
        f"INSERT INTO {table} ({', '.join(colonnes)}) "
        f"VALUES ({', '.join(':' + c for c in colonnes)})"
    )
    conn.execute(text(sql), lignes)


def _lecture(**champs) -> dict:
    base = {
        "Date": REF_DATE.isoformat(), "SaisiLe": None, "IDChaineMontage": 0,
        "IDEmploye": 0, "IDOFabrication": 0, "IDOperation": 0, "IdPhase": 0,
        "Quantite": 1, "Temps": 0, "Majoration": 0, "LibTaille": "M",
    }
    base.update(champs)
    return base


def _lectures() -> list[dict]:
    jour = REF_DATE.isoformat()
    lignes: list[dict] = [
        # veille, insérée en premier : la dernière lecture de la base est celle du 28
        _lecture(
            Date="2026-08-27", SaisiLe="2026-08-27 09:00:00",
            IDChaineMontage=ID_LIGNE_CH1_L2, IDEmploye=ID_EMPS_CH1_L2[0],
            IDOFabrication=ID_OF_91294_1, IdPhase=ID_PHASE_SORTIE_CONFECTION,
        )
    ]
    # 30 passages de phase 5 sur la ligne 10, répartis sur plusieurs heures
    for i in range(NB_PASSAGES_PHASE5_CH1_L2):
        heure, minute = 7 + i // 3, (i % 3) * 20
        lignes.append(
            _lecture(
                SaisiLe=f"{jour} {heure:02d}:{minute:02d}:00",
                IDChaineMontage=ID_LIGNE_CH1_L2,
                IDEmploye=ID_EMPS_CH1_L2[i % 3],
                IDOFabrication=ID_OF_91294_1 if i % 2 == 0 else ID_OF_91294_2,
                IdPhase=ID_PHASE_SORTIE_CONFECTION,
            )
        )
    # scans d'opération (IdPhase = 0) : Temps=20, Majoration=1
    for idx, emp in enumerate(ID_EMPS_CH1_L2):
        for k in range(NB_SCANS_OPERATION_PAR_EMPLOYE):
            lignes.append(
                _lecture(
                    SaisiLe=f"{jour} {8 + k:02d}:{idx * 10:02d}:30",
                    IDChaineMontage=ID_LIGNE_CH1_L2, IDEmploye=emp,
                    IDOFabrication=ID_OF_91294_1, IDOperation=1, IdPhase=0,
                    Temps=TEMPS_SCAN_OPERATION, Majoration=MAJORATION_SCAN_OPERATION,
                )
            )
    # repassage (phase 12) : 50 % de la quantité de chaque OF 91294
    for of_id, qte in ((ID_OF_91294_1, QTE_OF_91294_1), (ID_OF_91294_2, QTE_OF_91294_2)):
        lignes.append(
            _lecture(
                SaisiLe=f"{jour} 15:00:00", IDChaineMontage=ID_LIGNE_CH1_L2,
                IDEmploye=ID_EMPS_CH1_L2[0], IDOFabrication=of_id,
                IdPhase=ID_PHASE_REPASSAGE, Quantite=qte // 2,
            )
        )
    # tricotage : quelques passages de phase 1 sur A3
    for i in range(4):
        lignes.append(
            _lecture(
                SaisiLe=f"{jour} {9 + i * 2:02d}:15:00", IDChaineMontage=ID_LIGNE_A3,
                IDOFabrication=ID_OF_90253, IdPhase=ID_PHASE_TRICOTAGE, Quantite=5,
            )
        )
    return lignes


def peupler(engine: Engine) -> None:
    with engine.begin() as conn:
        _inserer(conn, "groupechaine", [
            {"IDGroupe": ID_GROUPE_TRICOTAGE, "Groupe": "TRICOTAGE"},
            {"IDGroupe": ID_GROUPE_CHAINE01, "Groupe": "CHAINE 01"},
            {"IDGroupe": ID_GROUPE_REPASSAGE, "Groupe": "REPASSAGE"},
        ])
        _inserer(conn, "chainemontage", [
            {"IDChaineMontage": ID_LIGNE_A3, "Code": "A3", "ChaineMontage": "TRICOT A3",
             "Etat": 1, "IDGroupe": ID_GROUPE_TRICOTAGE},
            {"IDChaineMontage": ID_LIGNE_CH1_L1, "Code": "CH1-L1",
             "ChaineMontage": "CHAINE 1 LIGNE 1", "Etat": 1, "IDGroupe": ID_GROUPE_CHAINE01},
            {"IDChaineMontage": ID_LIGNE_CH1_L2, "Code": "CH1-L2",
             "ChaineMontage": "CHAINE 1 LIGNE 2", "Etat": 1, "IDGroupe": ID_GROUPE_CHAINE01},
            {"IDChaineMontage": ID_LIGNE_ST1, "Code": "ST1", "ChaineMontage": "STIRO 1",
             "Etat": 1, "IDGroupe": ID_GROUPE_REPASSAGE},
        ])
        _inserer(conn, "phase", [
            {"IDPhase": 1, "Phase": "SORTIE TRICOTAGE", "Etat": 1, "isProduction": 1},
            {"IDPhase": 4, "Phase": "ENTREE CONFECTION", "Etat": 1, "isProduction": 1},
            {"IDPhase": 5, "Phase": "SORTIE CONFECTION", "Etat": 1, "isProduction": 1},
            {"IDPhase": 11, "Phase": "ENTREE REPASSAGE", "Etat": 1, "isProduction": 1},
            {"IDPhase": 12, "Phase": "SORTIE REPASSAGE", "Etat": 1, "isProduction": 1},
        ])
        _inserer(conn, "objectifchaine", [
            {"IDObjectifChaine": 1, "Date": "2026-03-31", "IDChaineMontage": ID_LIGNE_CH1_L1,
             "IDPhaseSortie": ID_PHASE_SORTIE_CONFECTION, "ObjectifSortie": OBJECTIF_CH1_L1,
             "Effectif": 20},
            {"IDObjectifChaine": 2, "Date": "2026-03-31", "IDChaineMontage": ID_LIGNE_CH1_L2,
             "IDPhaseSortie": ID_PHASE_SORTIE_CONFECTION, "ObjectifSortie": OBJECTIF_CH1_L2,
             "Effectif": 45},
        ])
        _inserer(conn, "employe", [
            {"IDEmploye": ID_EMP_BEN_SALAH_AHMED, "Matricule": "M101", "Nom": "BEN SALAH",
             "Prenom": "Ahmed", "IDChaineMontage": ID_LIGNE_CH1_L1, "IDPosteTravail": 1,
             "NumCIN": "00000001", "NumCNSS": "CNSS-1"},
            {"IDEmploye": ID_EMP_BEN_SALAH_MOHAMED, "Matricule": "M102", "Nom": "BEN SALAH",
             "Prenom": "Mohamed", "IDChaineMontage": ID_LIGNE_CH1_L2, "IDPosteTravail": 1,
             "NumCIN": "00000002", "NumCNSS": "CNSS-2"},
            {"IDEmploye": 201, "Matricule": "M201", "Nom": "TRABELSI", "Prenom": "Sonia",
             "IDChaineMontage": ID_LIGNE_CH1_L2, "IDPosteTravail": 2,
             "NumCIN": "00000003", "NumCNSS": "CNSS-3"},
            {"IDEmploye": 202, "Matricule": "M202", "Nom": "GHARBI", "Prenom": "Leila",
             "IDChaineMontage": ID_LIGNE_CH1_L2, "IDPosteTravail": 2,
             "NumCIN": "00000004", "NumCNSS": "CNSS-4"},
            {"IDEmploye": 203, "Matricule": "M203", "Nom": "JLASSI", "Prenom": "Karim",
             "IDChaineMontage": ID_LIGNE_CH1_L2, "IDPosteTravail": 3,
             "NumCIN": "00000005", "NumCNSS": "CNSS-5"},
        ])
        # présence éparse, antérieure au 2026-04-02 ; aucune pour les employés 201-203
        _inserer(conn, "presence", [
            {"IDPresence": 1, "Date": "2026-03-30", "IDEmploye": ID_EMP_BEN_SALAH_AHMED,
             "minutes": 450},
            {"IDPresence": 2, "Date": "2026-03-30", "IDEmploye": ID_EMP_BEN_SALAH_MOHAMED,
             "minutes": 450},
        ])
        _inserer(conn, "operation", [{"IDOperation": 1, "Operation": "ASSEMBLAGE COL"}])
        _inserer(conn, "client", [
            {"IDClient": 1, "Client": "LC WAIKIKI"},
            {"IDClient": 2, "Client": "METAVERSE"},
        ])
        _inserer(conn, "article", [
            {"IDArticle": 1, "Code": "AF599-10", "Article": "PULL COL ZIP"},
            {"IDArticle": 2, "Code": "HB001", "Article": "SET ENFANT"},
        ])
        _inserer(conn, "commande", [
            {"IDCommande": 1, "Commande": "CMD-91294", "Date": "2026-07-15", "IDClient": 1,
             "Qtte": QTE_OF_91294_1 + QTE_OF_91294_2, "DateFinVise": "2026-08-26"},
            {"IDCommande": 2, "Commande": "CMD-90253", "Date": "2026-07-20", "IDClient": 2,
             "Qtte": QTE_OF_90253, "DateFinVise": "2026-09-02"},
        ])
        _inserer(conn, "ofabrication", [
            {"IDOFabrication": ID_OF_91294_1, "OFAbrication": NOM_OF_91294_1, "IDArticle": 1,
             "IDClient": 1, "IDCommande": 1, "Quantite": QTE_OF_91294_1, "Etat": 1,
             "DateExportPrevue": "2026-08-26", "DtDebut": "2026-08-10",
             "NumCmdClient": "NC-91294", "PO": "PO-91294"},
            {"IDOFabrication": ID_OF_91294_2, "OFAbrication": NOM_OF_91294_2, "IDArticle": 1,
             "IDClient": 1, "IDCommande": 1, "Quantite": QTE_OF_91294_2, "Etat": 1,
             "DateExportPrevue": "2026-08-26", "DtDebut": "2026-08-10",
             "NumCmdClient": "NC-91294", "PO": "PO-91294"},
            {"IDOFabrication": ID_OF_90253, "OFAbrication": NOM_OF_90253, "IDArticle": 2,
             "IDClient": 2, "IDCommande": 2, "Quantite": QTE_OF_90253, "Etat": 1,
             "DateExportPrevue": "2026-09-02", "DtDebut": "2026-08-15",
             "NumCmdClient": "NC-90253", "PO": "PO-90253"},
            {"IDOFabrication": ID_OF_DONE, "OFAbrication": NOM_OF_DONE, "IDArticle": 2,
             "IDClient": 2, "IDCommande": 2, "Quantite": QTE_OF_DONE, "Etat": 0,
             "DateExportPrevue": "2026-07-01", "DtDebut": "2026-06-01",
             "NumCmdClient": "NC-88000", "PO": "PO-88000"},
        ])
        _inserer(conn, "lectures", _lectures())


@pytest.fixture
def divatex():
    """Moteur SQLite en mémoire, schéma + données injectés dans la connexion DIVA."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    creer_schema(engine)
    peupler(engine)
    set_engine(engine)
    vider_caches()
    yield engine
    set_engine(None)
    engine.dispose()


@pytest.fixture
def capture_sql(divatex):
    """Liste des instructions SQL exécutées pendant le test."""
    executees: list[str] = []

    @event.listens_for(divatex, "before_cursor_execute")
    def _capturer(_conn, _cursor, statement, _params, _context, _many):
        executees.append(statement)

    yield executees
    event.remove(divatex, "before_cursor_execute", _capturer)
