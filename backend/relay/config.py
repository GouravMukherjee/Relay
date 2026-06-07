"""Relay runtime configuration.

Reads every value from environment variables (or a .env file in the working directory).
Importing this module NEVER raises on missing credentials — individual adapters validate
required creds at construction time, not at import time.

Usage::

    from relay.config import settings          # module-level singleton
    from relay.config import get_settings      # or the lru_cache'd factory
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables / .env file.

    All field names are snake_case here; the corresponding env-var is the
    UPPER_SNAKE_CASE equivalent (pydantic-settings default behaviour).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Don't raise if .env is missing — it may not be present in CI / prod.
        env_ignore_empty=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://relay:relay@localhost:5432/relay",
        description="Async SQLAlchemy URL (must use asyncpg driver).",
    )

    # ------------------------------------------------------------------
    # Redis (arq queue + cache)
    # ------------------------------------------------------------------
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL used by arq and the cache layer.",
    )

    # ------------------------------------------------------------------
    # Supabase (auth + optional hosted DB)
    # ------------------------------------------------------------------
    supabase_url: str = Field(
        default="",
        description="Supabase project URL, e.g. https://<ref>.supabase.co.",
    )
    supabase_anon_key: str = Field(
        default="",
        description="Supabase anon/public JWT key (safe for client-side use).",
    )
    supabase_service_key: str = Field(
        default="",
        description="Supabase service-role key (server-side only — never expose).",
    )
    supabase_jwt_issuer: Optional[str] = Field(
        default=None,
        description="Override the JWKS issuer URL. Defaults to supabase_url.",
    )
    supabase_jwt_secret: Optional[str] = Field(
        default=None,
        description=(
            "If set, ALSO accept HS256 tokens signed with this secret. "
            "Used for local dev and tests — not a production fallback."
        ),
    )

    # ------------------------------------------------------------------
    # Moss retrieval service
    # ------------------------------------------------------------------
    moss_project_id: str = Field(default="", description="Moss project id (moss.dev).")
    moss_project_key: str = Field(default="", description="Moss project key (moss.dev).")
    moss_index_name: str = Field(
        default="relay",
        description="Moss index name holding the tenant knowledge chunks.",
    )
    moss_memory_index_name: str = Field(
        default="relay-memory",
        description="Moss index name holding per-customer memory (Desk mode).",
    )
    moss_hybrid_alpha: float = Field(
        default=0.5,
        description="Moss hybrid search weight (0=keyword, 1=semantic). 0.5 = balanced.",
    )
    moss_model_id: str = Field(
        default="",
        description="Moss embedding model id; empty = SDK default (moss-minilm).",
    )
    # Deprecated (pre-SDK HTTP adapter); kept so old env/secrets don't error.
    moss_api_key: str = Field(default="", description="Deprecated — unused (SDK uses project id/key).")
    moss_base_url: str = Field(default="", description="Deprecated — unused (SDK uses project id/key).")

    # ------------------------------------------------------------------
    # LiveKit (audio transport + room management)
    # ------------------------------------------------------------------
    livekit_url: str = Field(
        default="",
        description="LiveKit server WebSocket URL, e.g. wss://<project>.livekit.cloud.",
    )
    livekit_api_key: str = Field(default="", description="LiveKit API key.")
    livekit_api_secret: str = Field(
        default="",
        description="LiveKit API secret (server-side only — never expose).",
    )
    livekit_stt_model: str = Field(
        default="assemblyai/universal-streaming",
        description=(
            "STT model string routed through LiveKit Inference (billed against "
            "LiveKit credits via LIVEKIT_API_KEY/SECRET — no separate STT account). "
            "Passed to AgentSession(stt=...)."
        ),
    )
    livekit_agent_name: str = Field(
        default="relay-agent",
        description=(
            "Dispatch name the agent worker registers with (WorkerOptions.agent_name). "
            "Setting it requires EXPLICIT dispatch — the gateway dispatches this name to "
            "each session room, and SIP dispatch rules target it for inbound phone calls."
        ),
    )
    livekit_demo_room: str = Field(
        default="relay-demo",
        description=(
            "Fixed LiveKit room for the inbound-phone demo. The agent is dispatched here "
            "and transcribes whoever joins (a SIP caller OR the rep's browser mic); the "
            "rep dashboard's Live view watches this room by default so cards stream in "
            "with no manual room selection."
        ),
    )
    stt_min_endpointing_delay: float = Field(
        default=0.2,
        description=(
            "Seconds of trailing silence the agent waits before finalizing a turn "
            "(EndpointingOptions.min_delay). LiveKit's default is 0.5s and is additive "
            "on top of the STT's own endpointing — lowering it makes finals fire faster "
            "after the speaker stops, cutting perceived latency."
        ),
    )
    stt_max_endpointing_delay: float = Field(
        default=1.5,
        description=(
            "Upper bound (seconds) the agent waits before forcing turn end "
            "(EndpointingOptions.max_delay). Default is 3.0s; lowered so a trailing "
            "pause never stalls a card."
        ),
    )

    # ------------------------------------------------------------------
    # Unsiloed (document parsing)
    # ------------------------------------------------------------------
    unsiloed_api_key: str = Field(default="", description="Unsiloed API key (api-key header).")
    unsiloed_base_url: str = Field(
        default="https://prod.visionapi.unsiloed.ai",
        description="Unsiloed vision API base URL.",
    )

    # ------------------------------------------------------------------
    # TrueFoundry AI Gateway (LLM routing)
    # ------------------------------------------------------------------
    tfy_api_key: str = Field(
        default="",
        description="TrueFoundry API key for the AI Gateway.",
    )
    tfy_gateway_url: str = Field(
        default="https://llm-gateway.truefoundry.com/api/inference/openai",
        description="TrueFoundry AI Gateway base URL (OpenAI-compatible).",
    )
    tfy_model: str = Field(
        default="anthropic/claude-sonnet-4-5",
        description=(
            "Provider-prefixed chat model id on the TFY gateway "
            "(e.g. 'anthropic/claude-sonnet-4-5'). Routed via /chat/completions."
        ),
    )
    tfy_embedding_model: str = Field(
        default="openai-main/text-embedding-3-small",
        description=(
            "Provider-prefixed embedding model id on the TFY gateway. Must be a model "
            "your TFY account can access; produces vectors reduced to embedding_dim."
        ),
    )
    tfy_fallback_models: str = Field(
        default="",
        description=(
            "Comma-separated provider-prefixed model ids to try (in order) if the primary "
            "tfy_model call fails — automatic LLM failover, e.g. 'qwen/qwen-plus'."
        ),
    )
    tfy_fast_model: str = Field(
        default="anthropic/claude-haiku",
        description=(
            "Fast, low-latency model used ONLY for live card synthesis (cards are 1–2 "
            "sentences, so a small model is both faster and cheaper). Everything else "
            "stays on tfy_model. If the fast model fails, synthesis falls back to "
            "tfy_model and the configured fallbacks. "
            "# TODO: confirm <TrueFoundry> API — exact Haiku gateway id."
        ),
    )
    card_max_tokens: int = Field(
        default=150,
        description=(
            "Max output tokens for live card synthesis. Cards are 1–2 cited sentences, "
            "so a tight cap keeps latency low and discourages rambling answers."
        ),
    )

    # ------------------------------------------------------------------
    # LLM provider keys (routed through TFY gateway)
    # ------------------------------------------------------------------
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key (used by the Claude path via TFY gateway).",
    )
    minimax_api_key: str = Field(default="", description="Minimax API key.")
    minimax_group_id: str = Field(default="", description="Minimax GroupId (required by the T2A API).")
    minimax_base_url: str = Field(default="https://api.minimax.io", description="Minimax API base URL.")
    minimax_tts_model: str = Field(default="speech-02-turbo", description="Minimax TTS model (low-latency).")
    minimax_voice_id: str = Field(default="male-qn-qingse", description="Default Minimax voice id.")
    qwen_api_key: str = Field(default="", description="Qwen / Alibaba DashScope API key.")
    qwen_base_url: str = Field(
        default="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        description="DashScope OpenAI-compatible base URL (intl).",
    )
    qwen_embedding_model: str = Field(
        default="text-embedding-v3",
        description="Qwen embedding model (1024-d).",
    )

    # ------------------------------------------------------------------
    # AWS / S3 (raw file storage)
    # ------------------------------------------------------------------
    aws_access_key_id: str = Field(default="", description="AWS access key ID.")
    aws_secret_access_key: str = Field(
        default="",
        description="AWS secret access key (never log or expose).",
    )
    aws_region: str = Field(default="us-east-1", description="AWS region.")
    s3_bucket: str = Field(
        default="relay-documents",
        description="S3 bucket for raw uploaded documents.",
    )

    # ------------------------------------------------------------------
    # Slack (lead routing notifications)
    # ------------------------------------------------------------------
    slack_webhook_url: str = Field(
        default="",
        description="Incoming webhook URL for Slack lead-routing notifications.",
    )

    # ------------------------------------------------------------------
    # Application / runtime settings
    # ------------------------------------------------------------------
    frontend_origin: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        description=(
            "Allowed CORS origin(s). Comma-separated list supported (e.g. a localhost "
            "dev origin plus the deployed frontend URL). Trailing slashes are stripped. "
            "Parsed into a list by the `cors_origins` property — never use '*' with credentials."
        ),
    )
    llm_model: str = Field(
        default="claude",
        description="Active LLM model identifier: claude | qwen | minimax.",
    )
    embedding_dim: int = Field(
        default=1024,
        description="Embedding vector dimension; must match the embeddings model.",
    )
    default_org_id: str = Field(
        default="00000000-0000-0000-0000-000000000001",
        description="UUID string of the single demo/local-dev organisation.",
    )
    app_db_role: str = Field(
        default="relay_app",
        description="Postgres role used by the application (subject to RLS).",
    )

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------
    @property
    def cors_origins(self) -> list[str]:
        """Parse ``frontend_origin`` into a clean list of allowed CORS origins.

        Splits on commas, strips surrounding whitespace, and strips any trailing
        slash (a trailing slash makes the browser's Origin header fail to match).
        Empty entries are dropped. Used as ``allow_origins`` in the CORS middleware
        (with ``allow_credentials=True``, so a wildcard is never used).
        """
        return [
            origin.strip().rstrip("/")
            for origin in self.frontend_origin.split(",")
            if origin.strip()
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton.

    The cache is intentionally module-level so the .env file is only parsed once
    per process. In tests, call ``get_settings.cache_clear()`` and rebuild the env
    before importing modules that use ``settings``.
    """
    return Settings()


# Module-level singleton — the canonical import target for application code.
settings: Settings = get_settings()
