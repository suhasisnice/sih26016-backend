from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.enums import Role
from app.core.security import decode_access_token
from app.database import SessionLocal
from app.models import Case, Parcel, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Roles that work within one district. An admin is not listed: admins are
# central and see everything.
DISTRICT_SCOPED_ROLES = (Role.DISTRICT_OFFICER, Role.SLAO, Role.FIELD_OFFICER, Role.RNR_OFFICER)


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
    each of the six dashboard queries. None lets those queries simply omit
    the filter.

    An empty list is NOT the same as None — it means "entitled to nothing"
    and must still filter everything out.
    """
    if user.role is Role.ADMIN:
        return None
    return [case_id for (case_id,) in scope_cases_to_user(db.query(Case.id), user).all()]


def scope_cases_to_user(query, user: User):
    """Narrow a Case query to what this user is entitled to see.

    Every list endpoint runs through this, so a forgotten filter in one
    route cannot leak another district's caseload. It fails closed: a role
    it does not recognise, or an officer with no district assigned, gets
    nothing rather than everything.
    """
    if user.role is Role.ADMIN:
        return query

    if user.role in DISTRICT_SCOPED_ROLES:
        if user.district_id is None:
            return query.filter(Case.id.is_(None))
        return query.filter(Case.district_id == user.district_id)

    if user.role is Role.LANDOWNER:
        if user.person_id is None:
            return query.filter(Case.id.is_(None))
        owned = query.session.query(Parcel.case_id).filter(Parcel.owner_id == user.person_id)
        return query.filter(Case.id.in_(owned))

    return query.filter(Case.id.is_(None))
