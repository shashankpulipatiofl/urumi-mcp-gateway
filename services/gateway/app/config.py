"""Gateway settings. Every value can be set with an environment variable of the same name
(case-insensitive). Secrets (SECRET_KEY, ENCRYPTION_KEYS, ADMIN_PASSWORD, LLM_API_KEY, the
seed server credentials) are injected from Kubernetes Secrets, never hardcoded."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # --- storage -------------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./gateway.db"

    # --- secrets -------------------------------------------------------------------
    secret_key: str = ""                 # signs dashboard sessions + internal chat tokens
    encryption_keys: str = ""            # comma-separated; first one encrypts, all can decrypt (rotation)

    # --- bootstrap -----------------------------------------------------------------
    admin_email: str = "admin@example.com"
    admin_password: str = ""
    admin_name: str = "Admin"
    seed_servers_json: str = ""          # JSON list of upstream servers to register on first start

    # --- upstream behaviour ----------------------------------------------------------
    health_interval_seconds: float = 15.0   # how often every upstream is probed
    connect_timeout_seconds: float = 3.0    # TCP connect timeout to an upstream
    list_timeout_seconds: float = 8.0       # whole initialize + tools/list probe
    call_timeout_seconds: float = 20.0      # whole tools/call round trip

    # --- SSRF protection (security hardening) -------------------------------------------
    # Hostname patterns that may resolve to private IPs (our in-cluster upstreams).
    allowed_upstream_hosts: str = "*.svc.cluster.local"
    # Block upstream URLs that resolve to private / loopback / link-local / metadata IPs.
    block_private_upstreams: bool = True

    # --- limits ----------------------------------------------------------------------
    rate_limit_per_minute: int = 120        # tool calls per user per minute (0 = unlimited)
    session_hours: int = 12
    invite_days: int = 7

    # --- public URLs (for copy-paste snippets in the dashboard) ---------------------------
    public_base_url: str = "http://app.localhost"
    cookie_secure: bool = False             # True in production (HTTPS)

    # --- chat agent --------------------------------------------------------------------
    llm_provider: str = "gemini"            # gemini | openai (any OpenAI-compatible API: OpenAI, Groq, Ollama)
    llm_model: str = "gemini-2.5-flash-lite"   # fast, thinking switched off
    llm_api_key: str = ""
    llm_base_url: str = ""                  # for openai-compatible providers
    llm_max_steps: int = 8                  # max tool-call rounds per chat message
    llm_timeout_seconds: float = 60.0
    # Tried in order when the main model is overloaded (HTTP 429/5xx). Free tiers get busy.
    llm_fallback_models: str = "gemini-flash-lite-latest,gemini-flash-latest"
    # "low" = minimal hidden reasoning before answering -> much faster for simple tool lookups.
    llm_thinking: str = "low"               # low | default
    # The chat agent is a real MCP client of this gateway's own /mcp endpoint
    self_mcp_url: str = "http://127.0.0.1:8000/mcp"

    log_level: str = "INFO"

    @property
    def allowed_host_patterns(self) -> list[str]:
        return [p.strip().lower() for p in self.allowed_upstream_hosts.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
