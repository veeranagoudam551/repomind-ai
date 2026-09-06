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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
