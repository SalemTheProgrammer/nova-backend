"""Application configuration loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # Security
    # NoDecode: keep the raw env string so `_split_csv` parses the CSV
    # (otherwise pydantic-settings tries to JSON-decode the list first).
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
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

    # Superviseur autonome / simulation
    supervisor_enabled: bool = True
    auto_sim_autostart: bool = False
    # Autonomie du superviseur : "manuel" (comportement historique, tout attend
    # l'opérateur), "assiste" (risque FAIBLE exécuté seul, immédiatement),
    # "autopilote" (idem + risque MOYEN exécuté seul après un délai, sauf rejet
    # explicite avant l'échéance). Modifiable en runtime via
    # POST /api/v1/agent/autonomie (voir supervisor_service.definir_mode_autonomie) —
    # ce réglage n'est que la valeur de démarrage.
    autonomy_mode_defaut: Literal["manuel", "assiste", "autopilote"] = "manuel"
    # Délai (secondes) avant l'exécution automatique d'une proposition à risque
    # MOYEN en mode "autopilote" — le temps pour l'opérateur de dire non.
    autopilote_delai_moyen_s: int = 45
    # Nova proactive : les propositions du superviseur partent aussi par WhatsApp
    # vers ces numéros (CSV) ; l'opérateur répond oui/non depuis son téléphone.
    supervisor_notify_numbers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # False par défaut : toutes les propositions (dérive qualité, stock bas,
    # retard prévisionnel…) partent sur WhatsApp, pas seulement les CRITICAL —
    # c'est le comportement décrit dans NOVA-AI.md et attendu en démo.
    supervisor_notify_critical_only: bool = False

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
    # au moment de l'envoi, contrairement à `envoyer_rapport` en conversation).
    auto_bilan_enabled: bool = False
    auto_bilan_canal: Literal["email", "whatsapp"] = "email"
    auto_bilan_destinataire: str = ""
    # Heures de déclenchement (HH:MM, séparées par des virgules) — par défaut,
    # les fins de poste usuelles en 3x8.
    auto_bilan_heures: str = "06:00,14:00,22:00"

    # Base documentaire (RAG) — stockage des PDF sources pour consultation/citations
    documents_pdf_dir: str = "./data/documents"

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index_name: str = "nova-index"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_embedding_dimension: int = 1536

    # Agent
    agent_recursion_limit: int = 25

    @field_validator(
        "cors_origins",
        "api_keys",
        "whatsapp_allowed_numbers",
        "supervisor_notify_numbers",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
