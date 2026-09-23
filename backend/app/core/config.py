from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The two defaults above that are actual secrets, not just convenience
# values - fine to ship as defaults (development/tests need *something*
# to boot with), but never fine to still be in place once ENVIRONMENT
# says this is a real deployment. Day 47.
_PLACEHOLDER_JWT_SECRET_KEY = "changeme-generate-a-long-random-secret"
_PLACEHOLDER_DATABASE_URL_MARKER = ":changeme@"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"
    database_url: str = "postgresql+asyncpg://repomind:changeme@localhost:5432/repomind"
    jwt_secret_key: str = "changeme-generate-a-long-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    github_token: str = ""
    max_repo_size_mb: int = 250
    max_file_size_kb: int = 500
    chunk_max_lines: int = 100
    chunk_overlap_lines: int = 15
    # Qdrant's own score_threshold on /points/search (app/services/
    # vector_store.py's DISTANCE_METRIC is Cosine similarity - higher is
    # more relevant, not a distance). A live QA pass's own observed range
    # against this project's real ingested content and real embedding
    # model: genuinely relevant queries scored ~0.31-0.39; a deliberately
    # nonsensical query scored ~0.20-0.22. 0.25 sits with margin on both
    # sides of that gap, closer to the nonsense ceiling than the relevant
    # floor - deliberately conservative, since one QA session's sample
    # isn't a rigorous study: it should reliably exclude the "clearly
    # nothing matches" case without risking cutting off a real, if
    # less-than-perfect, match. Below this, Qdrant returns nothing for a
    # query rather than the nearest neighbors regardless of relevance.
    search_score_threshold: float = 0.25
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    # "openai" | "local" (Day 51) - see app/services/embedding_providers.py.
    # openai_api_key is only ever read when this is "openai"; the "local"
    # provider (fastembed, in-process) needs no API key at all.
    embedding_provider: str = "openai"
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection_name: str = "repomind_code_chunks"
    # "anthropic" | "groq" - see app/services/llm.py. anthropic_api_key/
    # anthropic_model are only read when this is "anthropic"; groq_api_key/
    # groq_model only when this is "groq".
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    redis_url: str = "redis://localhost:6379/0"

    # Rate limiting (Day 46) - a Redis-backed fixed-window counter per
    # (scope, identifier). Each scope below protects a group of endpoints
    # of similar cost; see app/core/rate_limit.py. Auth's windows are
    # deliberately an hour, not a minute: it's IP-scoped (unauthenticated),
    # and this project's own e2e suite registers ~20 users per full CI run
    # from a single runner IP - a short window sized for a single user's
    # realistic usage would make the test suite itself flaky.
    rate_limit_enabled: bool = True
    rate_limit_ingestion_limit: int = 20
    rate_limit_ingestion_window_seconds: int = 3600
    rate_limit_ai_limit: int = 20
    rate_limit_ai_window_seconds: int = 60
    rate_limit_agent_limit: int = 5
    rate_limit_agent_window_seconds: int = 60
    rate_limit_auth_register_limit: int = 30
    rate_limit_auth_register_window_seconds: int = 3600
    rate_limit_auth_login_limit: int = 40
    rate_limit_auth_login_window_seconds: int = 3600

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _reject_insecure_production_defaults(self) -> "Settings":
        # Refuses to start rather than silently running production traffic
        # through a forgeable JWT secret or a guessable database password -
        # cheap to check once at startup, expensive to discover any other
        # way. Development/tests are untouched: these two values existing
        # as defaults at all is what lets a fresh checkout boot with zero
        # configuration, per every "cp .env.example .env" step in the
        # README - the check only fires once ENVIRONMENT says this is real.
        if self.environment != "production":
            return self

        problems = []
        if not self.jwt_secret_key or self.jwt_secret_key == _PLACEHOLDER_JWT_SECRET_KEY:
            # Empty, not just the placeholder, matters here specifically
            # because of how Docker Compose substitutes an unset variable
            # with no fallback (${VAR}, no :-default) - it resolves to an
            # empty string rather than leaving the variable unset, which
            # would otherwise have fallen through to this same field's
            # class default (the placeholder, already caught above) instead.
            problems.append("JWT_SECRET_KEY is missing or still the placeholder default")
        if _PLACEHOLDER_DATABASE_URL_MARKER in self.database_url:
            problems.append("DATABASE_URL still has the placeholder 'changeme' password")

        if problems:
            raise ValueError(
                "Refusing to start with ENVIRONMENT=production and insecure "
                "defaults still in place: " + "; ".join(problems) + ". Set "
                "real values via environment variables before deploying."
            )
        return self


settings = Settings()
