from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import case as sql_case
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.enums import AlertSeverity, CaseStatus, Role, Stage
from app.dependencies import (
    get_current_user,
    get_db,
    require_role,
    require_stepup,
    scope_cases_to_user,
    verify_stepup,
)
from app.models import (
    AffectedFamily,
    AuditLog,
    Case,
    CaseStageHistory,
    District,
    Document,
    FundDeposit,
    Objection,
    Parcel,
    Project,
    Proposal,
    User,
    Village,
)
from app.schemas import (
    CaseCreate,
    CaseDetail,
    CaseHoldRequest,
    CaseListItem,
    CaseResumeRequest,
    CaseStageAdvance,
    CaseStageHistoryOut,
    CaseUpdate,
    PaginatedCases,
)
from app.schemas.audit import AuditEntryOut, AuditList
from app.schemas.fund_deposit import FundDepositCreate, FundDepositList, FundDepositOut
from app.services import audit, notify, numbering, sla, workflow

router = APIRouter(prefix="/cases", tags=["cases"])

# Roles allowed to create a case or move one along. A landowner is not one
# of them: they may see and object, not administer.
CASE_WRITERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO)

# The audit trail names which officers acted on a case — useful to other
# officers, not something a landowner needs to follow their own acquisition.
CASE_AUDIT_READERS = (
    Role.ADMIN,
    Role.DISTRICT_OFFICER,
    Role.SLAO,
    Role.FIELD_OFFICER,
    Role.RNR_OFFICER,
)

# The moments every officer-role workspace calls out as a "final" or
# "high-impact" decision — Declaration, Award, Possession, and the case's
# last legal stage. Earlier stages are procedural steps a case passes
# through on the way there and do not get the extra confirmation.
STEPUP_REQUIRED_STAGES = frozenset(
    {Stage.DECLARATION, Stage.AWARD, Stage.POSSESSION, workflow.TERMINAL_STAGE}
)


def _parcel_totals(db: Session, case_ids: list[int]) -> dict[int, tuple[int, float]]:
    """Parcel count and total hectares per case, in one grouped query
    rather than one query per row."""
    if not case_ids:
        return {}
    rows = (
        db.query(Parcel.case_id, func.count(Parcel.id), func.coalesce(func.sum(Parcel.area_ha), 0.0))
        .filter(Parcel.case_id.in_(case_ids))
        .group_by(Parcel.case_id)
        .all()
    )
    return {case_id: (count, round(float(area), 4)) for case_id, count, area in rows}


def _consent_progress(db: Session, case_id: int) -> tuple[int, int, float | None]:
    """Family count, consented count, and the percentage — computed live off
    affected_families every time, never stored (Law 1)."""
    total, given = (
        db.query(
            func.count(AffectedFamily.id),
            func.coalesce(
                func.sum(sql_case((AffectedFamily.consent_given.is_(True), 1), else_=0)), 0
            ),
        )
        .filter(AffectedFamily.case_id == case_id)
        .one()
    )
    total, given = int(total), int(given)
    pct = round(given / total * 100, 1) if total else None
    return total, given, pct


