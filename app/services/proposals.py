"""The proposal approval chain: who may move a proposal where, and what
happens when one is sanctioned.

This is the front half of the lifecycle the problem statement describes and
the system did not previously have. A proposal is submitted by the body that
wants the land, scrutinised by the state, and sanctioned centrally. Only on
sanction does a Case come into existence — which is why every case before
this had to be conjured directly at preliminary notification by whichever
district officer happened to be looking.

The transition table below is the whole point of the module. Modelling it as
data rather than as a chain of ifs means one place to read when asking "can
this role do this?", and it is the same shape as the stage machine in
workflow.py, deliberately: two workflows that behave differently for no
reason are two workflows somebody will get wrong.
"""

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.enums import CaseStatus, ProposalStatus, Role, Stage
from app.models import (
    Case,
    CaseStageHistory,
    District,
    Project,
    Proposal,
    ProposalReview,
    State,
    User,
    Village,
)
from app.services import audit, numbering, sla

# (from_status) -> {to_status: roles allowed to make that move}
#
# Read this as the org chart it is. The requiring body owns a proposal until
# it is submitted and can withdraw it at any point before a decision. The
# state scrutinises. The ministry decides. District Officer also sits on the
# approval step (not just scrutiny) so a district-only demo — DC scrutinises,
# SLAO gets the case — can run without a State/Ministry login; Ministry's own
# approval right is unchanged. Nobody approves their own submission: the
# requiring body never appears on the approval side of any hand-off.
TRANSITIONS: dict[ProposalStatus, dict[ProposalStatus, tuple[Role, ...]]] = {
    ProposalStatus.DRAFT: {
        ProposalStatus.SUBMITTED: (Role.REQUIRING_BODY, Role.ADMIN),
        ProposalStatus.WITHDRAWN: (Role.REQUIRING_BODY, Role.ADMIN),
    },
    ProposalStatus.SUBMITTED: {
        # Picking a proposal up for scrutiny. Separate from deciding it, so
        # the requiring body can see that somebody has actually started.
        ProposalStatus.UNDER_SCRUTINY: (Role.STATE_OFFICER, Role.DISTRICT_OFFICER, Role.ADMIN),
        ProposalStatus.RETURNED: (Role.STATE_OFFICER, Role.DISTRICT_OFFICER, Role.ADMIN),
        ProposalStatus.WITHDRAWN: (Role.REQUIRING_BODY, Role.ADMIN),
    },
    ProposalStatus.UNDER_SCRUTINY: {
        ProposalStatus.APPROVED: (Role.MINISTRY_OFFICER, Role.DISTRICT_OFFICER, Role.ADMIN),
        ProposalStatus.REJECTED: (Role.MINISTRY_OFFICER, Role.ADMIN),
        ProposalStatus.RETURNED: (
            Role.STATE_OFFICER,
            Role.MINISTRY_OFFICER,
            Role.ADMIN,
        ),
        ProposalStatus.WITHDRAWN: (Role.REQUIRING_BODY, Role.ADMIN),
    },
    # Returned means "fix it and send it back", so it goes to DRAFT and the
    # cycle repeats. This is the commonest real outcome and the reason
    # RETURNED is not folded into REJECTED.
    ProposalStatus.RETURNED: {
        ProposalStatus.DRAFT: (Role.REQUIRING_BODY, Role.ADMIN),
        ProposalStatus.WITHDRAWN: (Role.REQUIRING_BODY, Role.ADMIN),
    },
    # Terminal.
    ProposalStatus.APPROVED: {},
    ProposalStatus.REJECTED: {},
    ProposalStatus.WITHDRAWN: {},
}

# Which tier is holding the file, for the "with whom" column on the list.
# Derived from status rather than stored, so the two cannot disagree.
OWNER_BY_STATUS = {
    ProposalStatus.DRAFT: "Requiring body",
    ProposalStatus.SUBMITTED: "State — awaiting scrutiny",
    ProposalStatus.UNDER_SCRUTINY: "Ministry — awaiting sanction",
    ProposalStatus.RETURNED: "Requiring body — revision required",
    ProposalStatus.APPROVED: "Sanctioned",
    ProposalStatus.REJECTED: "Closed",
    ProposalStatus.WITHDRAWN: "Withdrawn",
}


def allowed_transitions(current: ProposalStatus, role: Role) -> list[ProposalStatus]:
    """What this role may move this proposal to, right now.

    Returned to the frontend on the detail response so the buttons a user
    sees match what the server will actually accept. The server still checks
    on the way in — this is a convenience, never the enforcement.
    """
    return [
        target
        for target, roles in TRANSITIONS.get(current, {}).items()
        if role in roles
    ]


