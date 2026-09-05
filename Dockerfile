# Production image. docker-compose overrides the command with --reload for
# local development, so the dev convenience lives there rather than here —
# a hot-reloading watcher in a deployed container is a stability and
# performance problem, not a feature.
FROM python:3.12-slim

WORKDIR /app

# gcc and libpq-dev are only needed while pip builds wheels; dropping them
# afterwards keeps the deployed image smaller and its attack surface
# narrower than the build environment that produced it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove gcc \
    && rm -rf /var/lib/apt/lists/*

COPY ./app ./app
COPY ./scripts ./scripts
# The migrations are part of the image, not something applied out of
# band. A container that can serve the app can always bring its own
# schema up to head first.
COPY ./alembic ./alembic
COPY ./alembic.ini ./alembic.ini

# Uploaded documents live here. On Render this is ephemeral unless a disk
# is attached — see DEPLOYMENT.md, which says so plainly rather than
# letting the first lost upload be the discovery.
RUN mkdir -p /app/uploads

# Render supplies PORT and expects the process to bind it. The default
# keeps `docker run` working locally without one set.
ENV PORT=8000
EXPOSE 8000

# Exec form wrapping an explicit shell, rather than plain shell form. $PORT
# still has to be expanded at runtime, so a shell is unavoidable; the `exec`
# is what matters. Without it uvicorn runs as a child of /bin/sh, and the
# SIGTERM Render sends on every deploy and every sleep goes to the shell
# instead of the server -- no graceful shutdown, just a SIGKILL once the
# grace period runs out, mid-request. `exec` replaces the shell with
# uvicorn so the signal lands on the process that can act on it.
# `alembic upgrade head` runs first and is not backgrounded: if the
# migration fails the container exits non-zero and the deploy is
# marked failed, which is the correct outcome. Starting the API
# against a schema that did not migrate would serve 500s that read
# like code bugs.
#
# It is idempotent — on an already-current database alembic reads
# one row from alembic_version and returns.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
