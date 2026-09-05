"""KPI 6 -- timeline adherence, computed as of the call rather than stored
(see app.services.sla for why).

A case with no `stage_due_on` is untracked, not on time -- an empty
denominator is unknown, not perfect. Adherence counts a case still "at
risk" as adhering: only an actual breach counts against the figure, since
at-risk is a warning about the future, not a fact about today.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.core.enums import TimelineStatus
from app.models import Case
from app.services import sla

FIELDS = (
    "timeline_on_time_count",
    "timeline_at_risk_count",
    "timeline_breached_count",
    "timeline_untracked_count",
)


def timeline_kpis(db: Session, case_ids: list[int], as_of: date) -> dict:
    if not case_ids:
        return {**{field: 0 for field in FIELDS}, "timeline_adherence_pct": None}

    sla_table = sla.load_sla(db)
    rows = db.query(Case.stage, Case.stage_due_on).filter(Case.id.in_(case_ids)).all()

    on_time = at_risk = breached = untracked = 0
    for stage, due_on in rows:
        if due_on is None:
            untracked += 1
            continue
        status = sla.timeline_status(due_on, stage, as_of, sla_table)
        if status is TimelineStatus.ON_TIME:
            on_time += 1
        elif status is TimelineStatus.AT_RISK:
            at_risk += 1
        else:
            breached += 1

    tracked = on_time + at_risk + breached
    adherence = round((on_time + at_risk) / tracked * 100, 1) if tracked else None

    return {
        "timeline_on_time_count": on_time,
        "timeline_at_risk_count": at_risk,
        "timeline_breached_count": breached,
        "timeline_untracked_count": untracked,
        "timeline_adherence_pct": adherence,
    }