def transition(
    db: Session,
    proposal: Proposal,
    to_status: ProposalStatus,
    user: User,
    note: str | None = None,
    on_date: date | None = None,
) -> Proposal:
    """Move a proposal, refusing anything this role may not do.

    Writes a ProposalReview row and an audit entry, and does not commit —
    the caller owns the transaction so the move, its review record and its
    audit row all land together or not at all.
    """
    if to_status == proposal.status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proposal is already '{proposal.status.value}'",
        )

    permitted = TRANSITIONS.get(proposal.status, {})
    if to_status not in permitted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot move a proposal from '{proposal.status.value}' to "
                f"'{to_status.value}'. Allowed: "
                f"{sorted(s.value for s in permitted)}"
            ),
        )
    if user.role not in permitted[to_status]:
        # 403 not 404 here, unlike cases: the caller can already see this
        # proposal, so refusing loudly leaks nothing and telling them which
        # office has to act is the whole point of a routing system.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Role '{user.role.value}' may not move a proposal to "
                f"'{to_status.value}'. That decision rests with: "
                f"{sorted(r.value for r in permitted[to_status])}"
            ),
        )

    effective = on_date or date.today()
    from_status = proposal.status

    proposal.status = to_status
    proposal.status_changed_on = effective
    if to_status is ProposalStatus.SUBMITTED and proposal.submitted_on is None:
        proposal.submitted_on = effective
        proposal.submitted_by_user_id = user.id
    if to_status in (ProposalStatus.APPROVED, ProposalStatus.REJECTED):
        proposal.decided_by_user_id = user.id
        proposal.decided_on = effective
        proposal.decision_note = note

    db.add(
        ProposalReview(
            proposal_id=proposal.id,
            from_status=from_status,
            to_status=to_status,
            actor_user_id=user.id,
            actor_role=user.role,
            note=note,
            created_on=effective,
        )
    )
    audit.record(
        db,
        user,
        action="proposal.transition",
        entity_type="proposal",
        entity_id=proposal.id,
        detail=f"{from_status.value} -> {to_status.value}",
    )
    return proposal


def sanction_to_case(db: Session, proposal: Proposal, user: User, on_date: date | None = None) -> Case:
    """Turn an approved proposal into a live acquisition case.

    Called only after the transition to APPROVED has succeeded. The case
    opens at preliminary notification — the stage everything in this system
    used to start at — so from here on it is an ordinary case and every
    existing rule, KPI and route applies to it unchanged.

    A project row is created for the requiring body if one does not already
    exist for that body in that district, so the project-wise dashboard
    figures include sanctioned proposals without anybody having to pre-register
    a project by hand.
    """
    if proposal.case_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proposal already became case {proposal.case_id}",
        )

    effective = on_date or date.today()
    district = db.get(District, proposal.district_id)
    village = db.get(Village, proposal.village_id)
    if district is None or village is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Proposal references a district or village that no longer exists",
        )

    project = (
        db.query(Project)
        .filter(
            Project.requiring_body == proposal.requiring_body,
            Project.district_id == proposal.district_id,
            Project.name == proposal.title,
        )
        .first()
    )
    if project is None:
        project = Project(
            name=proposal.title[:160],
            requiring_body=proposal.requiring_body,
            district_id=proposal.district_id,
        )
        db.add(project)
        db.flush()

    case = Case(
        case_number=numbering.next_case_number(db, district, effective.year),
        title=proposal.title[:200],
        project_id=project.id,
        district_id=proposal.district_id,
        village_id=proposal.village_id,
        stage=Stage.PRELIMINARY_NOTIFICATION,
        status=CaseStatus.ACTIVE,
        stage_changed_at=effective,
        created_at=effective,
    )
    sla.apply_due_date(db, case)
    db.add(case)
    db.flush()

    proposal.case_id = case.id
    proposal.project_id = project.id

    db.add(
        CaseStageHistory(
            case_id=case.id,
            from_stage=None,
            to_stage=Stage.PRELIMINARY_NOTIFICATION,
            changed_by_user_id=user.id,
            changed_on=effective,
            note="Case opened from approved proposal",
        )
    )

    audit.record(
        db,
        user,
        action="proposal.sanctioned",
        entity_type="proposal",
        entity_id=proposal.id,
        detail=f"became case {case.case_number}",
    )
    return case


def next_number(db: Session, state: State, on_date: date | None = None) -> str:
    return numbering.next_proposal_number(db, state, (on_date or date.today()).year)
