from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET_KEY = "dev-only-insecure-key-set-SECRET_KEY-before-any-shared-deployment"


class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "postgresql://sih26016:sih26016@db:5432/sih26016"
    frontend_origin: str = "http://localhost:5173"
    upload_dir: str = "/app/uploads"
    secret_key: str = DEV_SECRET_KEY
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 12 * 60
    max_upload_bytes: int = 10 * 1024 * 1024
    land_records_provider: str = "mock"
    rules_interval_minutes: int = 0
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300

    # Development/SIH convenience only. Keep false in production: the loader
    # creates fictional workflow records, not government records.
    demo_seed_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in ("production", "prod")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origin.split(",") if origin.strip()]

    def validate_for_environment(self) -> None:
        if not self.is_production:
            return

        problems = []
        if self.secret_key == DEV_SECRET_KEY:
            problems.append(
                "SECRET_KEY is still the built-in development value. Generate one with:\n"
                '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if len(self.secret_key) < 32:
            problems.append("SECRET_KEY is shorter than 32 characters.")
        if any(origin == "*" for origin in self.cors_origins):
            problems.append(
                "FRONTEND_ORIGIN is '*'. Name the deployed frontend origin explicitly; "
                "credentials are sent with requests and a wildcard cannot carry them."
            )
        if "@db:5432" in self.database_url:
            problems.append(
                "DATABASE_URL still points at the docker-compose database host 'db'. "
                "Set the managed database URL (Supabase) instead."
            )
        if self.demo_seed_enabled:
            problems.append("DEMO_SEED_ENABLED must be false in production.")

        if problems:
            raise RuntimeError(
                "Refusing to start in production with an unsafe configuration:\n  - "
                + "\n  - ".join(problems)
            )


settings = Settings()
