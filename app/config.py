from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://sih26016:sih26016@db:5432/sih26016"
    frontend_origin: str = "http://localhost:5173"
    upload_dir: str = "/app/uploads"

    # Signs and verifies JWTs. A default exists so a fresh clone runs, and
    # is acceptable only because this stack is loopback-only and its data
    # is entirely synthetic. Anything reachable by other people MUST set
    # SECRET_KEY in .env: whoever holds this value can mint a valid token
    # for any user and any role, admin included.
    secret_key: str = "dev-only-insecure-key-set-SECRET_KEY-before-any-shared-deployment"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 12 * 60

    # Cap on uploaded documents, enforced in the documents router.
    max_upload_bytes: int = 10 * 1024 * 1024

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)


settings = Settings()
