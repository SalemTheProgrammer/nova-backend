"""Outils agent : décisions du superviseur autonome, dans la conversation.

Le superviseur (`supervisor_service`) détecte les problèmes de l'atelier et crée
des propositions. Le panneau Nova étant un chat, c'est ici que l'opérateur les
consulte et les tranche en langage naturel (« oui, bascule l'OF », « non »).
Approuver exécute l'action réelle : même garde-fou de confirmation que les
commandes SCADA (voir `confirmation_gate`). Les cartes Oui/Non du chat passent,
elles, directement par l'API (`routes_agent`) — un clic est déjà un accord.
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy import select

from app.agent.tools import confirmation_gate
from app.agent.tools.actions import _action_executee, _demande_confirmation
from app.core.exceptions import AppError
from app.db.session import session_scope
from app.models import AgentProposal
from app.models.enums import StatutProposition
from app.services import supervisor_service


@tool
def lister_decisions_en_attente() -> str:
    """Liste les décisions du superviseur Nova qui attendent l'accord de l'opérateur
    (id, gravité, titre, action proposée, diagnostic chiffré). Lecture seule.

    À utiliser pour « qu'est-ce qui attend ma décision ? », ou avant
    `decider_proposition` quand l'opérateur répond sans préciser laquelle.
    """
    with session_scope() as db:
        propositions = db.execute(
            select(AgentProposal)
            .where(AgentProposal.statut == StatutProposition.PROPOSEE)
            .order_by(AgentProposal.created_at.desc())
        ).scalars().all()
        if not propositions:
            return "Aucune décision en attente."
        lignes = [f"{len(propositions)} décision(s) en attente, de la plus récente à la plus ancienne :"]
        for p in propositions:
            lignes.append(
                f"- id={p.id} [{p.severite.value}] {p.titre} — action proposée : "
                f"{p.action_libelle}. Diagnostic : {p.diagnostic}"
            )
        return "\n".join(lignes)


@tool(response_format="content_and_artifact")
def decider_proposition(
    proposition_id: int, approuver: bool, confirmation: bool, *, config: RunnableConfig
) -> tuple[str, dict | None]:
    """Tranche une décision du superviseur : approuver=true exécute l'action proposée
    (bascule d'OF, maintenance, pause qualité, alerte, message fournisseur…),
    approuver=false la rejette.

    ACTION : décrivez l'action et obtenez l'accord explicite de l'opérateur avant
    d'appeler avec confirmation=true.
    """
    with session_scope() as db:
        proposition = db.get(AgentProposal, proposition_id)
        if proposition is None:
            return f"Décision introuvable (id={proposition_id}).", None
        if proposition.statut != StatutProposition.PROPOSEE:
            deja = f"Cette décision a déjà été traitée ({proposition.statut.value})."
            return f"{deja} {proposition.resultat or ''}".strip(), None
        libelle = (
            proposition.action_libelle[:1].lower() + proposition.action_libelle[1:]
            if approuver
            else f"ignorer la décision « {proposition.titre} »"
        )
    if not confirmation_gate.evaluer(
        config,
        "decider_proposition",
        {"proposition_id": proposition_id, "approuver": approuver},
        confirmation,
    ):
        return _demande_confirmation(libelle)

    thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
    try:
        with session_scope() as db:
            proposition = supervisor_service.decider(
                db,
                proposition_id,
                approuver=approuver,
                canal="chat",
                identite=f"thread:{thread_id}" if thread_id else None,
            )
            statut, resultat = proposition.statut, proposition.resultat or ""
    except AppError as exc:
        return f"❌ {exc.message}", None

    if not approuver:
        return f"Décision ignorée. {resultat}", _action_executee(libelle)
    if statut == StatutProposition.EXECUTEE:
        return f"✅ {resultat}", _action_executee(libelle)
    return f"❌ {resultat}", None


SUPERVISION_TOOLS = [lister_decisions_en_attente, decider_proposition]
