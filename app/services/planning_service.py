"""Ordonnancement du backlog d'OF : 13 règles de dispatching, simulées par ligne.

Le moteur est ici — et NULLE PART ailleurs. Nova ne « devine » jamais un
planning : elle choisit une règle et des priorités, ce module calcule. À règle
et données égales, le résultat est identique (tri total, aucun aléa) : c'est ce
qui rend un scénario comparable, explicable et rejouable.

Modèle de charge (repris de l'ancienne simulation côté page Ordres, qui était
la référence visuelle de l'opérateur) :

  1. Une LIGNE est la ressource. Son débit = somme des 1/temps_de_cycle de ses
     machines actives et cadencées (deux machines à 2 s/u ⇒ 1 u/s). Une ligne
     sans machine cadencée retombe sur `DEBIT_DEFAUT` plutôt que de bloquer la
     démo. Le statut instantané (panne/maintenance) n'entre PAS dans le débit :
     une panne se répare et l'horizon se compte en jours — c'est au superviseur
     de proposer un reroutage, pas à une règle de dispatching de condamner une
     ligne.
  2. Les OF EN_COURS sont FIGÉS : ils occupent leur ligne le temps de finir leur
     reste à produire. On ne les déplace pas et on ne leur écrit aucune date.
  3. Les OF PLANIFIE sont triés par la règle choisie, puis posés à la suite sur
     leur ligne (ou, s'ils n'en ont pas, sur celle qui se libère le plus tôt).
  4. Chaque créneau est confronté à l'échéance client (`date_echeance`) pour
     mesurer le retard prévisionnel.

`date_echeance` est une ENTRÉE (elle trie EDD/CR/SLACK et sert de référence au
retard). `date_debut_prevue`/`date_fin_prevue` sont les SORTIES. Les confondre
ferait trier la règle sur son propre résultat et le retard tomberait à zéro pour
toujours — d'où la séparation stricte.

Utilisé par les outils agent `simuler_ordonnancement`, `comparer_algorithmes` et
`appliquer_ordonnancement`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import exp, inf

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LigneProduction, Machine, OrdreFabrication
from app.models.enums import StatutOF

# Règle de dispatching -> libellé opérateur. L'ordre de ce dict est celui des
# comparaisons ; les clés sont l'API publique (ce que le LLM passe en argument).
ALGORITHMES: dict[str, str] = {
    "FIFO": "Premier arrivé, premier servi",
    "LIFO": "Dernier arrivé, premier servi",
    "EDD": "Échéance la plus proche d'abord",
    "SPT": "Temps de production le plus court",
    "LPT": "Temps de production le plus long",
    "CR": "Ratio critique (temps restant / travail restant)",
    "SLACK": "Marge de temps minimale d'abord",
    "SETUP": "Regroupement par article (moins de changements de série)",
    "SETUP_EDD": "Regroupement par article, groupes classés par échéance",
    "MDD": "Échéance modifiée (échéance ou fin au plus tôt, la plus tardive)",
    "ATC": "Coût de retard apparent (urgence pondérée par la durée)",
    "COVERT": "Coût de retard escompté par unité de temps de production",
    "MOORE": "Moore-Hodgson (minimise le NOMBRE d'OF en retard)",
}

ALGORITHME_DEFAUT = "EDD"

# L'affectation choisit la RESSOURCE ; l'ordonnancement choisit ensuite l'ORDRE
# et les dates. Ces deux décisions restent séparées pour que Nova puisse les
# expliquer et les faire confirmer indépendamment.
STRATEGIES_AFFECTATION: dict[str, str] = {
    "ECT": "Équilibrage de charge (fin au plus tôt)",
    "SETUP": "Regroupement par article (moins de réglages)",
}

# Débit de repli (unités/s) d'une ligne sans machine cadencée.
DEBIT_DEFAUT = 0.5


@dataclass(frozen=True)
class Creneau:
    """Un OF posé sur une ligne, avec son créneau projeté."""

    of_id: int
    numero: str
    article: str
    quantite: float
    ligne_id: int
    ligne_code: str
    debut: datetime
    fin: datetime
    echeance: date | None
    retard_jours: int
    retard_h: float  # retard fin de mesure : départage deux règles à jours égaux
    en_cours: bool
    prioritaire: bool
    position: int | None  # rang dans la séquence PLANIFIE ; None si EN_COURS


@dataclass(frozen=True)
class Scenario:
    """Résultat d'une règle : les créneaux + les KPI qui permettent de la juger."""

    algorithme: str
    calcule_le: datetime
    creneaux: list[Creneau]
    non_planifiables: list[tuple[str, str]]  # (numéro, raison)
    prioritaires: tuple[str, ...]

    @property
    def libelle(self) -> str:
        return ALGORITHMES[self.algorithme]

    @property
    def planifies(self) -> list[Creneau]:
        return [c for c in self.creneaux if not c.en_cours]

    @property
    def en_retard(self) -> list[Creneau]:
        return [c for c in self.creneaux if c.retard_jours > 0]

    @property
    def retard_total_jours(self) -> int:
        return sum(c.retard_jours for c in self.creneaux)

    @property
    def nb_retards(self) -> int:
        return len(self.en_retard)

    @property
    def retard_total_h(self) -> float:
        return sum(c.retard_h for c in self.creneaux)

    @property
    def makespan_h(self) -> float:
        """Heures entre le calcul et la fin du dernier OF (durée totale du plan)."""
        if not self.creneaux:
            return 0.0
        fin = max(c.fin for c in self.creneaux)
        return max(0.0, (fin - self.calcule_le).total_seconds() / 3600)

    @property
    def nb_changements_serie(self) -> int:
        """Transitions d'article sur une même ligne : chacune coûte un réglage."""
        total = 0
        for ligne_id in {c.ligne_id for c in self.creneaux}:
            suite = sorted(
                (c for c in self.creneaux if c.ligne_id == ligne_id),
                key=lambda c: c.debut,
            )
            total += sum(
                1 for prec, suiv in zip(suite, suite[1:]) if prec.article != suiv.article
            )
        return total

    @property
    def score_tri(self) -> tuple:
        """Clé de classement entre règles : tenir les délais d'abord, puis limiter
        les réglages, puis finir tôt. Le retard client prime — c'est lui qui coûte.

        Le retard en heures s'intercale comme départage : à jours égaux (fréquent
        sur un plan court), c'est lui qui distingue réellement les règles.
        """
        return (
            self.retard_total_jours,
            self.nb_retards,
            round(self.retard_total_h, 3),
            self.nb_changements_serie,
            round(self.makespan_h, 3),
        )


