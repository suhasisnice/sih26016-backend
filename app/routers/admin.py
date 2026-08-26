"""Administrative operations. Admin role only, every one of them audited."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.enums import Role
from app.dependencies import get_db, require_role
from app.models import User
from app.schemas.dashboard import RunRulesResult
from app.services import alerts, audit

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/run-rules", response_model=RunRulesResult)
def run_rules(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.ADMIN)),
):
    """Re-run every alert rule and rebuild the alerts table.

    Admin only, because it rewrites what every other user sees on their
    dashboard. Safe to run repeatedly: the rules are pure functions of the
    current data, so running it twice in a row produces the same alerts.
    """
    summary = alerts.regenerate_alerts(db)
    audit.record(
        db,
        user,
        action="admin.run_rules",
        entity_type="alert",
        detail=f"{summary['alerts_generated']} alerts from {summary['cases_evaluated']} cases",
    )
    db.commit()
    return RunRulesResult(**summary)
