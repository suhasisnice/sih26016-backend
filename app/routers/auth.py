import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.core.enums import BiometricKind, Role
from app.core.security import create_access_token, hash_password, verify_password
from app.dependencies import get_current_user, get_db
from app.models import BiometricCredential, User
from app.schemas import LoginResponse, UserOut
from app.schemas.invite import (
    InviteCheck,
    InviteCodePreview,
    RegisterRequest,
    RegisterResponse,
)
from app.services import audit, face, invites, ratelimit

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    """UserOut with the district name filled in from the relationship."""
    return UserOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        district_id=user.district_id,
        district_name=user.district.name if user.district else None,
        state_id=user.state_id,
        state_name=user.state.name if user.state else None,
        organisation=user.organisation,
    )


@router.post("/login", response_model=LoginResponse)
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # Checked before the password is verified: a limiter that only runs
    # after the bcrypt comparison still pays the bcrypt cost for every
    # guess, which is most of what makes a login endpoint worth attacking.
    wait = ratelimit.retry_after_seconds(request)
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Try again shortly.",
            headers={"Retry-After": str(wait)},
        )

    user = db.query(User).filter(User.username == form.username).first()

    # The same message for "no such user" and for "wrong password".
    # Distinguishing them would let anyone enumerate valid usernames.
    if user is None or not verify_password(form.password, user.password_hash):
        ratelimit.record_failure(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        # Counts as a failure: an attacker who finds a disabled account
        # should not get unlimited attempts against the rest.
        ratelimit.record_failure(request)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    ratelimit.clear(request)
    audit.record(db, user, action="auth.login", entity_type="user", entity_id=user.id)
    db.commit()

    return LoginResponse(
        access_token=create_access_token(user.id, user.role.value),
        user=_user_out(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _user_out(user)


@router.post("/invite/preview", response_model=InviteCodePreview)
def preview_invite(payload: InviteCheck, db: Session = Depends(get_db)):
    """What an invitation entitles the holder to, before they fill the form.

    Unauthenticated, because someone registering has no account yet. Safe:
    the caller has already proved they hold the code by presenting it, and
    the response tells them only what came with it. A wrong code returns
    valid=false and a reason, never a hint about which codes exist.
    """
    invite, reason = invites.verify(db, payload.invite_code)
    if invite is None:
        return InviteCodePreview(valid=False, reason=reason)

    return InviteCodePreview(
        valid=True,
        role=invite.role,
        district_name=invite.district.name if invite.district else None,
        state_name=invite.state.name if invite.state else None,
        organisation=invite.organisation,
        expires_at=invite.expires_at,
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Create an account against an invitation.

    The role and district come from the invitation, never from the request,
    so the code is the only thing that decides what the new account can do.
    """
    invite, reason = invites.verify(db, payload.invite_code)
    if invite is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)

    # Face sign-in is mandatory for every role except landowner — a
    # landowner account has no camera or scanner enrolled against it and
    # never will, the same line Login.jsx and Signup.jsx already draw.
    # Enforced here, not just on the form: a hidden or skipped field on the
    # client is a courtesy, not a control.
    if invite.role != Role.LANDOWNER and not payload.face_image_base64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Face capture is required to create this account.",
        )

    # Validated and extracted before anything is written, so a bad photo
    # fails the whole registration cleanly rather than leaving a user row
    # behind with no working sign-in method.
    face_embedding = None
    if payload.face_image_base64:
        try:
            image_bytes = base64.b64decode(payload.face_image_base64, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That isn't valid base64 image data.")
        try:
            face_embedding = face.extract_embedding(image_bytes)
        except face.FaceCaptureError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    username = payload.username.strip().lower()
    if db.query(User).filter(func.lower(User.username) == username).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username is already taken.",
        )

    user = User(
        username=username,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role=invite.role,
        district_id=invite.district_id,
        # The whole scope travels with the invitation, not just the
        # district. A state officer redeeming a code without state_id would
        # get an account that logs in and sees nothing, which reads as a
        # broken system rather than as a misconfigured invitation.
        state_id=invite.state_id,
        organisation=invite.organisation,
        is_active=True,
    )
    db.add(user)

    # Counted here, in the same transaction as the account, so a failure
    # cannot consume an invitation without creating the user it was for.
    invite.used_count += 1
    db.flush()

    if face_embedding is not None:
        db.add(
            BiometricCredential(
                user_id=user.id,
                kind=BiometricKind.FACE,
                template=face.serialise(face_embedding),
                algorithm=face.ALGORITHM,
                is_active=True,
            )
        )

    audit.record(
        db,
        user,
        action="auth.register",
        entity_type="user",
        entity_id=user.id,
        # The selector, not the code. It identifies which invitation was used
        # without writing anything secret into the audit trail.
        detail=f"role={user.role.value} invite_selector={invite.selector}",
    )
    db.commit()
    db.refresh(user)

    return RegisterResponse(
        access_token=create_access_token(user.id, user.role.value),
        user=_user_out(user),
    )
