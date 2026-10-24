"""Registre des conversations : qui a écrit quel thread de l'agent.

Tout ici est « best effort » côté chat : enregistrer le propriétaire d'un thread
ne doit JAMAIS faire échouer une réponse de Nova (base indisponible, compte de
test absent…). Les erreurs sont journalisées et le chat continue.
"""
from __future__ import annotations

import re
import sqlite3

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.core.paths import chat_history_db_path
from app.db.session import session_scope
from app.models.conversation import CANAL_WEB, CANAL_WHATSAPP, Conversation
from app.models.utilisateur import Utilisateur

logger = get_logger(__name__)

APERCU_MAX = 200
PREFIXE_WHATSAPP = "wa:"
# « [WhatsApp — Nom +216…] » ajouté par la route WhatsApp pour l'agent.
_RE_PREFIXE_IDENTITE = re.compile(r"^\[WhatsApp — [^\]]*\]\s*")


def sans_prefixe_identite(texte: str) -> str:
    """Retire le préfixe d'identité WhatsApp destiné à l'agent, pas à l'humain."""
    return _RE_PREFIXE_IDENTITE.sub("", texte or "")


def enregistrer(
    thread_id: str,
    canal: str,
    *,
    utilisateur_id: int | None = None,
    numero: str = "",
    message: str | None = None,
) -> None:
    """Crée ou rafraîchit l'entrée du thread. Un thread déjà attribué à un autre
    utilisateur n'est jamais réattribué (un identifiant de thread deviné ne
    permet pas de « s'approprier » la conversation d'un collègue)."""
    try:
        with session_scope() as db:
            conv = db.get(Conversation, thread_id)
            if conv is None:
                conv = Conversation(thread_id=thread_id, canal=canal, numero=numero)
                conv.utilisateur_id = utilisateur_id
                db.add(conv)
            elif conv.utilisateur_id is None and utilisateur_id is not None:
                conv.utilisateur_id = utilisateur_id
            elif utilisateur_id is not None and conv.utilisateur_id != utilisateur_id:
                return
            if message and not conv.apercu:
                conv.apercu = sans_prefixe_identite(message).strip()[:APERCU_MAX]
            conv.date_maj = func.now()
    except Exception as exc:  # noqa: BLE001
        logger.warning("conversation_registre_echec", thread_id=thread_id, error=str(exc))


def peut_lire(user: Utilisateur, thread_id: str) -> bool:
    """L'historique d'un thread est réservé à son auteur et aux administrateurs.
    Un thread inconnu du registre (créé avant ce registre) reste accessible."""
    if user.is_admin:
        return True
    try:
        with session_scope() as db:
            conv = db.get(Conversation, thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("conversation_registre_lecture_echec", thread_id=thread_id, error=str(exc))
        return True
    return conv is None or conv.utilisateur_id in (None, user.id)


def synchroniser_whatsapp() -> int:
    """Ajoute au registre les threads WhatsApp du checkpointer qui n'y sont pas
    encore (conversations antérieures au registre), rattachés au compte dont le
    numéro correspond. Renvoie le nombre de threads ajoutés."""
    try:
        con = sqlite3.connect(f"file:{chat_history_db_path()}?mode=ro", uri=True)
        try:
            lignes = con.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ?",
                (PREFIXE_WHATSAPP + "%",),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("conversation_synchro_whatsapp_echec", error=str(exc))
        return 0

    ajoutes = 0
    with session_scope() as db:
        connus = set(db.execute(select(Conversation.thread_id)).scalars())
        comptes = {u.telephone: u.id for u in db.execute(select(Utilisateur)).scalars()}
        for (thread_id,) in lignes:
            if thread_id in connus:
                continue
            numero = thread_id[len(PREFIXE_WHATSAPP):]
            db.add(
                Conversation(
                    thread_id=thread_id,
                    canal=CANAL_WHATSAPP,
                    numero=numero,
                    utilisateur_id=comptes.get(numero),
                )
            )
            ajoutes += 1
    return ajoutes


__all__ = [
    "CANAL_WEB",
    "CANAL_WHATSAPP",
    "enregistrer",
    "peut_lire",
    "sans_prefixe_identite",
    "synchroniser_whatsapp",
]
