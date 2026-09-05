from fastapi import APIRouter
from sqlalchemy import text

from app.core.enums import (
    AlertSeverity,
    CaseStatus,
    CompensationStatus,
    DocType,
    NoticeType,
    ObjectionStatus,
    ParcelStatus,
    ProposalStatus,
    RiskBand,
    Role,
    RnRStatus,
    Stage,
    TimelineStatus,
)
from app.database import engine

router = APIRouter(tags=["meta"])


def _values(enum_cls) -> list[str]:
    return [e.value for e in enum_cls]


@router.get("/health")
def health():
    """Is the API and database up. Real check, not a stub."""
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"api": "ok", "database": "ok" if db_ok else "unreachable"}


@router.get("/meta/enums")
def get_enums():
    """All valid stages, statuses, roles, doc types. Frontend never hardcodes these."""
    return {
        "stages": _values(Stage),
        "case_statuses": _values(CaseStatus),
        "compensation_statuses": _values(CompensationStatus),
        "rnr_statuses": _values(RnRStatus),
        "objection_statuses": _values(ObjectionStatus),
        "parcel_statuses": _values(ParcelStatus),
        "alert_severities": _values(AlertSeverity),
        "roles": _values(Role),
        "doc_types": _values(DocType),
        "proposal_statuses": _values(ProposalStatus),
        "notice_types": _values(NoticeType),
        "timeline_statuses": _values(TimelineStatus),
        "risk_bands": _values(RiskBand),
    }
