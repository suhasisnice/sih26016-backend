from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings


def _normalise(url: str) -> str:
    """Accept the URL shapes a managed host actually hands out.

    Supabase and Render both still print `postgres://` in places, which
    SQLAlchemy 2 refuses — it wants the driver named. Rewriting it here
    means a copy-pasted connection string works instead of failing at boot
    with a dialect error that reads like a bug in the app.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


DATABASE_URL = _normalise(settings.database_url)

# Supabase's transaction pooler (port 6543) is pgbouncer. It multiplexes
# many client connections onto few server ones, which breaks anything that
# assumes a session persists between statements — including the prepared
# statements psycopg2 caches and SQLAlchemy's own connection pool, which
# would be a pool on top of a pool.
#
# NullPool hands the pooling job entirely to pgbouncer and opens a fresh
# connection per checkout. The alternative is the direct connection (port
# 5432), which keeps normal pooling but caps out at far fewer connections
# and is not what Supabase recommends for a serverless-ish web service.
_is_pooled = ":6543" in DATABASE_URL or "pooler.supabase.com" in DATABASE_URL

# SSL is keyed off the host, not the port: a direct Supabase connection
# (5432) needs it just as much as the pooled one (6543), while the local
# docker-compose database does not accept it at all. Deciding by port would
# have silently dropped SSL on the direct Supabase connection.
_LOCAL_HOSTS = ("@db:", "@localhost:", "@127.0.0.1:", "@host.docker.internal:")
_is_local = any(marker in DATABASE_URL for marker in _LOCAL_HOSTS)
_connect_args = {} if _is_local else {"sslmode": "require"}

if _is_pooled:
    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        pool_pre_ping=True,
        connect_args=_connect_args,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        # Render's free tier sleeps; a connection held across a sleep comes
        # back dead. Recycling well inside any idle timeout avoids handing
        # a stale one to the first request after a wake-up.
        pool_recycle=300,
        connect_args=_connect_args,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
