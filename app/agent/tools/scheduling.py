"""Outils agent d'envoi PROGRAMMÉ : « dans 5 minutes, envoie la procédure au +216… ».

Différence avec `notify` (envoi immédiat) : ici l'envoi est DIFFÉRÉ. L'agent
enregistre l'intention ; `scheduler_service` l'exécute à l'échéance et génère
le contenu À CE MOMENT-LÀ (PDF du document relu sur disque, rapport généré sur
les données du moment).

L'agent n'a pas d'horloge : le délai est donné en MINUTES à partir de maintenant
(`delai_minutes`), et le serveur calcule l'heure d'exécution. Comme tout envoi
SORTANT, la programmation exige la confirmation explicite de l'opérateur.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.agent.tools import confirmation_gate
from app.agent.tools.notify import CANAUX, CONFIRMATION_REQUISE, _destinataire_canonique
from app.agent.tools.tunicotex import _ErreurSaisie, resoudre_parametres_rapport
from app.db.session import session_scope
from app.integrations.divatex import connexion
from app.models.envoi_planifie import (
    STATUT_EN_ATTENTE,
    TYPE_DOCUMENT,
    TYPE_MESSAGE,
    TYPE_RAPPORT,
)
from app.services import document_files, scheduler_service, tunicotex_pdf

# Bornes de bon sens sur le délai (le serveur, pas le LLM, fait foi sur l'heure).
DELAI_MIN_MINUTES = 1
DELAI_MAX_MINUTES = 7 * 24 * 60  # une semaine

TYPES = (TYPE_DOCUMENT, TYPE_MESSAGE, TYPE_RAPPORT)


def _echeance(delai_minutes: int) -> datetime:
    return datetime.now() + timedelta(minutes=delai_minutes)


@tool(response_format="content_and_artifact")
def planifier_envoi(
    type_envoi: str,
    canal: str,
    destinataire: str,
    delai_minutes: int,
    confirmation: bool,
    document_nom: str | None = None,
    contenu: str | None = None,
    rapport: str | None = None,
    date: str | None = None,
    du: str | None = None,
    au: str | None = None,
    groupe: str | None = None,
    chaine: str | None = None,
    of: str | None = None,
    horizon_jours: int | None = None,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """PROGRAMME un envoi DIFFÉRÉ (exécuté plus tard, pas maintenant) par `canal`
    ("email" ou "whatsapp", ex. +216…), dans `delai_minutes` minutes.

    `type_envoi` détermine ce qui part — le contenu est produit À L'ÉCHÉANCE :
    - "document" : un document de la base documentaire, envoyé tel quel. Donne
      son nom approximatif dans `document_nom` (« procédure de coupe »).
    - "message" : un message texte libre que tu rédiges dans `contenu`.
    - "rapport" : un rapport PDF Tunicotex, `rapport` = R1 (fiche de production
      journalière), R2 (suivi des phases), R3 (rendement opérateurs), R4 (commandes
      à risque) ou R5 (fiche OF, `of` obligatoire). Mêmes paramètres que
      `rapport_pdf` (`date`, `du`, `au`, `groupe`, `chaine`, `of`, `horizon_jours`),
      résolus maintenant ; les données du rapport sont lues à l'échéance.

    À utiliser DÈS QU'un envoi est demandé pour PLUS TARD : « envoie-moi la
    procédure X dans une heure », « rappelle-moi l'équipe de nuit dans 30 min ».
    Pour un envoi IMMÉDIAT, utilise plutôt `envoyer_document` / `envoyer_message`.

    ACTION SORTANTE : récapitule TOUJOURS quoi + canal + destinataire + délai et
    obtiens un « oui » explicite avant de rappeler avec confirmation=true.
    """
    if type_envoi not in TYPES:
        return (
            f"type_envoi invalide : {type_envoi!r}. Utilise \"document\", \"message\" "
            "ou \"rapport\".",
            None,
        )
    if canal not in CANAUX:
        return f"Canal invalide : {canal!r}. Utilise \"email\" ou \"whatsapp\".", None
    if delai_minutes < DELAI_MIN_MINUTES or delai_minutes > DELAI_MAX_MINUTES:
        return (
            f"Délai invalide : {delai_minutes} min. Choisis entre {DELAI_MIN_MINUTES} "
            f"et {DELAI_MAX_MINUTES} minutes (une semaine max).",
            None,
        )
    destinataire = _destinataire_canonique(canal, destinataire)

    with session_scope() as db:
        # Résolution + validation de la cible AVANT de programmer, pour un retour
        # clair à l'opérateur (l'échec à l'échéance serait silencieux).
        document_id: int | None = None
        texte: str | None = None
        parametres: dict | None = None
        if type_envoi == TYPE_RAPPORT:
            if not rapport:
                return (
                    "Précise le `rapport` à programmer : "
                    + ", ".join(f"{k} ({v})" for k, v in tunicotex_pdf.TITRES.items())
                    + ".",
                    None,
                )
            try:
                params = resoudre_parametres_rapport(
                    rapport, date, du, au, groupe, chaine, of, horizon_jours
                )
            except _ErreurSaisie as exc:
                return str(exc), exc.artifact
            except connexion.DivatexIndisponible:
                return "Base DIVA injoignable.", None
            parametres = params.to_dict()
            quoi = f"le rapport « {tunicotex_pdf.TITRES[rapport]} »"
        elif type_envoi == TYPE_DOCUMENT:
            document = document_files.trouver_document(db, document_nom or "")
            if document is None:
                return (
                    f"Document introuvable : « {document_nom} ». Appelle "
                    "`lister_documents_disponibles` et propose le bon nom à l'opérateur.",
                    None,
                )
            document_id = document.id
            quoi = f"le document « {document.nom} »"
        else:  # TYPE_MESSAGE
            texte = (contenu or "").strip()
            if not texte:
                return "Le contenu du message programmé est vide.", None
            apercu = texte[:60] + ("…" if len(texte) > 60 else "")
            quoi = f"le message « {apercu} »"

        echeance = _echeance(delai_minutes)
        libelle = (
            f"programmer l'envoi de {quoi} par {canal} à {destinataire} "
            f"dans {delai_minutes} min (vers {echeance:%d/%m/%Y %H:%M})"
        )
        if not confirmation_gate.evaluer(
            config,
            "planifier_envoi",
            {
                "type_envoi": type_envoi,
                "canal": canal,
                "destinataire": destinataire,
                "delai_minutes": delai_minutes,
                "document_id": document_id,
                "contenu": texte,
                "rapport": rapport if type_envoi == TYPE_RAPPORT else None,
                "parametres": parametres,
            },
            confirmation,
        ):
            return (
                f"Confirmation requise : {libelle}. " + CONFIRMATION_REQUISE,
                {"kind": "confirmation_attente", "libelle": libelle},
            )

        demande_par = (config or {}).get("configurable", {}).get("thread_id")
        envoi = scheduler_service.programmer(
            db,
            type_envoi=type_envoi,
            canal=canal,
            destinataire=destinataire,
            execute_at=echeance,
            document_id=document_id,
            contenu=texte,
            rapport=rapport if type_envoi == TYPE_RAPPORT else None,
            parametres=parametres,
            demande_par=demande_par,
        )
        envoi_id = envoi.id

    return (
        f"✅ Envoi programmé (n°{envoi_id}) : {quoi} par {canal} à {destinataire}, "
        f"vers {echeance:%d/%m/%Y à %H:%M}. Je m'en occupe à l'heure dite.",
        {"kind": "action_executee", "libelle": libelle},
    )


@tool
def lister_envois_planifies() -> str:
    """Liste les envois PROGRAMMÉS encore en attente (id, quoi, canal, heure).

    À utiliser pour « qu'est-ce que tu as programmé ? », « quels envois sont
    prévus ? », ou avant d'annuler un envoi (`annuler_envoi`) pour retrouver son id.
    """
    with session_scope() as db:
        envois = scheduler_service.envois_en_attente(db)
        if not envois:
            return "Aucun envoi programmé en attente."
        lignes = [
            f"- n°{e.id} : {e.libelle()} — prévu {e.execute_at:%d/%m/%Y à %H:%M}"
            for e in envois
        ]
    return "Envois programmés en attente :\n" + "\n".join(lignes)


@tool(response_format="content_and_artifact")
def annuler_envoi(envoi_id: int, confirmation: bool, *, config: RunnableConfig) -> tuple[str, dict | None]:
    """ANNULE un envoi programmé encore en attente, par son `envoi_id`
    (voir `lister_envois_planifies`).

    À utiliser pour « annule l'envoi programmé », « n'envoie finalement pas le
    document de tout à l'heure ». Récapitule quel envoi et obtiens un « oui » avant
    de rappeler avec confirmation=true.
    """
    with session_scope() as db:
        envoi = db.get(scheduler_service.EnvoiPlanifie, envoi_id)
        if envoi is None or envoi.statut != STATUT_EN_ATTENTE:
            return f"Aucun envoi programmé en attente avec l'id {envoi_id}.", None
        libelle = f"annuler l'envoi programmé n°{envoi_id} ({envoi.libelle()})"
        if not confirmation_gate.evaluer(
            config, "annuler_envoi", {"envoi_id": envoi_id}, confirmation
        ):
            return (
                f"Confirmation requise : {libelle}. " + CONFIRMATION_REQUISE,
                {"kind": "confirmation_attente", "libelle": libelle},
            )
        scheduler_service.annuler(db, envoi_id)
    return f"✅ Envoi programmé n°{envoi_id} annulé.", {"kind": "action_executee", "libelle": libelle}


SCHEDULING_TOOLS = [planifier_envoi, lister_envois_planifies, annuler_envoi]