@dataclass(frozen=True)
class AffectationLigne:
    """Ligne actuelle et ligne proposée pour un OF PLANIFIE."""

    of_id: int
    numero: str
    article: str
    quantite: float
    ligne_actuelle_id: int | None
    ligne_actuelle_code: str | None
    ligne_proposee_id: int
    ligne_proposee_code: str

    @property
    def change(self) -> bool:
        return self.ligne_actuelle_id != self.ligne_proposee_id


@dataclass(frozen=True)
class ScenarioAffectationLignes:
    """Proposition déterministe d'affectation du backlog aux lignes."""

    strategie: str
    reaffecter: bool
    affectations: list[AffectationLigne]
    charges_h: dict[int, float]
    codes_lignes: dict[int, str]
    non_affectes: list[tuple[str, str]]

    @property
    def changements(self) -> list[AffectationLigne]:
        return [a for a in self.affectations if a.change]


@dataclass(frozen=True)
class _Tache:
    """Un OF PLANIFIE préparé pour le tri (durée estimée figée avant affectation)."""

    of_id: int
    numero: str
    article: str
    quantite: float
    echeance: date | None
    ligne_id: int | None
    duree_ref_s: float
    rang_priorite: int


def _restant(of: OrdreFabrication) -> float:
    return max(
        0.0,
        float(of.quantite_planifiee) - float(of.quantite_bonne) - float(of.quantite_rejetee),
    )


def _retard(fin: datetime, echeance: date | None) -> int:
    """Retard client en JOURS : la mesure métier, celle que le client subit."""
    if echeance is None:
        return 0
    return max(0, (fin.date() - echeance).days)


def _retard_h(fin: datetime, echeance: date | None) -> float:
    """Retard en HEURES au-delà de la fin du jour d'échéance.

    Le retard en jours est trop grossier pour comparer des règles : un plan qui
    tient sur quelques heures ne change jamais de retard-jours quand on le
    réordonne, et les règles finissent ex aequo. Cette mesure continue les
    départage sans mentir sur le chiffre métier, qui reste le retard en jours.
    """
    if echeance is None:
        return 0.0
    return max(0.0, (fin - _limite(echeance)).total_seconds() / 3600)


def _limite(echeance: date) -> datetime:
    """Une échéance au 14/07 court jusqu'à la fin du 14/07, pas jusqu'à minuit."""
    return datetime.combine(echeance, time.max)


def _fin_creneau(debut: datetime, quantite: float, debit: float) -> datetime:
    """Fin d'un créneau, à la seconde : la microseconde n'a aucun sens sur un plan
    qui s'étale sur des heures, et elle pollue les dates écrites en base."""
    return (debut + timedelta(seconds=quantite / debit)).replace(microsecond=0)


def _debits(db: Session, lignes: list[LigneProduction]) -> dict[int, float]:
    """Débit (unités/s) de chaque ligne = somme des cadences de ses machines."""
    machines = db.execute(select(Machine).where(Machine.actif.is_(True))).scalars().all()
    debits: dict[int, float] = {}
    for ligne in lignes:
        debit = 0.0
        for m in machines:
            if m.ligne_production_id != ligne.id:
                continue
            cycle = float(m.temps_cycle_cible_s or 0)
            if cycle > 0:
                debit += 1 / cycle
        debits[ligne.id] = debit if debit > 0 else DEBIT_DEFAUT
    return debits


