from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET_KEY = "dev-only-insecure-key-set-SECRET_KEY-before-any-shared-deployment"


class Settings(BaseSettings):
    # "development" or "production". Production turns the dev conveniences
    # off and turns the guards on — see validate_for_environment below.
    # Render sets this via the dashboard; docker-compose leaves it as dev.
    environment: str = "development"

    database_url: str = "postgresql://sih26016:sih26016@db:5432/sih26016"

    # Comma-separated in the environment, so a deployment can allow its own
    # frontend and a local dev origin at once:
    #   FRONTEND_ORIGIN=https://bhoomimitra.onrender.com,http://localhost:5173
    frontend_origin: str = "http://localhost:5173"

    upload_dir: str = "/app/uploads"

    # Signs and verifies JWTs. A default exists so a fresh clone runs, and
    # is acceptable only because that stack is loopback-only with synthetic
    # data. Whoever holds this value can mint a valid token for any user and
    # any role, admin included — so in production the app refuses to start
    # with it rather than trusting anyone to have read the comment.
    secret_key: str = DEV_SECRET_KEY
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 12 * 60

    # Cap on uploaded documents, enforced in the documents router.
    max_upload_bytes: int = 10 * 1024 * 1024

    # Which external land-record portal the integration talks to. "mock" is a
    # simulation and says so on every response it produces — see
    # app/integrations. A deployment with real credentials for a state
    # revenue portal points this at that adapter instead; nothing else in the
    # application changes.
    land_records_provider: str = "mock"

    # How often the alert rules re-run themselves, in minutes. 0 disables the
    # in-process scheduler — the default, so a developer working against a
    # seeded database does not get the alert table rewritten underneath them.
    # A deployment sets this (60 is sensible) so that "the system notices
    # something is overdue" does not depend on somebody calling an endpoint.
    rules_interval_minutes: int = 0

    # Login attempts allowed per IP per window, enforced in the auth router.
    # Generous enough that a demo never trips it, tight enough that a
    # password guesser gets nowhere.
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in ("production", "prod")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origin.split(",") if origin.strip()]

    def validate_for_environment(self) -> None:
        """Refuse to start a production process that is quietly insecure.

        A comment telling somebody to set SECRET_KEY is not a control: the
        one deployment where it gets skipped is the one where anybody can
        mint an admin token. Failing at boot is loud, immediate, and happens
        before the service is reachable — which is the only point where this
        is cheap to fix.
        """
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

        if problems:
            raise RuntimeError(
                "Refusing to start in production with an unsafe configuration:\n  - "
                + "\n  - ".join(problems)
            )


settings = Settings()
