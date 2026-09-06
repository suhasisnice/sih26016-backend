from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import models  # noqa: F401  registers every table on Base.metadata
from app.config import settings
from app.services import scheduler, sla
from app.database import Base, SessionLocal, engine
from app.routers import (
    admin, auth, biometrics, cases, dashboard, documents, exports,
    integrations, meta, mfa, notices, notifications, objections, parcels,
    persons, projects, proposals, public_records, reference, survey,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_for_environment()
    Base.metadata.create_all(bind=engine)

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE parcels ADD COLUMN IF NOT EXISTS boundary geometry(Polygon, 4326)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_parcels_boundary ON parcels USING gist (boundary)"))

    with SessionLocal() as session:
        added = sla.seed_defaults(session)
        if added:
            session.commit()

    # Optional SIH/demo convenience. The seed is idempotent and explicitly
    # synthetic; production validation above refuses to start with it enabled.
    if settings.demo_seed_enabled:
        from app.demo_seed import seed_demo
        seed_demo()

    if settings.real_seed_enabled:
        from app.real_seed import seed_real
        seed_real()

    sweep = scheduler.start(app)
    try:
        yield
    finally:
        await scheduler.stop(sweep)


app = FastAPI(title="SIH26016 - Land Acquisition Management System", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(reference.router)
app.include_router(public_records.router)
app.include_router(notices.router)
app.include_router(auth.router)
app.include_router(mfa.router)
app.include_router(biometrics.router)
app.include_router(cases.router)
app.include_router(projects.router)
app.include_router(proposals.router)
app.include_router(parcels.router)
app.include_router(survey.router)
app.include_router(persons.router)
app.include_router(persons.compensation_router)
app.include_router(persons.rnr_router)
app.include_router(documents.router)
app.include_router(objections.router)
app.include_router(dashboard.router)
app.include_router(notifications.router)
app.include_router(exports.router)
app.include_router(integrations.router)
app.include_router(admin.router)
