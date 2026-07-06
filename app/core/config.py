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

    # LLM
    openai_api_key: str = ""
    llm_model: str = "gpt-5.4-nano"
    llm_temperature: float = 0.0
    embedding_model: str = "text-embedding-3-small"

    # Voix (OpenAI audio)
    stt_model: str = "gpt-4o-mini-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"

    # Superviseur autonome / simulation
    supervisor_enabled: bool = True
    auto_sim_autostart: bool = False

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

    # Base documentaire (RAG) — stockage des PDF sources pour consultation/citations
    documents_pdf_dir: str = "./data/documents"

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index_name: str = "nova-index"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_embedding_dimension: int = 1536

    # Agent
    agent_max_iterations: int = 8
    agent_recursion_limit: int = 25

    @field_validator("cors_origins", "api_keys", mode="before")
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