def proposer_affectation_lignes(
    db: Session,
    strategie: str,
    reaffecter: bool,
) -> ScenarioAffectationLignes:
    """Propose une ligne compatible pour chaque OF PLANIFIE, sans rien écrire.

    ECT place d'abord les gros OF puis choisit la ligne qui les terminerait le
    plus tôt. SETUP garde ensemble les OF d'un même article avant de choisir la
    ligne qui finirait le groupe le plus tôt. Les OF EN_COURS ne bougent jamais :
    leur reste à produire précharge simplement leur ligne.
    """
    choix = (strategie or "").strip().upper()
    if choix not in STRATEGIES_AFFECTATION:
        raise ValueError(
            f"Stratégie d'affectation inconnue : {strategie!r}. "
            f"Disponibles : {', '.join(STRATEGIES_AFFECTATION)}."
        )

    lignes = sorted(
        db.execute(
            select(LigneProduction).where(LigneProduction.actif.is_(True))
        ).scalars().all(),
        key=lambda ligne: ligne.id,
    )
    ofs = db.execute(
        select(OrdreFabrication).where(
            OrdreFabrication.statut.in_([StatutOF.PLANIFIE, StatutOF.EN_COURS])
        )
    ).scalars().all()
    ofs = sorted((of for of in ofs if _restant(of) > 0), key=lambda of: of.id)

    codes = {ligne.id: ligne.code for ligne in lignes}
    non_affectes: list[tuple[str, str]] = []
    if not lignes:
        return ScenarioAffectationLignes(
            choix,
            reaffecter,
            [],
            {},
            {},
            [(of.numero, "aucune ligne de production active") for of in ofs],
        )

    debits = _debits(db, lignes)
    compatibles = {ligne.id: {article.id for article in ligne.articles} for ligne in lignes}
    charges_s: dict[int, float] = {ligne.id: 0.0 for ligne in lignes}
    affectations: list[AffectationLigne] = []
    candidats: list[OrdreFabrication] = []

    def charger(ligne_id: int, quantite: float) -> None:
        charges_s[ligne_id] += quantite / debits[ligne_id]

    def lignes_eligibles(of: OrdreFabrication) -> list[LigneProduction]:
        return [ligne for ligne in lignes if of.article_id in compatibles[ligne.id]]

    def enregistrer(of: OrdreFabrication, ligne: LigneProduction) -> None:
        quantite = _restant(of)
        charger(ligne.id, quantite)
        actuelle_id = of.ligne_production_id if of.ligne_production_id in codes else None
        affectations.append(
            AffectationLigne(
                of_id=of.id,
                numero=of.numero,
                article=of.article.code,
                quantite=quantite,
                ligne_actuelle_id=actuelle_id,
                ligne_actuelle_code=codes.get(actuelle_id),
                ligne_proposee_id=ligne.id,
                ligne_proposee_code=ligne.code,
            )
        )

    # Les OF EN_COURS immobilisent leur ressource mais ne sont jamais réaffectés.
    for of in (o for o in ofs if o.statut == StatutOF.EN_COURS):
        if of.ligne_production_id in charges_s:
            charger(of.ligne_production_id, _restant(of))

    for of in (o for o in ofs if o.statut == StatutOF.PLANIFIE):
        actuelle_id = of.ligne_production_id
        actuelle_valide = (
            actuelle_id in charges_s and of.article_id in compatibles[actuelle_id]
        )
        if not reaffecter and actuelle_valide:
            ligne = next(ligne for ligne in lignes if ligne.id == actuelle_id)
            enregistrer(of, ligne)
        else:
            candidats.append(of)

    def meilleure_ligne(
        quantite: float, eligibles: list[LigneProduction]
    ) -> LigneProduction:
        return min(
            eligibles,
            key=lambda ligne: (
                charges_s[ligne.id] + quantite / debits[ligne.id],
                ligne.id,
            ),
        )

    if choix == "SETUP":
        groupes: dict[int, list[OrdreFabrication]] = {}
        for of in candidats:
            groupes.setdefault(of.article_id, []).append(of)
        groupes_tries = sorted(
            groupes.values(),
            key=lambda groupe: (
                -sum(_restant(of) for of in groupe),
                groupe[0].article.code,
            ),
        )
        for groupe in groupes_tries:
            eligibles = lignes_eligibles(groupe[0])
            if not eligibles:
                non_affectes.extend(
                    (of.numero, "aucune ligne active compatible avec cet article")
                    for of in groupe
                )
                continue
            volume = sum(_restant(of) for of in groupe)
            ligne = meilleure_ligne(volume, eligibles)
            for of in sorted(groupe, key=lambda ordre: ordre.id):
                enregistrer(of, ligne)
    else:
        for of in sorted(candidats, key=lambda ordre: (-_restant(ordre), ordre.id)):
            eligibles = lignes_eligibles(of)
            if not eligibles:
                non_affectes.append(
                    (of.numero, "aucune ligne active compatible avec cet article")
                )
                continue
            enregistrer(of, meilleure_ligne(_restant(of), eligibles))

    return ScenarioAffectationLignes(
        strategie=choix,
        reaffecter=reaffecter,
        affectations=sorted(affectations, key=lambda a: a.of_id),
        charges_h={ligne_id: secondes / 3600 for ligne_id, secondes in charges_s.items()},
        codes_lignes=codes,
        non_affectes=non_affectes,
    )


