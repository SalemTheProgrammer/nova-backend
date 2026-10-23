"""Application configuration loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "nova-agent-backend"
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Database
    database_url: str = "sqlite:///./nova.db"
    # Historique des conversations (checkpointer SQLite) : vide = à côté de la
    # base SQLite, sinon ./data/nova_chat_history.db (voir core/paths.py).
    chat_history_db_path: str = ""

    # Divatex (base DIVA du client, accès en lecture seule)
    divatex_url: str = ""
    divatex_statement_timeout_s: int = 15
    divatex_cache_ttl_s: int = 60
    # "auto" = date de la dernière lecture, "today" = aujourd'hui, ou AAAA-MM-JJ.
    divatex_date_reference: str = "auto"
    divatex_temps_facteur: float = 1.0  # provisoire : "Temps" DIVA semble déjà en minutes
    divatex_appliquer_majoration: bool = True
    divatex_duree_poste_min: int = 480
    divatex_phase_finale: int = 12
    divatex_calibre: bool = False

    # Security
    # NoDecode: keep the raw env string so `_split_csv` parses the CSV
    # (otherwise pydantic-settings tries to JSON-decode the list first).
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Authentification par numéro de téléphone (code de vérification WhatsApp).
    # AUTH_SECRET signe les jetons de session (HMAC) — À CHANGER en production.
    auth_secret: str = "change-me-in-production-nova-auth-secret"
    # Durée de validité d'un jeton de session (7 jours par défaut).
    auth_token_ttl_s: int = 604800
    # Durée de validité d'un code de vérification (5 minutes).
    verification_code_ttl_s: int = 300
    # Numéro administrateur : accès à tous les outils + page d'administration.
    # Créé/mis à jour automatiquement au démarrage (voir auth_service.seed_admin).
    admin_phone: str = ""

    # LLM
    openai_api_key: str = ""
    llm_model: str = "gpt-5.4-nano"
    llm_temperature: float = 0.0
    embedding_model: str = "text-embedding-3-small"

    # Voix (OpenAI audio)
    stt_model: str = "gpt-4o-mini-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    # Vision (analyse des photos envoyées à Nova sur WhatsApp)
    vision_model: str = "gpt-4o-mini"

    # Notifications sortantes (envoi du bilan / messages par Nova)
    # E-mail : n'importe quel SMTP (Gmail : smtp.gmail.com + mot de passe d'application).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # défaut : smtp_user
    # WhatsApp : service Baileys local (dossier whatsapp/ du projet, `npm start`
    # puis scan du QR code une seule fois).
    whatsapp_service_url: str = "http://localhost:3001"
    # WhatsApp entrant : dialoguer avec Nova en lui écrivant sur WhatsApp.
    # Liste blanche CSV de numéros autorisés (ex. "+21612345678,+21698765432") ;
    # vide = tout numéro accepté (confort démo).
    whatsapp_inbound_enabled: bool = True
    whatsapp_allowed_numbers: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Bilan automatique (envoi périodique sans opérateur) : désactivé par défaut,
    # l'activation en config tient lieu d'accord préalable (pas de confirmation
    # au moment de l'envoi, contrairement aux envois en conversation).
    auto_bilan_enabled: bool = False
    auto_bilan_canal: Literal["email", "whatsapp"] = "email"
    auto_bilan_destinataire: str = ""
    # Heures de déclenchement (HH:MM, séparées par des virgules) — par défaut,
    # le rapport du matin.
    auto_bilan_heures: str = "07:30"
    # Rapport envoyé automatiquement (R1 = fiche de production journalière).
    auto_bilan_rapport: Literal["R1", "R2", "R3", "R4"] = "R1"

    # Base documentaire (RAG) — stockage des PDF sources pour consultation/citations
    documents_pdf_dir: str = "./data/documents"

    # PostgreSQL + pgvector (RAG documentaire)
    vector_database_url: str = ""
    vector_table_name: str = "document_chunks"
    vector_embedding_dimension: int = 1536

    # Agent
    agent_recursion_limit: int = 25

    @field_validator(
        "cors_origins",
        "api_keys",
        "whatsapp_allowed_numbers",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def database_url_masquee(self) -> str:
        """URL de la base sans mot de passe, sûre à journaliser."""
        try:
            return make_url(self.database_url).render_as_string(hide_password=True)
        except ArgumentError:
            return "***"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
