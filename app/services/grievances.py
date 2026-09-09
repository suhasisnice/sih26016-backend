"""Grievance category/role mapping and status-change recording.

Mirrors two things already established elsewhere rather than inventing new
patterns: workflow.STAGE_RESPONSIBLE_ROLE (a category-to-role display
mapping, not an access-control boundary — see the docstring below) and
workflow.advance_case's own history-writing shape (append a row, let the
caller commit).
"""

from datetime import date

from sqlalchemy.orm import Session

from app.core.enums import GrievanceCategory, GrievanceStatus, Role
from app.models import Grievance, GrievanceStatusHistory, User

# Who may respond to / update a grievance. Mirrors objections.OBJECTION_RESPONDERS
# exactly — the same case-administering roles that answer an objection are
# who fields a service complaint about the same case.
GRIEVANCE_RESPONDERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO)

OPEN_STATUSES = (
    GrievanceStatus.SUBMITTED,
    GrievanceStatus.ASSIGNED,
    GrievanceStatus.UNDER_REVIEW,
    GrievanceStatus.INFO_REQUIRED,
    GrievanceStatus.RESPONSE_PROVIDED,
)

# Which role a grievance category is naturally routed to, for display only
# ("Assigned To") — same caveat app.services.case_report's _RESPONSIBLE_ROLE
# states explicitly: no table in this schema names a specific officer as
# owning a grievance, only a role. Answering one is still gated by
# GRIEVANCE_RESPONDERS above, not by this mapping — a compensation
# grievance categorised here as the SLAO's does not mean an RNR Officer is
# refused; the case-administering trio can act on any category, the same
# way workflow.CASE_STAGE_OWNERS can advance any stage.
GRIEVANCE_CATEGORY_ROLE: dict[GrievanceCategory, Role] = {
    GrievanceCategory.LAND_PROPERTY: Role.SLAO,
    GrievanceCategory.COMPENSATION: Role.SLAO,
    GrievanceCategory.DOCUMENT: Role.SLAO,
    GrievanceCategory.SURVEY_MEASUREMENT: Role.FIELD_OFFICER,
    GrievanceCategory.NOTICE_NOTIFICATION: Role.SLAO,
    GrievanceCategory.REHABILITATION_RESETTLEMENT: Role.RNR_OFFICER,
    GrievanceCategory.ACQUISITION_OBJECTION: Role.SLAO,
    GrievanceCategory.DELAY_IN_PROCESSING: Role.DISTRICT_OFFICER,
    GrievanceCategory.OTHER: Role.SLAO,
}


def record_status_change(
    db: Session,
    grievance: Grievance,
    to_status: GrievanceStatus,
    user: User | None,
    note: str | None = None,
    on_date: date | None = None,
) -> GrievanceStatusHistory:
    """Move `grievance` to `to_status` and append the history row that
    makes it a real timeline. Does not commit — the caller owns the
    transaction, same as workflow.advance_case."""
    from_status = grievance.status
    effective_date = on_date or date.today()

    grievance.status = to_status
    entry = GrievanceStatusHistory(
        grievance_id=grievance.id,
        from_status=from_status,
        to_status=to_status,
        changed_by_user_id=user.id if user else None,
        changed_on=effective_date,
        note=note,
    )
    db.add(entry)
    return entry
