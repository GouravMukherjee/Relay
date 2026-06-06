"""Central config — reads from env (12-factor). See .env.example for the full set."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql://relay:relay@postgres:5432/relay"

    # Internal services
    retrieval_url: str = "http://retrieval:8001"

    # Moss
    moss_api_key: str = ""
    moss_endpoint: str = ""

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # STT
    deepgram_api_key: str = ""

    # LLM
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # Ingestion
    unsiloed_api_key: str = ""

    # App
    log_level: str = "info"
    embedding_dim: int = 1024


settings = Settings()