def _cle_regle(
    algorithme: str,
    maintenant: datetime,
    duree_moyenne_s: float,
    taches: list[_Tache],
):
    """Clé de tri d'une règle. Toujours complétée par l'id : à égalité stricte,
    l'ordre reste le même d'un calcul à l'autre (déterminisme).

    `taches` : le backlog complet — SETUP_EDD en tire l'échéance la plus proche
    de chaque article (l'urgence d'un groupe est celle de son OF le plus pressé).
    """

    # SETUP_EDD : échéance la plus proche par article. Un article sans aucune
    # échéance n'est jamais urgent : son groupe part en fin de file.
    urgence_article: dict[str, date] = {}
    for t in taches:
        if t.echeance is None:
            continue
        actuelle = urgence_article.get(t.article)
        if actuelle is None or t.echeance < actuelle:
            urgence_article[t.article] = t.echeance

    def ratio_critique(t: _Tache) -> float:
        if t.echeance is None:
            return inf  # sans échéance : jamais urgent, donc en fin de file
        return (_limite(t.echeance) - maintenant).total_seconds() / t.duree_ref_s

    def marge(t: _Tache) -> float:
        if t.echeance is None:
            return inf
        return (_limite(t.echeance) - maintenant).total_seconds() - t.duree_ref_s

    def echeance_modifiee(t: _Tache) -> float:
        """MDD : une échéance déjà intenable est remplacée par « fin au plus tôt ».
        Trier dessus évite de courir après des délais perdus au détriment des
        OF encore sauvables."""
        au_plus_tot = t.duree_ref_s
        if t.echeance is None:
            return inf
        return max(au_plus_tot, (_limite(t.echeance) - maintenant).total_seconds())

    def cout_retard_apparent(t: _Tache) -> float:
        """ATC : urgence exponentielle (marge qui fond) pondérée par la durée.
        Indice grand = à passer d'abord ; on trie donc sur son opposé.
        k = 3 : valeur d'usage classique pour l'horizon de quelques heures."""
        if t.echeance is None:
            return 0.0  # jamais urgent → indice nul → fin de file
        slack = max(
            0.0, (_limite(t.echeance) - maintenant).total_seconds() - t.duree_ref_s
        )
        return exp(-slack / (3 * duree_moyenne_s)) / t.duree_ref_s

    def cout_retard_escompte(t: _Tache) -> float:
        """COVERT : même famille qu'ATC mais l'urgence décroît LINÉAIREMENT avec
        la marge (au lieu d'exponentiellement) et tombe à zéro au-delà de
        k × durée moyenne. Indice grand = à passer d'abord ; tri sur l'opposé.
        k = 3, comme ATC, pour que les deux règles soient comparables."""
        if t.echeance is None:
            return 0.0  # jamais urgent → indice nul → fin de file
        slack = max(
            0.0, (_limite(t.echeance) - maintenant).total_seconds() - t.duree_ref_s
        )
        return max(0.0, 1.0 - slack / (3 * duree_moyenne_s)) / t.duree_ref_s

    regles = {
        "FIFO": lambda t: (t.of_id,),
        "LIFO": lambda t: (-t.of_id,),
        "EDD": lambda t: (t.echeance is None, t.echeance or date.max),
        "SPT": lambda t: (t.duree_ref_s,),
        "LPT": lambda t: (-t.duree_ref_s,),
        "CR": lambda t: (ratio_critique(t),),
        "SLACK": lambda t: (marge(t),),
        "SETUP": lambda t: (t.article,),
        # SETUP_EDD : mêmes groupes que SETUP, mais classés par l'échéance la plus
        # proche du groupe (puis EDD à l'intérieur) — on économise les réglages
        # SANS servir les articles dans un ordre alphabétique arbitraire.
        "SETUP_EDD": lambda t: (
            t.article not in urgence_article,
            urgence_article.get(t.article, date.max),
            t.article,
            t.echeance is None,
            t.echeance or date.max,
        ),
        "MDD": lambda t: (echeance_modifiee(t),),
        "ATC": lambda t: (-cout_retard_apparent(t),),
        "COVERT": lambda t: (-cout_retard_escompte(t),),
    }
    regle = regles[algorithme]
    # Les OF explicitement priorisés par l'opérateur passent AVANT la règle :
    # « fais l'OF-42 en premier » est un ordre, pas une suggestion.
    return lambda t: (t.rang_priorite, *regle(t), t.of_id)


