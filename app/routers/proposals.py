"""Proposals: submit, scrutinise, sanction.

The front half of the lifecycle the problem statement describes and the
system did not previously have — "online submission, verification, approval
and tracking of proposals". Everything before this began at preliminary
notification, which is *after* the part being described.

Routing rules live in app.services.proposals, not here. This router owns who
may see what and the shape of the response; the service owns which tier may
move a file where. Keeping those apart is what stops a permission check from
being duplicated in two places and drifting.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import AlertSeverity, ProposalStatus, Role
from app.dependencies import (
    get_current_user,
    get_db,
    require_role,
    scope_proposals_to_user,
)
from app.models import District, Proposal, ProposalReview, State, User, Village
from app.schemas.proposal import (
    PaginatedProposals,
    ProposalCreate,
    ProposalDetail,
    ProposalListItem,
    ProposalReviewOut,
    ProposalTransition,
    ProposalUpdate,
)
from app.services import audit, notify, proposals as proposal_service

router = APIRouter(prefix="/proposals", tags=["proposals"])

# Who may open a proposal at all. A landowner cannot: a proposal names a
# village and a project, not a person, and there is nothing here for them.
PROPOSAL_AUTHORS = (Role.REQUIRING_BODY, Role.ADMIN)

# Roles whose decisions are worth telling the submitter about immediately.
DECISION_STATUSES = (
    ProposalStatus.APPROVED,
    ProposalStatus.REJECTED,
    ProposalStatus.RETURNED,
)


def _visible_proposal_or_404(db: Session, user: User, proposal_id: int) -> Proposal:
    """404 rather than 403 outside the caller's scope, matching cases.py: a
    403 confirms the proposal exists, which is itself something a competing
    requiring body should not be able to fish for by trying ids."""
    proposal = (
        scope_proposals_to_user(db.query(Proposal), user)
        .filter(Proposal.id == proposal_id)
        .first()
    )
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    return proposal


def _list_item(proposal: Proposal, state_name: str, district_name: str,
               village_name: str, today: date) -> ProposalListItem:
    return ProposalListItem(
        id=proposal.id,
        proposal_number=proposal.proposal_number,
        title=proposal.title,
        requiring_body=proposal.requiring_body,
        status=proposal.status,
        held_by=proposal_service.OWNER_BY_STATUS.get(proposal.status, "—"),
        state_id=proposal.state_id,
        state_name=state_name,
        district_id=proposal.district_id,
        district_name=district_name,
        village_name=village_name,
        estimated_area_ha=proposal.estimated_area_ha,
        estimated_families=proposal.estimated_families,
        submitted_on=proposal.submitted_on,
        status_changed_on=proposal.status_changed_on,
        days_in_status=(today - proposal.status_changed_on).days,
        case_id=proposal.case_id,
    )


@router.get("", response_model=PaginatedProposals)
def list_proposals(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    proposal_status: ProposalStatus | None = None,
    state_id: int | None = None,
    district_id: int | None = None,
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """The proposal pipeline, scoped to what this user may see.

    Ordered oldest-first by status change, so the file that has been waiting
    longest is at the top — a queue, not a newest-first feed. A proposal
    sitting untouched is the failure this screen exists to expose.
    """
    base = (
        db.query(Proposal, State.name, District.name, Village.name)
        .join(State, Proposal.state_id == State.id)
        .join(District, Proposal.district_id == District.id)
        .join(Village, Proposal.village_id == Village.id)
    )
    base = scope_proposals_to_user(base, user)

    if proposal_status is not None:
        base = base.filter(Proposal.status == proposal_status)
    if state_id is not None:
        base = base.filter(Proposal.state_id == state_id)
    if district_id is not None:
        base = base.filter(Proposal.district_id == district_id)
    if search:
        # ilike with a bound parameter — the wildcards are ours, the value
        # stays parameterised, so % or _ in the input cannot restructure the
        # query.
        pattern = f"%{search}%"
        base = base.filter(
            Proposal.proposal_number.ilike(pattern) | Proposal.title.ilike(pattern)
        )

    total = base.order_by(None).count()

    # Status counts for the pipeline strip, aggregated in the database.
    # Deliberately computed on the SCOPED query but WITHOUT the status
    # filter, so selecting one status does not zero out the other chips the
    # user is trying to switch between.
    counts_query = scope_proposals_to_user(db.query(Proposal.status, func.count(Proposal.id)), user)
    if state_id is not None:
        counts_query = counts_query.filter(Proposal.state_id == state_id)
    if district_id is not None:
        counts_query = counts_query.filter(Proposal.district_id == district_id)
    by_status = {
        row_status.value: count
        for row_status, count in counts_query.group_by(Proposal.status).all()
    }

    rows = (
        base.order_by(Proposal.status_changed_on.asc(), Proposal.id.asc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    today = date.today()

    return PaginatedProposals(
        items=[
            _list_item(proposal, state_name, district_name, village_name, today)
            for proposal, state_name, district_name, village_name in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
        by_status=by_status,
    )


@router.get("/{proposal_id}", response_model=ProposalDetail)
def get_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proposal = _visible_proposal_or_404(db, user, proposal_id)

    state = db.get(State, proposal.state_id)
    district = db.get(District, proposal.district_id)
    village = db.get(Village, proposal.village_id)

    review_rows = (
        db.query(ProposalReview, User.full_name)
        .outerjoin(User, ProposalReview.actor_user_id == User.id)
        .filter(ProposalReview.proposal_id == proposal.id)
        .order_by(ProposalReview.created_on.asc(), ProposalReview.id.asc())
        .all()
    )

    base = _list_item(
        proposal,
        state.name if state else "—",
        district.name if district else "—",
        village.name if village else "—",
        date.today(),
    )

    return ProposalDetail(
        **base.model_dump(),
        purpose=proposal.purpose,
        estimated_cost=proposal.estimated_cost,
        submitted_by_user_id=proposal.submitted_by_user_id,
        decided_by_user_id=proposal.decided_by_user_id,
        decided_on=proposal.decided_on,
        decision_note=proposal.decision_note,
        case_number=(proposal.case_id and _case_number(db, proposal.case_id)) or None,
        project_id=proposal.project_id,
        created_at=proposal.created_at,
        reviews=[
            ProposalReviewOut(
                id=review.id,
                from_status=review.from_status,
                to_status=review.to_status,
                actor_user_id=review.actor_user_id,
                actor_name=actor_name,
                actor_role=review.actor_role,
                note=review.note,
                created_on=review.created_on,
            )
            for review, actor_name in review_rows
        ],
        allowed_transitions=proposal_service.allowed_transitions(proposal.status, user.role),
    )


def _case_number(db: Session, case_id: int) -> str | None:
    from app.models import Case

    return db.query(Case.case_number).filter(Case.id == case_id).scalar()


@router.post("", response_model=ProposalDetail, status_code=status.HTTP_201_CREATED)
def create_proposal(
    payload: ProposalCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PROPOSAL_AUTHORS)),
):
    """Open a proposal, as a draft.

    Opens at DRAFT rather than SUBMITTED: submission is a deliberate act with
    a date attached that starts the state's clock, and creating a record
    should not silently start it.
    """
    village = db.get(Village, payload.village_id)
    if village is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown village_id")
    district = db.get(District, village.district_id)
    if district is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Village is not attached to a district",
        )

    # A requiring-body account submits for its own organisation, whatever the
    # payload says. Only an admin may file on behalf of another body, and
    # even then the value is recorded rather than inferred.
    if user.role is Role.REQUIRING_BODY:
        if not user.organisation:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This account has no requiring body set, so it cannot file a "
                    "proposal. An administrator must set one."
                ),
            )
        requiring_body = user.organisation
    else:
        if not payload.requiring_body:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="requiring_body is required when filing on behalf of a body",
            )
        requiring_body = payload.requiring_body

    state = db.get(State, district.state_id)
    today = date.today()

    proposal = Proposal(
        proposal_number=proposal_service.next_number(db, state, today),
        title=payload.title,
        purpose=payload.purpose,
        requiring_body=requiring_body,
        state_id=state.id,
        district_id=district.id,
        village_id=village.id,
        estimated_area_ha=payload.estimated_area_ha,
        estimated_families=payload.estimated_families,
        estimated_cost=payload.estimated_cost,
        status=ProposalStatus.DRAFT,
        created_at=today,
        status_changed_on=today,
    )
    db.add(proposal)
    db.flush()

    db.add(
        ProposalReview(
            proposal_id=proposal.id,
            from_status=None,
            to_status=ProposalStatus.DRAFT,
            actor_user_id=user.id,
            actor_role=user.role,
            note="Proposal opened",
            created_on=today,
        )
    )
    audit.record(
        db,
        user,
        action="proposal.create",
        entity_type="proposal",
        entity_id=proposal.id,
        detail=proposal.proposal_number,
    )
    db.commit()
    return get_proposal(proposal.id, db=db, user=user)


@router.patch("/{proposal_id}", response_model=ProposalDetail)
def update_proposal(
    proposal_id: int,
    payload: ProposalUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Edit a proposal while it is still with the requiring body.

    Refused once the file has moved on. A submission that could be edited
    after the state started reading it would make the scrutiny record
    meaningless — the reviewer would have approved something other than what
    is now on file.
    """
    proposal = _visible_proposal_or_404(db, user, proposal_id)

    if proposal.status not in (ProposalStatus.DRAFT, ProposalStatus.RETURNED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A proposal that is '{proposal.status.value}' cannot be edited. "
                f"It is with: {proposal_service.OWNER_BY_STATUS.get(proposal.status, 'another office')}"
            ),
        )
    if user.role is Role.REQUIRING_BODY and proposal.requiring_body != user.organisation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    if user.role not in PROPOSAL_AUTHORS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user.role.value}' may not edit a proposal",
        )

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    if "village_id" in fields and fields["village_id"] is not None:
        village = db.get(Village, fields["village_id"])
        if village is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown village_id"
            )
        district = db.get(District, village.district_id)
        # Moving the village moves the district and the state with it, so
        # all three stay consistent rather than the village silently
        # disagreeing with the district on the same row.
        proposal.district_id = district.id
        proposal.state_id = district.state_id

    for key, value in fields.items():
        setattr(proposal, key, value)

    audit.record(
        db,
        user,
        action="proposal.update",
        entity_type="proposal",
        entity_id=proposal.id,
        detail=", ".join(sorted(fields)),
    )
    db.commit()
    return get_proposal(proposal.id, db=db, user=user)


