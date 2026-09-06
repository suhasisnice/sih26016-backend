"""Which stage a case may legally move to, and recording the move.

The nine stages run in the order set by the RFCTLARR Act 2013. A case
advances one step at a time and never skips: skipping would mean, for
example, taking possession without ever holding the objection period, which
is exactly the kind of thing this system exists to prevent.

Moving backwards is allowed only to the immediately previous stage, because
in practice a case does get sent back when a survey or an award is found
wanting. Anything further back is refused — that is a data-entry mistake,
not a legal step.
"""

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.enums import CaseStatus, ObjectionStatus, Stage, SurveyTaskStatus
from app.models import Case, CaseStageHistory, Objection, SurveyTask, User
from app.services import audit, sla

STAGE_ORDER: list[Stage] = list(Stage)
TERMINAL_STAGE = STAGE_ORDER[-1]


def allowed_transitions(current: Stage) -> list[Stage]:
    index = STAGE_ORDER.index(current)
    allowed = []
    if index > 0:
        allowed.append(STAGE_ORDER[index - 1])
    if index < len(STAGE_ORDER) - 1:
        allowed.append(STAGE_ORDER[index + 1])
    return allowed


def next_stage(current: Stage) -> Stage | None:
    index = STAGE_ORDER.index(current)
    if index >= len(STAGE_ORDER) - 1:
        return None
    return STAGE_ORDER[index + 1]


def advance_case(
    db: Session,
    case: Case,
    to_stage: Stage,
    user: User,
    note: str | None = None,
    on_date: date | None = None,
) -> Case:
    """Move a case to to_stage, refusing anything the Act does not allow.

    Writes a CaseStageHistory row and an audit entry, and does not commit —
    the caller owns the transaction so the move, its history and its audit
    record all land together or not at all.
    """
    if to_stage == case.stage:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Case is already at stage '{case.stage.value}'",
        )

    if to_stage not in allowed_transitions(case.stage):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot move from '{case.stage.value}' to '{to_stage.value}'. "
                f"Allowed: {[s.value for s in allowed_transitions(case.stage)]}"
            ),
        )

    # An objection filed under s.21 has to be disposed of before the
    # declaration under s.19 can issue — advancing past it with the
    # objection still open would let the declaration outrun its own record.
    if case.stage is Stage.OBJECTION_PERIOD and to_stage is Stage.DECLARATION:
        open_objections = (
            db.query(Objection)
            .filter(
                Objection.case_id == case.id,
                Objection.status.in_((ObjectionStatus.FILED, ObjectionStatus.UNDER_REVIEW)),
            )
            .count()
        )
        if open_objections:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot move to 'declaration': {open_objections} objection(s) "
                    "still open on this case."
                ),
            )

    # Likewise, a survey still out with the field officer means the land
    # under s.12 has not actually been verified yet. A case with no survey
    # task at all is unaffected — most cases never get one assigned.
    if case.stage is Stage.LAND_VERIFICATION and to_stage is Stage.OBJECTION_PERIOD:
        open_surveys = (
            db.query(SurveyTask)
            .filter(
                SurveyTask.case_id == case.id,
                SurveyTask.status.in_(
                    (
                        SurveyTaskStatus.ASSIGNED,
                        SurveyTaskStatus.IN_PROGRESS,
                        SurveyTaskStatus.SUBMITTED,
                        SurveyTaskStatus.RETURNED,
                    )
                ),
            )
            .count()
        )
        if open_surveys:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot move to 'objection_period': {open_surveys} survey "
                    "task(s) still open on this case."
                ),
            )

    from_stage = case.stage
    effective_date = on_date or date.today()

    case.stage = to_stage
    case.stage_changed_at = effective_date
    # The deadline for the stage the case is now in. Written here rather
    # than derived on read, so timeline adherence is one indexed column
    # instead of a stage_sla join on every dashboard query — and written in
    # this transaction, so it can never describe a stage the case left.
    sla.apply_due_date(db, case)
    # A case that moved is active again by definition. Whether it has since
    # gone quiet is the stalled-case rule's call, not ours.
    if to_stage is TERMINAL_STAGE:
        case.status = CaseStatus.CLOSED
    else:
        case.status = CaseStatus.ACTIVE

    db.add(
        CaseStageHistory(
            case_id=case.id,
            from_stage=from_stage,
            to_stage=to_stage,
            changed_by_user_id=user.id,
            changed_on=effective_date,
            note=note,
        )
    )

    audit.record(
        db,
        user,
        action="case.advance_stage",
        entity_type="case",
        entity_id=case.id,
        detail=f"{from_stage.value} -> {to_stage.value}",
    )
    return case