@router.get("", response_model=PaginatedCases)
def list_cases(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    stage: Stage | None = None,
    case_status: CaseStatus | None = None,
    district_id: int | None = None,
    project_id: int | None = None,
    search: str | None = Query(default=None, max_length=100),
    overdue_only: bool = Query(
        default=False, description="Only cases past their current stage's deadline"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Case table, filtered and paginated.

    Scoped to what this user may see before any of their own filters are
    applied, so narrowing can only ever shrink the result, never widen it
    past their entitlement.
    """
    query = (
        db.query(Case, District.name, Village.name, Project.name)
        .join(District, Case.district_id == District.id)
        .join(Village, Case.village_id == Village.id)
        .join(Project, Case.project_id == Project.id)
    )
    query = scope_cases_to_user(query, user)

    if stage is not None:
        query = query.filter(Case.stage == stage)
    if case_status is not None:
        query = query.filter(Case.status == case_status)
    if district_id is not None:
        query = query.filter(Case.district_id == district_id)
    if project_id is not None:
        query = query.filter(Case.project_id == project_id)
    if search:
        # ilike with a bound parameter — the wildcards are ours, the value
        # stays parameterised, so a % or _ in user input cannot alter the
        # query's structure.
        pattern = f"%{search}%"
        query = query.filter(Case.case_number.ilike(pattern) | Case.title.ilike(pattern))
    if overdue_only:
        # Filtered in SQL against the stored due date, so "show me what is
        # late" stays one indexed comparison rather than fetching every case
        # and discarding most of them.
        query = query.filter(
            Case.stage_due_on.isnot(None), Case.stage_due_on < date.today()
        )

    total = query.order_by(None).count()
    rows = query.order_by(Case.stage_changed_at.asc(), Case.id.asc()).limit(limit).offset(offset).all()

    totals = _parcel_totals(db, [row[0].id for row in rows])
    today = date.today()
    # Loaded once for the page rather than per row: the SLA table is nine
    # rows and every case on the page reads from it.
    sla_table = sla.load_sla(db)

    items = []
    for case, district_name, village_name, project_name in rows:
        parcel_count, total_area = totals.get(case.id, (0, 0.0))
        items.append(
            CaseListItem(
                id=case.id,
                case_number=case.case_number,
                title=case.title,
                stage=case.stage,
                status=case.status,
                district_id=case.district_id,
                district_name=district_name,
                village_name=village_name,
                project_name=project_name,
                stage_changed_at=case.stage_changed_at,
                days_in_stage=(today - case.stage_changed_at).days,
                parcel_count=parcel_count,
                total_area_ha=total_area,
                stage_due_on=case.stage_due_on,
                days_remaining=sla.days_remaining(case.stage_due_on, today),
                timeline_status=sla.timeline_status(
                    case.stage_due_on, case.stage, today, sla_table
                ),
            )
        )

    return PaginatedCases(items=items, total=total, limit=limit, offset=offset)


def _get_visible_case(db: Session, user: User, case_id: int) -> Case:
    """Fetch a case the user is entitled to see, or 404.

    Returns 404 rather than 403 for a case outside their scope: a 403 would
    confirm the case exists, which is itself information a landowner should
    not be able to fish for by trying ids.
    """
    query = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id)
    case = query.first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


@router.get("/{case_id}", response_model=CaseDetail)
def get_case(case_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    case = _get_visible_case(db, user, case_id)
    parcel_count, total_area = _parcel_totals(db, [case.id]).get(case.id, (0, 0.0))
    history = (
        db.query(CaseStageHistory)
        .filter(CaseStageHistory.case_id == case.id)
        .order_by(CaseStageHistory.changed_on.asc(), CaseStageHistory.id.asc())
        .all()
    )

    today = date.today()
    sla_table = sla.load_sla(db)
    sla_entry = sla_table.get(case.stage) or sla.DEFAULT_SLA[case.stage]

    # Provenance, looked up from the proposal side — proposals.case_id is
    # the single column that records this link, and it is indexed.
    origin = (
        db.query(Proposal.id, Proposal.proposal_number)
        .filter(Proposal.case_id == case.id)
        .first()
    )
    proposal_id, proposal_number = origin if origin else (None, None)

    consent_total, consent_given, consent_pct = _consent_progress(db, case.id)

    return CaseDetail(
        id=case.id,
        case_number=case.case_number,
        title=case.title,
        stage=case.stage,
        status=case.status,
        project_id=case.project_id,
        project_name=case.project.name,
        district_id=case.district_id,
        district_name=case.district.name,
        village_id=case.village_id,
        village_name=case.village.name,
        stage_changed_at=case.stage_changed_at,
        created_at=case.created_at,
        days_in_stage=(today - case.stage_changed_at).days,
        parcel_count=parcel_count,
        total_area_ha=total_area,
        allowed_next_stages=workflow.allowed_transitions(case.stage),
        stage_history=[CaseStageHistoryOut.model_validate(h) for h in history],
        stage_due_on=case.stage_due_on,
        days_remaining=sla.days_remaining(case.stage_due_on, today),
        timeline_status=sla.timeline_status(case.stage_due_on, case.stage, today, sla_table),
        standard_days=sla_entry["standard_days"],
        statutory_days=sla_entry["statutory_days"],
        sla_basis=sla_entry["basis"],
        proposal_id=proposal_id,
        proposal_number=proposal_number,
        consent_threshold_pct=case.consent_threshold_pct,
        consent_family_count=consent_total,
        consent_given_count=consent_given,
        consent_obtained_pct=consent_pct,
    )


@router.post("", response_model=CaseDetail, status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CaseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_WRITERS)),
):
    village = db.get(Village, payload.village_id)
    if village is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown village_id")
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown project_id")

    # District comes from the village, never from the client.
    district_id = village.district_id
    if project.district_id != district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project and village belong to different districts",
        )
    if user.role is not Role.ADMIN and user.district_id is not None and user.district_id != district_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create a case outside your district",
        )

    district = db.get(District, district_id)
    today = date.today()
    case_number = numbering.next_case_number(db, district, today.year)

    case = Case(
        case_number=case_number,
        title=payload.title,
        project_id=payload.project_id,
        district_id=district_id,
        village_id=payload.village_id,
        stage=Stage.PRELIMINARY_NOTIFICATION,
        status=CaseStatus.ACTIVE,
        stage_changed_at=today,
        created_at=today,
        consent_threshold_pct=payload.consent_threshold_pct,
    )
    # A case gets a deadline the moment it opens, not on its first stage
    # change — otherwise every brand-new case reads as "untracked" on the
    # adherence tile, which is the tile most likely to be looked at.
    sla.apply_due_date(db, case)
    db.add(case)
    db.flush()

    db.add(
        CaseStageHistory(
            case_id=case.id,
            from_stage=None,
            to_stage=Stage.PRELIMINARY_NOTIFICATION,
            changed_by_user_id=user.id,
            changed_on=today,
            note="Case opened",
        )
    )
    audit.record(
        db, user, action="case.create", entity_type="case", entity_id=case.id, detail=case_number
    )
    db.commit()

    return get_case(case.id, db=db, user=user)


@router.get("/{case_id}/audit", response_model=AuditList)
def case_audit_trail(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_AUDIT_READERS)),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Who did what to this case, and when.

    Officers and admins only. The trail names the officers who acted on a
    case, which is not something a landowner needs in order to follow their
    own acquisition.

    Covers the case itself plus the documents and objections attached to
    it, so the page reads as one history rather than three.
    """
    _get_visible_case(db, user, case_id)

    document_ids = [d for (d,) in db.query(Document.id).filter(Document.case_id == case_id).all()]
    objection_ids = [o for (o,) in db.query(Objection.id).filter(Objection.case_id == case_id).all()]

    conditions = [(AuditLog.entity_type == "case") & (AuditLog.entity_id == case_id)]
    if document_ids:
        conditions.append(
            (AuditLog.entity_type == "document") & (AuditLog.entity_id.in_(document_ids))
        )
    if objection_ids:
        conditions.append(
            (AuditLog.entity_type == "objection") & (AuditLog.entity_id.in_(objection_ids))
        )

    query = (
        db.query(AuditLog, User.full_name)
        .outerjoin(User, AuditLog.user_id == User.id)
        .filter(or_(*conditions))
    )
    total = query.count()
    rows = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).all()

    return AuditList(
        items=[
            AuditEntryOut(
                id=entry.id,
                user_id=entry.user_id,
                user_name=user_name,
                action=entry.action,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                detail=entry.detail,
                created_at=entry.created_at,
            )
            for entry, user_name in rows
        ],
        total=total,
    )


@router.post("/{case_id}/advance", response_model=CaseDetail)
def advance_stage(
    case_id: int,
    payload: CaseStageAdvance,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_WRITERS)),
    x_stepup_token: str | None = Header(default=None),
):
    """Move a case to the next (or previous) legal stage.

    workflow.advance_case refuses anything the Act does not allow and
    writes both the stage history and the audit entry. Two extra checks
    live here rather than in workflow.advance_case, because both depend on
    *which* transition this is, not just that it is a legal one:

    - Moving backward is "send back for review" in every officer
      workspace's own language, and a send-back with no reason attached is
      not accountable to anyone reading the case history later.
    - Moving into a stage in STEPUP_REQUIRED_STAGES needs a fresh biometric
      re-confirmation, checked via the same X-Stepup-Token every other
      high-impact action checks — this can't be a plain FastAPI
      Depends(require_stepup) because whether it's required at all depends
      on payload.to_stage, which a dependency resolved before the body is
      parsed cannot see.
    """
    case = _get_visible_case(db, user, case_id)

    is_send_back = workflow.STAGE_ORDER.index(payload.to_stage) < workflow.STAGE_ORDER.index(case.stage)
    if is_send_back and not (payload.note and payload.note.strip()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sending a case back a stage requires a remark explaining why.",
        )

    if payload.to_stage in STEPUP_REQUIRED_STAGES:
        verify_stepup(x_stepup_token, user)

    workflow.advance_case(db, case, payload.to_stage, user, note=payload.note)

    # Informational, not urgent — a stage moving forward is the case working
    # as intended, not a problem the landowner needs to act on. LOW is the
    # honest severity for "this happened", the same distinction the rule
    # engine already draws between a finding and a fact.
    stage_label = case.stage.value.replace("_", " ").title()
    notify.notify_case_landowners(
        db,
        case,
        title="Your case has moved to a new stage",
        body=f"{case.case_number} is now at {stage_label}.",
        severity=AlertSeverity.LOW,
    )

    db.commit()
    return get_case(case.id, db=db, user=user)