def _sequence_moore(taches: list[_Tache], maintenant: datetime) -> list[_Tache]:
    """Moore-Hodgson : minimise le NOMBRE d'OF en retard (pas les jours cumulés).

    L'algorithme exact vaut pour une machine unique ; ici on l'approxime sur une
    « capacité agrégée » (les durées de référence s'enchaînent) — suffisant pour
    décider QUI sacrifier. Principe : on déroule en EDD, et dès qu'un OF finit en
    retard, on éjecte en fin de file l'OF le plus LONG déjà séquencé — c'est lui
    qui pénalise le plus les suivants. Les éjectés sont de toute façon en retard :
    autant qu'ils ne mettent personne d'autre en retard.

    Les OF priorisés par l'opérateur restent en tête, hors algorithme : un ordre
    explicite ne se sacrifie pas.
    """
    # Prioritaire = rang strictement inférieur au rang par défaut (les non-priorisés
    # partagent tous le rang max, cf. construction de _Tache).
    rang_defaut = max((t.rang_priorite for t in taches), default=0)
    fronts = sorted(
        (t for t in taches if t.rang_priorite < rang_defaut), key=lambda t: t.rang_priorite
    )
    reste = [t for t in taches if t.rang_priorite >= rang_defaut]

    edd = sorted(reste, key=lambda t: (t.echeance is None, t.echeance or date.max, t.of_id))
    cumul = sum(t.duree_ref_s for t in fronts)
    gardes: list[_Tache] = []
    ejectes: list[_Tache] = []
    for t in edd:
        gardes.append(t)
        cumul += t.duree_ref_s
        if t.echeance is not None and maintenant + timedelta(seconds=cumul) > _limite(t.echeance):
            long = max(gardes, key=lambda u: (u.duree_ref_s, u.of_id))
            gardes.remove(long)
            cumul -= long.duree_ref_s
            ejectes.append(long)
    return fronts + gardes + ejectes


def _resoudre_prioritaires(
    ofs: list[OrdreFabrication], prioritaires: tuple[str, ...]
) -> tuple[dict[int, int], list[str]]:
    """Mappe chaque OF priorisé sur son rang. Renvoie aussi les références inconnues."""
    rangs: dict[int, int] = {}
    inconnus: list[str] = []
    for rang, reference in enumerate(prioritaires):
        ref = reference.strip().upper()
        trouve = next(
            (o for o in ofs if o.numero.upper() == ref or str(o.id) == ref),
            None,
        )
        if trouve is None:
            inconnus.append(reference)
        else:
            rangs.setdefault(trouve.id, rang)
    return rangs, inconnus