@router.post("/{proposal_id}/transition", response_model=ProposalDetail)
def transition_proposal(
    proposal_id: int,
    payload: ProposalTransition,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Move a proposal along the approval chain.

    On sanction this also creates the Case, in the same transaction — an
    approval that did not produce a case, or a case with no approval behind
    it, would both be worse than the operation failing outright.
    """
    proposal = _visible_proposal_or_404(db, user, proposal_id)

    proposal_service.transition(db, proposal, payload.to_status, user, note=payload.note)

    if payload.to_status is ProposalStatus.APPROVED:
        proposal_service.sanction_to_case(db, proposal, user)

    # Tell the submitter what happened. A decision the applicant has to
    # discover by refreshing a list is not a decision that has been
    # communicated.
    if payload.to_status in DECISION_STATUSES and proposal.submitted_by_user_id:
        severity = (
            AlertSeverity.HIGH
            if payload.to_status is ProposalStatus.REJECTED
            else AlertSeverity.MEDIUM
        )
        notify.notify_user(
            db,
            user_id=proposal.submitted_by_user_id,
            title=f"Proposal {payload.to_status.value}: {proposal.proposal_number}",
            body=(payload.note or f"Your proposal is now {payload.to_status.value}.")[:400],
            severity=severity,
            case_id=proposal.case_id,
            details={"proposal_id": proposal.id, "status": payload.to_status.value},
        )

    db.commit()
    return get_proposal(proposal.id, db=db, user=user)