@router.patch("/{case_id}", response_model=CaseDetail)
def update_case(
    case_id: int,
    payload: CaseUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_WRITERS)),
):
    """Edit a case's title or status. The stage is not editable here.

    Moving a case is POST /{case_id}/advance, which checks the transition
    against the Act and writes the stage history. A PATCH that also set the
    stage would be a second, unvalidated route to the same change and would
    leave the timeline with unexplained jumps.
    """
    case = _get_visible_case(db, user, case_id)

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    changed = {k: v for k, v in fields.items() if getattr(case, k) != v}
    if not changed:
        return get_case(case.id, db=db, user=user)

    for key, value in changed.items():
        setattr(case, key, value)

    audit.record(
        db,
        user,
        action="case.update",
        entity_type="case",
        entity_id=case.id,
        detail=", ".join(
            f"{k}={v.value if hasattr(v, 'value') else v}" for k, v in changed.items()
        ),
    )
    db.commit()
    return get_case(case.id, db=db, user=user)


@router.post("/{case_id}/hold", response_model=CaseDetail)
def hold_case(
    case_id: int,
    payload: CaseHoldRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_WRITERS)),
    _stepup: None = Depends(require_stepup),
):
    """Halt a case that cannot proceed as submitted.

    Sets CaseStatus.STALLED with a mandatory reason — see
    CaseHoldRequest's docstring for why this is a status change, not a
    stage the Act does not have. Always requires step-up: putting a case on
    hold is exactly the kind of high-impact call every officer workspace
    wants a fresh identity check on.
    """
    case = _get_visible_case(db, user, case_id)
    if case.status == CaseStatus.CLOSED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A closed case cannot be put on hold.")

    case.status = CaseStatus.STALLED
    audit.record(
        db, user, action="case.hold", entity_type="case", entity_id=case.id, detail=payload.note
    )
    db.commit()
    return get_case(case.id, db=db, user=user)


