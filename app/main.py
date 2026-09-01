from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401  registers every table on Base.metadata
from app.config import settings
from app.services import sla
from app.database import Base, SessionLocal, engine
from app.routers import (
    admin,
    auth,
    cases,
    dashboard,
    documents,
    exports,
    meta,
    notices,
    notifications,
    objections,
    parcels,
    persons,
    proposals,
    reference,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fails the boot rather than the audit: a production process with the
    # built-in SECRET_KEY, a wildcard CORS origin or the compose database
    # URL stops here, before it is reachable. No-op in development.
    settings.validate_for_environment()

    # create_all only ever adds missing tables; it never alters one whose
    # columns have changed. While the schema is still moving, reset with
    # `python -m app.ai_layer.seed --rebuild` rather than expecting this
    # to migrate anything.
    #
    # PostGIS must already exist in the target database — create_all cannot
    # add an extension. On Supabase, enable it once (see DEPLOYMENT.md);
    # locally, init-extensions.sql does it on first boot of the db volume.
    Base.metadata.create_all(bind=engine)

    # Stage deadlines are reference data the timeline features cannot work
    # without, so they are written on startup rather than left to a seed run
    # that a fresh clone may never do. Idempotent: it only fills gaps.
    with SessionLocal() as session:
        added = sla.seed_defaults(session)
        if added:
            session.commit()
    yield


app = FastAPI(title="SIH26016 - Land Acquisition Management System", lifespan=lifespan)

# One or more origins, comma-separated in FRONTEND_ORIGIN — a deployment
# usually needs its own frontend plus a local one for debugging against it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(reference.router)
# Unauthenticated by design — a notice a citizen must log in to read has
# not been published in any sense the Act would recognise.
app.include_router(notices.router)
app.include_router(auth.router)
app.include_router(cases.router)
# The proposal pipeline: submission, scrutiny, sanction. Registered before
# parcels only for readability — the lifecycle reads in order.
app.include_router(proposals.router)
app.include_router(parcels.router)
app.include_router(persons.router)
# Compensation and R&R are edited on their own paths, by different
# offices, and are never reconciled into one endpoint.
app.include_router(persons.compensation_router)
app.include_router(persons.rnr_router)
app.include_router(documents.router)
app.include_router(objections.router)
app.include_router(dashboard.router)
# Per-user inbox, and the MIS exports a reviewing officer asks for.
app.include_router(notifications.router)
app.include_router(exports.router)
app.include_router(admin.router)
