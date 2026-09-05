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

import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.enums import (
    CompensationStatus,
    NoticeType,
    NotificationChannel,
    ObjectionStatus,
    ParcelStatus,
    Role,
    Stage,
)
from app.core.security import hash_password
from app.dependencies import get_db
from app.models import (
    Case,
    Compensation,
    District,
    NotificationSubscription,
    Objection,
    Parcel,
    Person,
    Project,
    StatutoryNotice,
    User,
    Village,
)
from app.services import audit, credentials, landowner_notify, totp

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
    ulpin: str | None = None
    case_number: str | None = None
    stage: Stage | None = None
    village_name: str | None = None
    district_name: str | None = None
    project_name: str | None = None
    # The body the acquisition is for — Project.requiring_body. Naming who
    # to approach with a question is as much "on the public record" as the
    # rest of this response; it is the officer's own name and phone number
    # this route still withholds, not the office.
    requiring_authority: str | None = None
    area_ha: float | None = None
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


def _find_parcel_and_case(
    db: Session, survey_number: str | None, ulpin: str | None
) -> tuple[Parcel, Case] | None:
    """The one query every survey-number-or-ULPIN entry point in this router
    runs first — the public lookup below, and /subscribe and /provision
    further down. Every parcel in this schema already belongs to a case
    (see Parcel's docstring), so "found" and "under acquisition" are the
    same fact here: there is no registry of land that exists but isn't
    part of some acquisition for this route to distinguish from "no such
    parcel on file at all"."""
    query = db.query(Parcel, Case).join(Case, Parcel.case_id == Case.id)
    if ulpin:
        query = query.filter(Parcel.ulpin == ulpin.strip().upper())
    else:
        query = query.filter(Parcel.survey_number == survey_number.strip())
    return query.first()


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

    row = _find_parcel_and_case(db, survey_number, ulpin)
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
        ulpin=parcel.ulpin,
        case_number=case.case_number,
        stage=case.stage,
        village_name=village.name if village else None,
        district_name=district.name if district else None,
        project_name=project.name if project else None,
        requiring_authority=project.requiring_body if project else None,
        area_ha=parcel.area_ha,
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
# Subscribing to updates, and provisioning a landowner's account — both
# still unauthenticated, and both still reached from the same survey
# number / ULPIN a citizen already used to run the lookup above. Neither is
# proof of identity by itself (a survey number is often visible on a
# physical notice board), which is a real limitation of this prototype, not
# an oversight — see the module docstring's cross-reference in
# app.services.credentials for the account side of that trade-off.
# --------------------------------------------------------------------------

# Loose on purpose: this accepts whatever a person actually typed on a
# phone (spaces, a leading 0, a +91) rather than forcing one exact shape,
# and only rejects what could not possibly be dialled.
_PHONE_RE = re.compile(r"^\+?[0-9\s-]{10,15}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SubscribeRequest(BaseModel):
    survey_number: str | None = Field(default=None, max_length=20)
    ulpin: str | None = Field(default=None, max_length=14)
    whatsapp_number: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    # No default: silently treating a missing box as "consented" is exactly
    # the failure mode consent exists to prevent.
    consent: bool


class SubscribeResponse(BaseModel):
    id: int
    message: str
    # "sent" | "failed" | None (None = that channel wasn't chosen) — lets
    # the UI show WhatsApp ✓ / Email ✓ per channel rather than one bare
    # success, and tell a real send failure apart from "wasn't asked for".
    whatsapp_status: str | None = None
    email_status: str | None = None


@router.post("/subscribe", response_model=SubscribeResponse, status_code=status.HTTP_201_CREATED)
def subscribe(payload: SubscribeRequest, db: Session = Depends(get_db)):
    """"Get updates about this land" — save a subscription against the
    parcel a citizen just looked up, and immediately send them today's
    status on whichever channel(s) they chose, via notify_landowner.
    Independent of any account: this works whether or not the person ever
    provisions a login below."""
    if not payload.survey_number and not payload.ulpin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide a survey_number or a ulpin.")
    if not payload.consent:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Consent is required before we can send you updates about this land.",
        )
    if not payload.whatsapp_number and not payload.email:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Choose at least one of WhatsApp or email to be notified on.",
        )
    if payload.whatsapp_number and not _PHONE_RE.match(payload.whatsapp_number.strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That doesn't look like a valid mobile number.")
    if payload.email and not _EMAIL_RE.match(payload.email.strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That doesn't look like a valid email address.")

    row = _find_parcel_and_case(db, payload.survey_number, payload.ulpin)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Your land was not found in the available acquisition records.",
        )
    parcel, case = row

    whatsapp_number = payload.whatsapp_number.strip() if payload.whatsapp_number else None
    email = payload.email.strip().lower() if payload.email else None

    duplicate_query = db.query(NotificationSubscription).filter(
        NotificationSubscription.parcel_id == parcel.id
    )
    if whatsapp_number:
        existing_whatsapp = duplicate_query.filter(
            NotificationSubscription.whatsapp_number == whatsapp_number
        ).first()
        if existing_whatsapp:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "That number is already subscribed to updates for this land.",
            )
    if email:
        existing_email = duplicate_query.filter(NotificationSubscription.email == email).first()
        if existing_email:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "That email is already subscribed to updates for this land.",
            )

    subscription = NotificationSubscription(
        parcel_id=parcel.id,
        whatsapp_number=whatsapp_number,
        email=email,
        consent_given_at=datetime.now(timezone.utc),
    )
    db.add(subscription)
    db.flush()

    audit.record(
        db,
        None,
        action="notice.subscribe",
        entity_type="notification_subscription",
        entity_id=subscription.id,
        detail=f"parcel_id={parcel.id}",
    )
    db.commit()

    # Sent immediately, on today's actual status — not a bare "you're
    # subscribed" line — so the very first message this channel ever
    # carries is already the real notification template, not a placeholder
    # the person has to wait for a later event to see.
    project = db.get(Project, case.project_id)
    notification_type, status_label = landowner_notify.label_for_stage(case.stage.value)
    logs = landowner_notify.notify_landowner(db, parcel, project, notification_type, status_label)
    db.commit()

    whatsapp_status = next((log.status.value for log in logs if log.channel == NotificationChannel.WHATSAPP), None)
    email_status = next((log.status.value for log in logs if log.channel == NotificationChannel.EMAIL), None)

    return SubscribeResponse(
        id=subscription.id,
        message="You're subscribed to updates on this land.",
        whatsapp_status=whatsapp_status,
        email_status=email_status,
    )


