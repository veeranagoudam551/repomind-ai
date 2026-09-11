from pydantic_settings import BaseSettings, SettingsConfigDict


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
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection_name: str = "repomind_code_chunks"
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
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


settings = Settings()
