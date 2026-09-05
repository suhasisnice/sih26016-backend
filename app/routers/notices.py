"""The public notice board.

Section 11 requires a preliminary notification to be published, and Section
19 requires the same of a declaration. Publication means public: this is the
one router in the API with no authentication, because a notice a citizen has
to log in to read has not been published in any sense the Act would
recognise.

Being unauthenticated, it is deliberately narrow. It derives from the same
cases table the rest of the API serves, but exposes only what a gazette
notice carries — what land, where, whose project, on what date. No officer
names, no compensation figures, no objections, no audit, and no route to any
of them. Nothing here is scoped to a district, because the public record is
not.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.enums import CompensationStatus, NoticeType, ObjectionStatus, ParcelStatus, Stage
from app.dependencies import get_db
from app.models import Case, Compensation, District, Objection, Parcel, Project, StatutoryNotice, Village

router = APIRouter(prefix="/notices", tags=["notices"])

# The two stages the Act requires be published. A case at any other stage is
# in progress, not on the record, and must not appear here.
PUBLISHED_STAGES = (Stage.PRELIMINARY_NOTIFICATION, Stage.DECLARATION)


class NoticeOut(BaseModel):
    case_number: str
    title: str
    stage: Stage
    published_on: date
    village_name: str
    district_name: str
    project_name: str
    requiring_body: str
    parcel_count: int
    total_area_ha: float


class NoticeList(BaseModel):
    items: list[NoticeOut]
    total: int


class NoticeLookupResult(BaseModel):
    """What a citizen searching their own survey number or ULPIN sees.

    Everything here is already on the public record once it applies: a
    stage the Act requires published, an award amount once the award notice
    itself has been issued (not merely once the case has reached that
    stage internally), a payment state without a rupee figure, and an
    objection tally without who filed it or what it said. No owner name, no
    phone number, no bank reference — see the module docstring.
    """

    found: bool
    survey_number: str | None = None
    case_number: str | None = None
    stage: Stage | None = None
    village_name: str | None = None
    district_name: str | None = None
    project_name: str | None = None
    preliminary_notification_on: date | None = None
    declaration_on: date | None = None
    award_declared: bool = False
    award_amount: int | None = None
    # "not_yet_declared" | "not_yet_paid" | "partially_paid" | "paid" — a
    # state, not an amount, so the individual award figure per beneficiary
    # stays off a page anyone can load without signing in.
    payment_status: str | None = None
    possession_taken: bool = False
    objection_count: int = 0
    objections_resolved: int = 0


@router.get("", response_model=NoticeList)
def list_notices(
    db: Session = Depends(get_db),
    stage: Stage | None = Query(default=None, description="Restrict to one published stage"),
    district_id: int | None = Query(default=None),
    limit: int = Query(default=100, le=200),
):
    stages = [stage] if stage in PUBLISHED_STAGES else list(PUBLISHED_STAGES)

    query = (
        db.query(
            Case,
            Village.name,
            District.name,
            Project.name,
            Project.requiring_body,
            func.count(Parcel.id),
            func.coalesce(func.sum(Parcel.area_ha), 0.0),
        )
        .join(Village, Case.village_id == Village.id)
        .join(District, Case.district_id == District.id)
        .join(Project, Case.project_id == Project.id)
        .outerjoin(Parcel, Parcel.case_id == Case.id)
        .filter(Case.stage.in_(stages))
    )

    if district_id is not None:
        query = query.filter(Case.district_id == district_id)

    rows = (
        query.group_by(Case.id, Village.name, District.name, Project.name, Project.requiring_body)
        .order_by(Case.stage_changed_at.desc(), Case.id.desc())
        .limit(limit)
        .all()
    )

    items = [
        NoticeOut(
            case_number=case.case_number,
            title=case.title,
            stage=case.stage,
            # The date the case entered the published stage is the date of
            # publication. created_at would be when the file was opened,
            # which is not what the sixty-day objection window runs from.
            published_on=case.stage_changed_at,
            village_name=village_name,
            district_name=district_name,
            project_name=project_name,
            requiring_body=requiring_body,
            parcel_count=parcel_count,
            total_area_ha=round(float(total_area), 4),
        )
        for case, village_name, district_name, project_name, requiring_body, parcel_count, total_area in rows
    ]

    return NoticeList(items=items, total=len(items))


@router.get("/lookup", response_model=NoticeLookupResult)
def lookup_notice(
    db: Session = Depends(get_db),
    survey_number: str | None = Query(default=None, max_length=20),
    ulpin: str | None = Query(default=None, max_length=14),
):
    """A citizen's own lookup: find a parcel by survey number or ULPIN, see
    what stage its acquisition has reached, its award and payment position,
    and how many objections on the case have been resolved.

    Unauthenticated, like the board above, and equally deliberate about
    what it withholds: this answers "where does MY land stand", not "who
    owns land near me" — a survey number has to be known already, this
    route does not enumerate them.
    """
    if not survey_number and not ulpin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a survey_number or a ulpin to look up",
        )

    query = db.query(Parcel, Case).join(Case, Parcel.case_id == Case.id)
    if ulpin:
        query = query.filter(Parcel.ulpin == ulpin.strip().upper())
    else:
        query = query.filter(Parcel.survey_number == survey_number.strip())

    row = query.first()
    if row is None:
        return NoticeLookupResult(found=False)
    parcel, case = row

    village = db.get(Village, case.village_id)
    district = db.get(District, case.district_id)
    project = db.get(Project, case.project_id)

    notices = {
        n.notice_type: n
        for n in db.query(StatutoryNotice).filter(StatutoryNotice.case_id == case.id).all()
    }
    award_notice = notices.get(NoticeType.AWARD)

    payment_status = None
    if award_notice:
        comps = db.query(Compensation).filter(Compensation.case_id == case.id).all()
        if not comps:
            payment_status = "not_yet_paid"
        elif all(c.status == CompensationStatus.PAID for c in comps):
            payment_status = "paid"
        elif any(c.amount_paid > 0 for c in comps):
            payment_status = "partially_paid"
        else:
            payment_status = "not_yet_paid"
    else:
        payment_status = "not_yet_declared"

    objections = db.query(Objection).filter(Objection.case_id == case.id).all()
    resolved = sum(
        1 for o in objections if o.status in (ObjectionStatus.RESOLVED, ObjectionStatus.REJECTED)
    )

    return NoticeLookupResult(
        found=True,
        survey_number=parcel.survey_number,
        case_number=case.case_number,
        stage=case.stage,
        village_name=village.name if village else None,
        district_name=district.name if district else None,
        project_name=project.name if project else None,
        preliminary_notification_on=(
            notices[NoticeType.PRELIMINARY_NOTIFICATION].issued_on
            if NoticeType.PRELIMINARY_NOTIFICATION in notices
            else None
        ),
        declaration_on=notices[NoticeType.DECLARATION].issued_on if NoticeType.DECLARATION in notices else None,
        award_declared=award_notice is not None,
        award_amount=award_notice.total_amount if award_notice else None,
        payment_status=payment_status,
        possession_taken=parcel.status == ParcelStatus.POSSESSION_TAKEN,
        objection_count=len(objections),
        objections_resolved=resolved,
    )


# --------------------------------------------------------------------------
# The authenticated side: issuing an instrument, and the register per case.
#
# The public list above stays exactly as narrow as it was. These routes are
# how a notice gets ONTO that list, and how "notifications issued" and
# "awards declared" become countable facts rather than inferences from a
# case's current stage.
# --------------------------------------------------------------------------

from fastapi import Depends, HTTPException, status  # noqa: E402

from app.core.enums import Role  # noqa: E402
from app.dependencies import (  # noqa: E402
    get_current_user,
    require_role,
    scope_cases_to_user,
)
from app.models import User  # noqa: E402
from app.schemas.notice import (  # noqa: E402
    StatutoryNoticeCreate,
    StatutoryNoticeList,
    StatutoryNoticeOut,
)
from app.services import audit  # noqa: E402

# Issuing a statutory instrument is an act with legal weight, so it is
# narrower than the general case-writer list: a field officer records
# findings, they do not publish notifications.
NOTICE_ISSUERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO)

# The stage a case must have reached before each instrument can be issued.
# An award published before the declaration stage is not an early award, it
# is a data-entry error, and the register is the wrong place to discover it.
MINIMUM_STAGE_FOR = {
    NoticeType.PRELIMINARY_NOTIFICATION: Stage.PRELIMINARY_NOTIFICATION,
    NoticeType.DECLARATION: Stage.DECLARATION,
    NoticeType.AWARD: Stage.AWARD,
    NoticeType.POSSESSION_NOTICE: Stage.POSSESSION,
}

DEFAULT_SECTION = {
    NoticeType.PRELIMINARY_NOTIFICATION: "Section 11(1)",
    NoticeType.DECLARATION: "Section 19(1)",
    NoticeType.AWARD: "Section 23",
    NoticeType.POSSESSION_NOTICE: "Section 38(1)",
}

STAGE_SEQUENCE = list(Stage)


@router.get("/register", response_model=StatutoryNoticeList)
def notice_register(
    case_id: int = Query(description="Case whose issued instruments to list"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every instrument issued on one case, in issue order.

    Authenticated and case-scoped, unlike the public board above: this is the
    internal register, and it carries the issuing officer and the gazette
    reference.
    """
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    rows = (
        db.query(StatutoryNotice)
        .filter(StatutoryNotice.case_id == case_id)
        .order_by(StatutoryNotice.issued_on.asc(), StatutoryNotice.id.asc())
        .all()
    )
    return StatutoryNoticeList(
        items=[
            StatutoryNoticeOut(**{
                **{c.name: getattr(row, c.name) for c in row.__table__.columns},
                "case_number": case.case_number,
            })
            for row in rows
        ],
        total=len(rows),
    )