def ordonnancer(
    db: Session,
    algorithme: str = ALGORITHME_DEFAUT,
    prioritaires: tuple[str, ...] = (),
) -> Scenario:
    """Calcule le planning du backlog selon `algorithme`. N'écrit RIEN.

    `prioritaires` : numéros (ou ids) d'OF à placer en tête de file, dans l'ordre
    donné, avant application de la règle sur le reste.

    Lève ValueError si la règle est inconnue ou une priorité introuvable.
    """
    algo = algorithme.strip().upper()
    if algo not in ALGORITHMES:
        raise ValueError(
            f"Règle inconnue : {algorithme!r}. Disponibles : {', '.join(ALGORITHMES)}."
        )

    maintenant = datetime.now().replace(microsecond=0)

    ofs = db.execute(
        select(OrdreFabrication).where(
            OrdreFabrication.statut.in_([StatutOF.PLANIFIE, StatutOF.EN_COURS])
        )
    ).scalars().all()
    ofs = [of for of in ofs if _restant(of) > 0]

    rangs, inconnus = _resoudre_prioritaires(ofs, prioritaires)
    if inconnus:
        raise ValueError(
            f"OF introuvable(s) parmi les OF ouverts : {', '.join(inconnus)}."
        )

    lignes = db.execute(
        select(LigneProduction).where(LigneProduction.actif.is_(True))
    ).scalars().all()

    non_planifiables: list[tuple[str, str]] = []
    if not ofs:
        return Scenario(algo, maintenant, [], [], prioritaires)
    if not lignes:
        return Scenario(
            algo,
            maintenant,
            [],
            [(of.numero, "aucune ligne de production active") for of in ofs],
            prioritaires,
        )

    debits = _debits(db, lignes)
    codes = {l.id: l.code for l in lignes}
    dispo: dict[int, datetime] = {l.id: maintenant for l in lignes}
    creneaux: list[Creneau] = []

    # 1. OF EN_COURS : figés sur leur ligne, ils la mobilisent jusqu'à leur fin.
    for of in sorted((o for o in ofs if o.statut == StatutOF.EN_COURS), key=lambda o: o.id):
        ligne_id = of.ligne_production_id
        if ligne_id is None or ligne_id not in dispo:
            non_planifiables.append(
                (of.numero, "OF en cours sans ligne active — capacité non modélisée")
            )
            continue
        restant = _restant(of)
        debut = dispo[ligne_id]
        fin = _fin_creneau(debut, restant, debits[ligne_id])
        dispo[ligne_id] = fin
        creneaux.append(
            Creneau(
                of_id=of.id,
                numero=of.numero,
                article=of.article.code,
                quantite=restant,
                ligne_id=ligne_id,
                ligne_code=codes[ligne_id],
                debut=debut,
                fin=fin,
                echeance=of.date_echeance,
                retard_jours=_retard(fin, of.date_echeance),
                retard_h=_retard_h(fin, of.date_echeance),
                en_cours=True,
                prioritaire=of.id in rangs,
                position=None,
            )
        )

    # 2. OF PLANIFIE : triés par la règle, puis posés à la suite.
    debit_moyen = sum(debits.values()) / len(debits)
    taches = [
        _Tache(
            of_id=of.id,
            numero=of.numero,
            article=of.article.code,
            quantite=float(of.quantite_planifiee),
            echeance=of.date_echeance,
            ligne_id=of.ligne_production_id if of.ligne_production_id in dispo else None,
            # Durée de référence : estimée AVANT l'affectation (SPT/LPT/CR/SLACK en
            # ont besoin pour trier). Sur la ligne de l'OF si elle est connue, sinon
            # au débit moyen de l'atelier. Bornée pour ne jamais diviser par zéro.
            duree_ref_s=max(
                1.0,
                float(of.quantite_planifiee)
                / (
                    debits[of.ligne_production_id]
                    if of.ligne_production_id in debits
                    else debit_moyen
                ),
            ),
            rang_priorite=rangs.get(of.id, len(rangs)),
        )
        for of in ofs
        if of.statut == StatutOF.PLANIFIE
    ]
    if algo == "MOORE":
        # Moore-Hodgson est une SÉQUENCE, pas un simple critère de tri.
        taches = _sequence_moore(taches, maintenant)
    else:
        duree_moyenne = (
            sum(t.duree_ref_s for t in taches) / len(taches) if taches else 1.0
        )
        taches.sort(key=_cle_regle(algo, maintenant, duree_moyenne, taches))

    for position, t in enumerate(taches, start=1):
        # Sans ligne imposée : celle qui se libère le plus tôt (id en départage).
        ligne_id = t.ligne_id
        if ligne_id is None:
            ligne_id = min(dispo, key=lambda i: (dispo[i], i))
        debut = dispo[ligne_id]
        fin = _fin_creneau(debut, t.quantite, debits[ligne_id])
        dispo[ligne_id] = fin
        creneaux.append(
            Creneau(
                of_id=t.of_id,
                numero=t.numero,
                article=t.article,
                quantite=t.quantite,
                ligne_id=ligne_id,
                ligne_code=codes[ligne_id],
                debut=debut,
                fin=fin,
                echeance=t.echeance,
                retard_jours=_retard(fin, t.echeance),
                retard_h=_retard_h(fin, t.echeance),
                en_cours=False,
                prioritaire=t.of_id in rangs,
                position=position,
            )
        )

    return Scenario(algo, maintenant, creneaux, non_planifiables, prioritaires)


def comparer(db: Session, prioritaires: tuple[str, ...] = ()) -> list[Scenario]:
    """Joue toutes les règles sur les mêmes données. Meilleure en tête (`score_tri`)."""
    scenarios = [ordonnancer(db, algo, prioritaires) for algo in ALGORITHMES]
    return sorted(scenarios, key=lambda s: (s.score_tri, s.algorithme))


def appliquer(db: Session, scenario: Scenario) -> int:
    """Écrit le créneau (début/fin) ET la ligne des OF PLANIFIE du scénario.
    Renvoie le nombre d'OF datés.

    La ligne écrite est celle sur laquelle le moteur a POSÉ l'OF : pour un OF qui
    avait déjà une ligne, c'est la même (le moteur ne réaffecte pas) ; pour un OF
    sans ligne, c'est celle qui se libérait le plus tôt. Persister ce placement
    rend la séquence visible et rejouable sur la page Ordres (regroupée par ligne).

    N'écrit que sur les OF PLANIFIE : un OF EN_COURS garde ses dates, et un OF
    dont le statut a changé depuis le calcul est ignoré (le scénario est recalculé
    juste avant l'écriture, mais la garde reste au plus près de l'UPDATE).
    """
    dates = 0
    for c in scenario.planifies:
        of = db.get(OrdreFabrication, c.of_id)
        if of is None or of.statut != StatutOF.PLANIFIE:
            continue
        of.ligne_production_id = c.ligne_id
        of.date_debut_prevue = c.debut
        of.date_fin_prevue = c.fin
        dates += 1
    db.flush()
    return dates


