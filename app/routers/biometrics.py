"""Face enrollment and login.

Runs entirely on the backend. A frame comes in, an embedding comes out, it
is compared to the account's stored embedding, and the backend decides — no
other party's word is taken for anything.

Ends the same way every other login does: create_access_token and the same
audit trail password login writes to, so nothing downstream needs to know
or care which factor was used.
"""

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.enums import BiometricKind
from app.core.security import STEPUP_TOKEN_EXPIRE_MINUTES, create_access_token, create_stepup_token
from app.dependencies import get_current_user, get_db
from app.models import BiometricCredential, User
from app.schemas.auth import LoginResponse
from app.schemas.biometrics import (
    BiometricEnrollResponse,
    BiometricStatus,
    FaceEnrollRequest,
    FaceLoginRequest,
    FaceStepUpRequest,
    StepUpResponse,
)
from app.services import audit, face, ratelimit

router = APIRouter(prefix="/biometrics", tags=["biometrics"])


def _issue_login(db: Session, user: User, *, action: str) -> LoginResponse:
    from app.routers.auth import _user_out  # local import: avoids a cycle at module load

    audit.record(db, user, action=action, entity_type="user", entity_id=user.id)
    db.commit()
    return LoginResponse(
        access_token=create_access_token(user.id, user.role.value),
        user=_user_out(user),
        must_change_password=user.must_change_password,
    )


def _active_credential(db: Session, user_id: int, kind: BiometricKind) -> BiometricCredential | None:
    return (
        db.query(BiometricCredential)
        .filter(
            BiometricCredential.user_id == user_id,
            BiometricCredential.kind == kind,
            BiometricCredential.is_active.is_(True),
        )
        .first()
    )


def _enroll(db: Session, user: User, kind: BiometricKind, template: str, algorithm: str) -> BiometricCredential:
    existing = _active_credential(db, user.id, kind)
    if existing is not None:
        # Superseded, not deleted — same reasoning as everywhere else in
        # this codebase a "ledger, not a flag" table appears: the old
        # embedding stays as history, it just stops being the one anything
        # compares against.
        existing.is_active = False

    credential = BiometricCredential(
        user_id=user.id, kind=kind, template=template, algorithm=algorithm, is_active=True
    )
    db.add(credential)
    return credential


@router.get("/status", response_model=BiometricStatus)
def status_(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return BiometricStatus(
        face_enrolled=_active_credential(db, user.id, BiometricKind.FACE) is not None,
    )


# ---------------------------------------------------------------- face ----


@router.post("/face/enroll", response_model=BiometricEnrollResponse)
def enroll_face(
    payload: FaceEnrollRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        image_bytes = base64.b64decode(payload.image_base64, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That isn't valid base64 image data.")

    try:
        embedding = face.extract_embedding(image_bytes)
    except face.FaceCaptureError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    credential = _enroll(db, user, BiometricKind.FACE, face.serialise(embedding), face.ALGORITHM)
    audit.record(db, user, action="biometrics.face_enroll", entity_type="user", entity_id=user.id)
    db.commit()
    db.refresh(credential)
    return BiometricEnrollResponse(kind=BiometricKind.FACE, enrolled_at=credential.created_at)


@router.post("/face/login", response_model=LoginResponse)
def login_face(payload: FaceLoginRequest, request: Request, db: Session = Depends(get_db)):
    # Same limiter, same failure-counting, as password login — a face
    # login attempt that gets it wrong is exactly as attackable as a
    # password guess and costs the same to defend.
    wait = ratelimit.retry_after_seconds(request)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed sign-in attempts. Try again shortly.",
            headers={"Retry-After": str(wait)},
        )

    user = db.query(User).filter(User.username == payload.username).first()
    credential = _active_credential(db, user.id, BiometricKind.FACE) if user else None

    # Same message regardless of which of "no such user", "no face
    # enrolled" or "wrong face" is true — matching the password route's
    # refusal to let a response distinguish any of them.
    generic_failure = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Face not recognised. Try again or use another sign-in method."
    )

    if user is None or credential is None or not user.is_active:
        ratelimit.record_failure(request)
        raise generic_failure

    try:
        image_bytes = base64.b64decode(payload.image_base64, validate=True)
        attempt_embedding = face.extract_embedding(image_bytes)
    except (ValueError, binascii.Error, face.FaceCaptureError) as exc:
        # A capture problem (no face, bad image) is shown verbatim — it is
        # actionable ("move into the light") in a way "face not recognised"
        # is not, and reveals nothing about whether the account or
        # enrollment exist.
        ratelimit.record_failure(request)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    matched, dist = face.matches(face.deserialise(credential.template), attempt_embedding)
    if not matched:
        ratelimit.record_failure(request)
        raise generic_failure

    ratelimit.clear(request)
    return _issue_login(db, user, action="auth.login_face")


# ------------------------------------------------------------- step-up ----
#
# A fresh re-confirmation of an ALREADY signed-in officer's identity,
# before one specific high-impact action — never a login. See
# app.dependencies.verify_stepup for how the resulting token is checked at
# the action itself (POST /cases/{id}/hold, or advancing into a
# consequential stage). No rate limiter shared with login: an officer
# retrying their own already-authenticated capture a few times is not a
# credential-guessing surface the way an anonymous login attempt is.


@router.post("/face/stepup", response_model=StepUpResponse)
def face_stepup(
    payload: FaceStepUpRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    credential = _active_credential(db, user.id, BiometricKind.FACE)
    if credential is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No face is enrolled on this account. Enrol one from Security first.",
        )

    try:
        image_bytes = base64.b64decode(payload.image_base64, validate=True)
        attempt_embedding = face.extract_embedding(image_bytes)
    except (ValueError, binascii.Error, face.FaceCaptureError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    matched, _dist = face.matches(face.deserialise(credential.template), attempt_embedding)
    if not matched:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Face not recognised. Try again.")

    audit.record(db, user, action="stepup.face", entity_type="user", entity_id=user.id)
    db.commit()
    return StepUpResponse(
        stepup_token=create_stepup_token(user.id),
        expires_in_seconds=STEPUP_TOKEN_EXPIRE_MINUTES * 60,
    )
