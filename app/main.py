from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401  registers every table on Base.metadata
from app.config import settings
from app.database import Base, engine
from app.routers import (
    admin,
    auth,
    cases,
    dashboard,
    documents,
    meta,
    objections,
    parcels,
    persons,
    reference,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # create_all only ever adds missing tables; it never alters one whose
    # columns have changed. While the schema is still moving, reset with
    # `python -m app.ai_layer.seed --rebuild` rather than expecting this
    # to migrate anything.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="SIH26016 - Land Acquisition Management System", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(reference.router)
app.include_router(auth.router)
app.include_router(cases.router)
app.include_router(parcels.router)
app.include_router(persons.router)
app.include_router(documents.router)
app.include_router(objections.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