@router.post("/register", response_model=StatutoryNoticeOut, status_code=status.HTTP_201_CREATED)
def issue_notice(
    payload: StatutoryNoticeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*NOTICE_ISSUERS)),
):
    """Record that an instrument has been published.

    Refuses to issue one twice for the same case: a second preliminary
    notification on the same acquisition is not a second notification, it is
    a duplicate, and it would inflate the national count by exactly as much
    as somebody double-clicks.
    """
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == payload.case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    minimum = MINIMUM_STAGE_FOR[payload.notice_type]
    if STAGE_SEQUENCE.index(case.stage) < STAGE_SEQUENCE.index(minimum):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot issue a {payload.notice_type.value} on a case at "
                f"'{case.stage.value}'. The case must have reached '{minimum.value}'."
            ),
        )

    existing = (
        db.query(StatutoryNotice.id)
        .filter(
            StatutoryNotice.case_id == payload.case_id,
            StatutoryNotice.notice_type == payload.notice_type,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {payload.notice_type.value} has already been issued on this case",
        )

    if payload.notice_type is not NoticeType.AWARD and (
        payload.beneficiary_count is not None or payload.total_amount is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="beneficiary_count and total_amount apply only to an award notice",
        )

    notice = StatutoryNotice(
        case_id=payload.case_id,
        notice_type=payload.notice_type,
        section_reference=payload.section_reference or DEFAULT_SECTION[payload.notice_type],
        gazette_number=payload.gazette_number,
        issuing_authority=payload.issuing_authority,
        issued_on=payload.issued_on or date.today(),
        document_id=payload.document_id,
        issued_by_user_id=user.id,
        beneficiary_count=payload.beneficiary_count,
        total_amount=payload.total_amount,
    )
    db.add(notice)
    db.flush()

    audit.record(
        db,
        user,
        action="notice.issue",
        entity_type="statutory_notice",
        entity_id=notice.id,
        detail=f"{payload.notice_type.value} on case {payload.case_id} ({notice.section_reference})",
    )
    db.commit()
    db.refresh(notice)

    return StatutoryNoticeOut(**{
        **{c.name: getattr(notice, c.name) for c in notice.__table__.columns},
        "case_number": case.case_number,
    })
