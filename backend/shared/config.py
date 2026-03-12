from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Postgres
    postgres_url: str = "postgresql+asyncpg://argus:argus@localhost:5432/argus"

    # Synchronous URL used by Alembic (runs migrations in sync context)
    postgres_url_sync: str = "postgresql+psycopg2://argus:argus@localhost:5432/argus"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "argus-1234"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # OpenAlex polite pool — include a real email to get higher rate limits
    openalex_email: str = "admin@argus.local"

    # Crawler
    openalex_rate_limit: int = 10  # requests per second
    crawl_page_size: int = 200     # works per API page (OpenAlex max is 200)


settings = Settings()
