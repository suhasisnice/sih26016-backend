from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.enums import Role
from app.core.security import decode_access_token
from app.database import SessionLocal
from app.models import Case, District, Parcel, Proposal, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Roles that work within one district. An admin is not listed: admins are
# central and see everything.
DISTRICT_SCOPED_ROLES = (Role.DISTRICT_OFFICER, Role.SLAO, Role.FIELD_OFFICER, Role.RNR_OFFICER)

# Roles that work across one state. A state officer scrutinises proposals
# from every district in their state, so scoping them to a single district
# would make the tier meaningless.
STATE_SCOPED_ROLES = (Role.STATE_OFFICER,)

# Roles that read nationally but are not administrators. A ministry officer
# sanctions proposals and monitors progress across states; they do not
# operate a case, which is enforced by the route guards, not here.
NATIONAL_READ_ROLES = (Role.MINISTRY_OFFICER,)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None or "sub" not in payload:
        raise credentials_error

    user = db.get(User, int(payload["sub"]))
    # The user is re-read rather than trusted from the token's claims: a
    # deactivated account or a changed role has to take effect on the next
    # request, not whenever the token happens to expire.
    if user is None or not user.is_active:
        raise credentials_error
    return user


def require_role(*allowed: Role):
    """Route guard. Authorisation is enforced here, in the backend, never
    by hiding a button in the frontend."""

    def guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role.value}' may not perform this action",
            )
        return user

    return guard


def entitled_case_ids(db, user: User) -> list[int] | None:
    """The case ids this user may see, or None meaning "no restriction".

    None matters for scale: an unrestricted admin would otherwise get every
    case id pulled into Python and shipped back down as a huge IN clause on
    each of the dashboard queries. None lets those queries simply omit the
    filter.

    An empty list is NOT the same as None — it means "entitled to nothing"
    and must still filter everything out.
    """
    if user.role in (Role.ADMIN,) + NATIONAL_READ_ROLES:
        return None
    return [case_id for (case_id,) in scope_cases_to_user(db.query(Case.id), user).all()]


def scope_cases_to_user(query, user: User):
    """Narrow a Case query to what this user is entitled to see.

    Every list endpoint runs through this, so a forgotten filter in one
    route cannot leak another district's caseload. It fails closed: a role
    it does not recognise, or an officer with no district assigned, gets
    nothing rather than everything.
    """
    if user.role is Role.ADMIN or user.role in NATIONAL_READ_ROLES:
        return query

    if user.role in DISTRICT_SCOPED_ROLES:
        if user.district_id is None:
            return query.filter(Case.id.is_(None))
        return query.filter(Case.district_id == user.district_id)

    if user.role in STATE_SCOPED_ROLES:
        if user.state_id is None:
            return query.filter(Case.id.is_(None))
        # Every district in the officer's state. Expressed as a subquery so
        # the filter stays in SQL rather than loading a district list into
        # Python and rebuilding it as an IN clause on each call.
        districts_in_state = query.session.query(District.id).filter(
            District.state_id == user.state_id
        )
        return query.filter(Case.district_id.in_(districts_in_state))

    if user.role is Role.LANDOWNER:
        if user.person_id is None:
            return query.filter(Case.id.is_(None))
        owned = query.session.query(Parcel.case_id).filter(Parcel.owner_id == user.person_id)
        return query.filter(Case.id.in_(owned))

    if user.role is Role.REQUIRING_BODY:
        # A requiring body sees the cases its own sanctioned proposals became
        # — and nothing else. Not every case of the same organisation name,
        # because the organisation string is set by an administrator on the
        # account and is not an authorisation boundary on its own.
        if user.organisation is None:
            return query.filter(Case.id.is_(None))
        own_cases = query.session.query(Proposal.case_id).filter(
            Proposal.requiring_body == user.organisation,
            Proposal.case_id.isnot(None),
        )
        return query.filter(Case.id.in_(own_cases))

    return query.filter(Case.id.is_(None))


def scope_proposals_to_user(query, user: User):
    """Narrow a Proposal query to what this user may see.

    Deliberately a separate function from scope_cases_to_user rather than a
    parameter on it. The two answer different questions — a district officer
    sees every case in their district but only the proposals routed to their
    state — and folding them together is how one of them ends up silently
    wrong.

    Fails closed in exactly the same way.
    """
    if user.role in (Role.ADMIN,) + NATIONAL_READ_ROLES:
        return query

    if user.role is Role.REQUIRING_BODY:
        if user.organisation is None:
            return query.filter(Proposal.id.is_(None))
        return query.filter(Proposal.requiring_body == user.organisation)

    if user.role in STATE_SCOPED_ROLES:
        if user.state_id is None:
            return query.filter(Proposal.id.is_(None))
        return query.filter(Proposal.state_id == user.state_id)

    if user.role in DISTRICT_SCOPED_ROLES:
        if user.district_id is None:
            return query.filter(Proposal.id.is_(None))
        return query.filter(Proposal.district_id == user.district_id)

    # A landowner has no business in the proposal pipeline: a proposal names
    # a village, not a person, and there is nothing here they could act on.
    return query.filter(Proposal.id.is_(None))


def entitled_district_ids(db, user: User) -> list[int] | None:
    """Districts this user may see, or None for no restriction.

    Used by the reference and export routes, which are about places rather
    than cases and so cannot go through scope_cases_to_user.
    """
    if user.role in (Role.ADMIN,) + NATIONAL_READ_ROLES:
        return None
    if user.role in STATE_SCOPED_ROLES:
        if user.state_id is None:
            return []
        return [d for (d,) in db.query(District.id).filter(District.state_id == user.state_id)]
    if user.role in DISTRICT_SCOPED_ROLES:
        return [user.district_id] if user.district_id is not None else []
    # Landowners and requiring bodies get the districts their own cases are
    # in, derived rather than assumed.
    return [
        d
        for (d,) in scope_cases_to_user(db.query(Case.district_id), user).distinct()
        if d is not None
    ]