@router.post("/{case_id}/resume", response_model=CaseDetail)
def resume_case(
    case_id: int,
    payload: CaseResumeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_WRITERS)),
):
    """Reverse a hold — a case that was stalled for a reason that has now
    been addressed goes back to active, not to whatever stage it happened
    to be sitting at, which never changed."""
    case = _get_visible_case(db, user, case_id)
    if case.status != CaseStatus.STALLED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a held case can be resumed.")

    case.status = CaseStatus.ACTIVE
    audit.record(
        db, user, action="case.resume", entity_type="case", entity_id=case.id, detail=payload.note
    )
    db.commit()
    return get_case(case.id, db=db, user=user)


@router.get("/{case_id}/fund-deposits", response_model=FundDepositList)
def list_fund_deposits(
    case_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The deposit ledger for one case — the gap this closes: nothing
    previously distinguished a case where the requiring body has put up
    no money at all from one that is merely waiting on disbursement."""
    _get_visible_case(db, user, case_id)
    rows = (
        db.query(FundDeposit)
        .filter(FundDeposit.case_id == case_id)
        .order_by(FundDeposit.deposited_on.asc(), FundDeposit.id.asc())
        .all()
    )
    return FundDepositList(
        items=[FundDepositOut.model_validate(row) for row in rows],
        total=len(rows),
        total_deposited=sum(row.amount for row in rows),
    )


@router.post(
    "/{case_id}/fund-deposits", response_model=FundDepositOut, status_code=status.HTTP_201_CREATED
)
def record_fund_deposit(
    case_id: int,
    payload: FundDepositCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*CASE_WRITERS)),
):
    """Record that the requiring body's money has landed. A ledger entry,
    not a single flag — a part payment topped up later is normal, and the
    fund_deposit_missing alert only needs at least one row to exist."""
    _get_visible_case(db, user, case_id)

    deposit = FundDeposit(
        case_id=case_id,
        amount=payload.amount,
        deposited_on=payload.deposited_on,
        reference=payload.reference,
        recorded_by_user_id=user.id,
    )
    db.add(deposit)
    db.flush()

    audit.record(
        db,
        user,
        action="fund_deposit.create",
        entity_type="fund_deposit",
        entity_id=deposit.id,
        detail=f"case={case_id} amount={payload.amount} ref={payload.reference or '—'}",
    )
    db.commit()
    db.refresh(deposit)
    return FundDepositOut.model_validate(deposit)