def appliquer_affectation_lignes(
    db: Session, scenario: ScenarioAffectationLignes
) -> int:
    """Applique les changements de ligne et invalide les anciens créneaux."""
    modifies = 0
    for affectation in scenario.changements:
        of = db.get(OrdreFabrication, affectation.of_id)
        if of is None or of.statut != StatutOF.PLANIFIE:
            continue
        if of.ligne_production_id == affectation.ligne_proposee_id:
            continue
        of.ligne_production_id = affectation.ligne_proposee_id
        of.date_debut_prevue = None
        of.date_fin_prevue = None
        modifies += 1
    db.flush()
    return modifies


def resume_affectation_lignes(scenario: ScenarioAffectationLignes) -> str:
    """Résumé lisible par Nova et l'opérateur dans le panneau de conversation."""
    libelle = STRATEGIES_AFFECTATION[scenario.strategie]
    scope = (
        "tous les OF PLANIFIE, y compris ceux déjà affectés"
        if scenario.reaffecter
        else "les OF sans ligne ou sur une ligne incompatible uniquement"
    )
    lignes = [
        f"Simulation d'affectation {scenario.strategie} — {libelle}, sur {scope} : "
        f"{len(scenario.changements)} changement(s), "
        f"{len(scenario.affectations) - len(scenario.changements)} affectation(s) conservée(s), "
        f"{len(scenario.non_affectes)} OF non affectable(s)."
    ]
    if scenario.charges_h:
        charges = ", ".join(
            f"{scenario.codes_lignes[ligne_id]} {heures:.1f} h"
            for ligne_id, heures in sorted(
                scenario.charges_h.items(),
                key=lambda item: scenario.codes_lignes[item[0]],
            )
        )
        lignes.append(f"Charge projetée : {charges}.")
    for affectation in scenario.changements:
        actuelle = affectation.ligne_actuelle_code or "aucune"
        lignes.append(
            f"- {affectation.numero} ({affectation.article}, "
            f"{affectation.quantite:.0f} u) : {actuelle} → "
            f"{affectation.ligne_proposee_code}"
        )
    for numero, raison in scenario.non_affectes:
        lignes.append(f"- {numero} : NON AFFECTABLE — {raison}")
    if scenario.changements:
        lignes.append(
            "Ces changements invalideront les anciens créneaux ; il faudra ensuite "
            "recalculer l'ordonnancement."
        )
    return "\n".join(lignes)


def artifact_affectation_lignes(scenario: ScenarioAffectationLignes) -> dict:
    """Données structurées conservées dans l'historique de conversation."""
    return {
        "kind": "affectation_lignes",
        "strategie": scenario.strategie,
        "libelle": STRATEGIES_AFFECTATION[scenario.strategie],
        "reaffecter": scenario.reaffecter,
        "applique": False,
        "charges": [
            {
                "ligne_id": ligne_id,
                "ligne": scenario.codes_lignes[ligne_id],
                "heures": round(heures, 2),
            }
            for ligne_id, heures in sorted(scenario.charges_h.items())
        ],
        "changements": [
            {
                "of_id": a.of_id,
                "numero": a.numero,
                "article": a.article,
                "quantite": a.quantite,
                "ligne_actuelle": a.ligne_actuelle_code,
                "ligne_proposee": a.ligne_proposee_code,
            }
            for a in scenario.changements
        ],
        "non_affectes": [
            {"numero": numero, "raison": raison}
            for numero, raison in scenario.non_affectes
        ],
    }


# --------------------------- Rendu opérateur --------------------------- #


def _verdict(c: Creneau) -> str:
    if c.retard_jours > 0:
        return f"⚠ retard {c.retard_jours} j (échéance {c.echeance:%d/%m})"
    if c.echeance is None:
        return "sans échéance"
    return "dans les temps"


def resume_texte(scenario: Scenario) -> str:
    """Le plan en texte lisible par un opérateur."""
    if not scenario.creneaux:
        if scenario.non_planifiables:
            lignes = ["Aucun OF planifiable :"]
            lignes += [f"- {num} : {raison}" for num, raison in scenario.non_planifiables]
            return "\n".join(lignes)
        return "Aucun OF ouvert à ordonnancer : le backlog est vide."

    entete = (
        f"Règle {scenario.algorithme} — {scenario.libelle} : "
        f"{len(scenario.planifies)} OF planifié(s), {scenario.nb_retards} retard(s) "
        f"prévisionnel(s) ({scenario.retard_total_jours} j cumulés), "
        f"{scenario.nb_changements_serie} changement(s) de série, "
        f"plan étalé sur {scenario.makespan_h:.1f} h."
    )
    lignes = [entete]
    if scenario.prioritaires:
        lignes.append(f"Priorité forcée par l'opérateur : {', '.join(scenario.prioritaires)}.")

    for c in sorted(scenario.creneaux, key=lambda c: (c.debut, c.ligne_code)):
        rang = f"#{c.position} " if c.position is not None else ""
        etat = "EN COURS (figé)" if c.en_cours else "à lancer"
        marque = " ★" if c.prioritaire else ""
        lignes.append(
            f"- {rang}{c.numero}{marque} ({c.article}, {c.quantite:.0f} u) → {c.ligne_code}, "
            f"{etat} du {c.debut:%d/%m %H:%M} au {c.fin:%d/%m %H:%M} — {_verdict(c)}"
        )
    for num, raison in scenario.non_planifiables:
        lignes.append(f"- {num} : NON PLANIFIABLE — {raison}")

    if scenario.en_retard:
        pire = max(scenario.en_retard, key=lambda c: c.retard_jours)
        lignes.append(
            f"Point dur : {pire.numero} concentre le plus gros retard ({pire.retard_jours} j) "
            f"sur {pire.ligne_code}."
        )
    return "\n".join(lignes)