class ProvisionRequest(BaseModel):
    survey_number: str | None = Field(default=None, max_length=20)
    ulpin: str | None = Field(default=None, max_length=14)


class ProvisionResponse(BaseModel):
    username: str
    # Shown exactly once, in this response, and never again — the database
    # only ever stores its bcrypt hash. See app.services.credentials.
    temporary_password: str
    # The 6-digit code /auth/login/verify accepts for an account with no
    # authenticator app enrolled yet (app.services.totp.FALLBACK_CODE).
    # Returned here rather than hardcoded in the frontend, so the frontend
    # never has to know or duplicate that constant.
    login_code_hint: str
    message: str


@router.post("/provision", response_model=ProvisionResponse, status_code=status.HTTP_201_CREATED)
def provision_landowner(payload: ProvisionRequest, db: Session = Depends(get_db)):
    """Create a landowner login for whoever owns the parcel just looked up.

    Auto-provisioned, not officer-approved: a survey number or ULPIN is
    enough to reach this endpoint, which is a real trust boundary this
    prototype accepts deliberately rather than papering over — anyone who
    can read a physical notice board could reach this for someone else's
    land. Production hardening (an OTP to a phone number already on file,
    an officer approval queue) is exactly the kind of thing this
    intentionally does not build — see the project's own written
    limitations for why.
    """
    if not payload.survey_number and not payload.ulpin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide a survey_number or a ulpin.")

    row = _find_parcel_and_case(db, payload.survey_number, payload.ulpin)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Your land was not found in the available acquisition records.",
        )
    parcel, case = row

    owner = db.get(Person, parcel.owner_id)
    if owner is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No landowner is on record for this parcel yet. Contact your district office.",
        )

    existing = (
        db.query(User)
        .filter(User.person_id == owner.id, User.role == Role.LANDOWNER)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Credentials have already been issued for this land record. "
            "Contact your district office if you need them reset.",
        )

    username = credentials.generate_username(db)
    temporary_password = credentials.generate_temporary_password()

    user = User(
        username=username,
        full_name=owner.name,
        password_hash=hash_password(temporary_password),
        role=Role.LANDOWNER,
        district_id=case.district_id,
        person_id=owner.id,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    db.flush()

    audit.record(
        db,
        None,
        action="landowner.provision",
        entity_type="user",
        entity_id=user.id,
        detail=f"parcel_id={parcel.id}",
    )
    db.commit()

    return ProvisionResponse(
        username=username,
        temporary_password=temporary_password,
        login_code_hint=totp.FALLBACK_CODE,
        message="Your BhoomiMitra login has been created. Sign in and set a new password.",
    )


# --------------------------------------------------------------------------
# The authenticated side: issuing an instrument, and the register per case.
#
# The public list above stays exactly as narrow as it was. These routes are
# how a notice gets ONTO that list, and how "notifications issued" and
# "awards declared" become countable facts rather than inferences from a
# case's current stage.
# --------------------------------------------------------------------------

from app.dependencies import (  # noqa: E402
    get_current_user,
    require_role,
    scope_cases_to_user,
)
from app.schemas.notice import (  # noqa: E402
    StatutoryNoticeCreate,
    StatutoryNoticeList,
    StatutoryNoticeOut,
)

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

    # Tell everyone already subscribed on this case's parcels — best-effort
    # per parcel (notify_landowner never raises), so a subscriber's bad
    # number cannot turn a legitimate notice-issuing request into a 500 for
    # the officer who just published it.
    notification_type, status_label = landowner_notify.label_for_notice_type(payload.notice_type.value)
    project = db.get(Project, case.project_id)
    parcels = db.query(Parcel).filter(Parcel.case_id == payload.case_id).all()
    for parcel in parcels:
        landowner_notify.notify_landowner(
            db, parcel, project, notification_type, status_label, notified_on=notice.issued_on
        )
    db.commit()

    return StatutoryNoticeOut(**{
        **{c.name: getattr(notice, c.name) for c in notice.__table__.columns},
        "case_number": case.case_number,
    })