def artifact(scenario: Scenario) -> dict:
    """Artifact structuré consommé par la carte d'ordonnancement du chat."""
    return {
        "kind": "ordonnancement",
        "algorithme": scenario.algorithme,
        "libelle": scenario.libelle,
        "calcule_le": scenario.calcule_le.isoformat(),
        "applique": False,
        "prioritaires": list(scenario.prioritaires),
        "kpi": {
            "nb_of": len(scenario.planifies),
            "nb_retards": scenario.nb_retards,
            "retard_total_jours": scenario.retard_total_jours,
            "retard_total_h": round(scenario.retard_total_h, 1),
            "nb_changements_serie": scenario.nb_changements_serie,
            "makespan_h": round(scenario.makespan_h, 1),
        },
        "creneaux": [
            {
                "of_id": c.of_id,
                "numero": c.numero,
                "article": c.article,
                "quantite": c.quantite,
                "ligne": c.ligne_code,
                "position": c.position,
                "debut": c.debut.isoformat(),
                "fin": c.fin.isoformat(),
                "echeance": c.echeance.isoformat() if c.echeance else None,
                "retard_jours": c.retard_jours,
                "en_cours": c.en_cours,
                "prioritaire": c.prioritaire,
            }
            for c in sorted(scenario.creneaux, key=lambda c: (c.debut, c.ligne_code))
        ],
        "non_planifiables": [
            {"numero": num, "raison": raison} for num, raison in scenario.non_planifiables
        ],
    }


def resume_comparaison(scenarios: list[Scenario]) -> str:
    """Classement des règles, meilleure en tête."""
    if not scenarios or not scenarios[0].creneaux:
        return "Aucun OF ouvert à ordonnancer : la comparaison n'a pas d'objet."

    meilleur = scenarios[0]
    lignes = [
        f"Comparaison des {len(scenarios)} règles sur le backlog actuel "
        f"({len(meilleur.planifies)} OF planifiable(s)) :"
    ]
    for rang, s in enumerate(scenarios, start=1):
        lignes.append(
            f"{rang}. {s.algorithme} ({s.libelle}) — {s.nb_retards} retard(s), "
            f"{s.retard_total_jours} j cumulés ({s.retard_total_h:.1f} h de retard réel), "
            f"{s.nb_changements_serie} changement(s) de série, {s.makespan_h:.1f} h de plan."
        )
    lignes.append(
        f"Recommandation : {meilleur.algorithme} — c'est la règle qui laisse le moins "
        f"de retard client ({meilleur.retard_total_jours} j cumulés sur "
        f"{meilleur.nb_retards} OF, soit {meilleur.retard_total_h:.1f} h de retard réel)"
        + (
            f", et {meilleur.nb_changements_serie} changement(s) de série."
            if meilleur.nb_changements_serie
            else ", sans aucun changement de série."
        )
    )
    ex_aequo = [s.algorithme for s in scenarios[1:] if s.score_tri == meilleur.score_tri]
    if ex_aequo:
        lignes.append(
            f"À noter : {', '.join(ex_aequo)} donne(nt) exactement les mêmes KPI — "
            "le choix entre elles est indifférent sur ces données."
        )
    return "\n".join(lignes)


def artifact_comparaison(scenarios: list[Scenario]) -> dict:
    return {
        "kind": "comparaison_ordonnancement",
        "calcule_le": scenarios[0].calcule_le.isoformat() if scenarios else None,
        "recommande": scenarios[0].algorithme if scenarios else None,
        "regles": [
            {
                "algorithme": s.algorithme,
                "libelle": s.libelle,
                "nb_retards": s.nb_retards,
                "retard_total_jours": s.retard_total_jours,
                "retard_total_h": round(s.retard_total_h, 1),
                "nb_changements_serie": s.nb_changements_serie,
                "makespan_h": round(s.makespan_h, 1),
            }
            for s in scenarios
        ],
    }
