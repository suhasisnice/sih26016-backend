"""Face and kiosk-fingerprint enrollment and login.

Two very different trust models share this file:

- Face runs entirely on the backend. A frame comes in, an embedding comes
  out, it is compared to the account's stored embedding, and the backend
  decides — no other party's word is taken for anything.
- Fingerprint cannot work that way (see app/services/kiosk_auth.py and
  mantra-agent/README.md for why): the actual capture-and-match happens on
  a kiosk PC this server can never reach, so what lands here is a
  kiosk-authenticated *report* of a match, checked against a numeric score
  threshold rather than trusted as a bare yes.

Both end the same way every other login does: create_access_token and the
same audit trail password login writes to, so nothing downstream needs to
know or care which factor was used.
"""

import base64
import binascii
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.enums import BiometricKind
from app.core.security import STEPUP_TOKEN_EXPIRE_MINUTES, create_access_token, create_stepup_token
from app.dependencies import get_current_user, get_db
from app.models import BiometricCredential, FingerprintChallenge, KioskAgent, StepUpChallenge, User
from app.schemas.auth import LoginResponse
from app.schemas.biometrics import (
    BiometricEnrollResponse,
    BiometricStatus,
    FaceEnrollRequest,
    FaceLoginRequest,
    FaceStepUpRequest,
    FingerprintChallengeRequest,
    FingerprintChallengeResponse,
    FingerprintEnrollRequest,
    FingerprintLoginRequest,
    FingerprintStepUpReportRequest,
    FingerprintStepUpStartResponse,
    StepUpResponse,
)
from app.services import audit, face, kiosk_auth, ratelimit

router = APIRouter(prefix="/biometrics", tags=["biometrics"])

# Mantra's own documented guidance for MFS100MatchISO: 0-100000, >=14000
# considered a match. Enforced here rather than trusted from the agent —
# see FingerprintLoginRequest's docstring.
MIN_FINGERPRINT_MATCH_SCORE = 14_000

# A kiosk has this long between fetching a template and reporting a result.
# Generous next to Mantra's own ~10s capture timeout, short enough that a
# fetched template cannot be sat on and reused well after the officer has
# walked away from the scanner.
CHALLENGE_TTL_SECONDS = 45


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


def get_kiosk_agent(
    db: Session = Depends(get_db),
    x_kiosk_key: str | None = Header(default=None),
) -> KioskAgent:
    kiosk = kiosk_auth.authenticate(db, x_kiosk_key)
    if kiosk is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing kiosk key",
        )
    return kiosk


@router.get("/status", response_model=BiometricStatus)
def status_(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return BiometricStatus(
        face_enrolled=_active_credential(db, user.id, BiometricKind.FACE) is not None,
        fingerprint_enrolled=_active_credential(db, user.id, BiometricKind.FINGERPRINT) is not None,
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


# --------------------------------------------------------- fingerprint ----


@router.post("/fingerprint/enroll", response_model=BiometricEnrollResponse)
def enroll_fingerprint(
    payload: FingerprintEnrollRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stores whatever base64 template the browser forwards from a local
    kiosk agent's capture. This endpoint only ever runs against an
    authenticated session — the officer is already logged in (by
    password), sitting at the kiosk, enrolling the finger they'll use to
    log in next time."""
    credential = _enroll(
        db, user, BiometricKind.FINGERPRINT, payload.template_base64, "mfs100_ansi378"
    )
    audit.record(
        db, user, action="biometrics.fingerprint_enroll", entity_type="user", entity_id=user.id
    )
    db.commit()
    db.refresh(credential)
    return BiometricEnrollResponse(kind=BiometricKind.FINGERPRINT, enrolled_at=credential.created_at)


@router.post("/fingerprint/challenge", response_model=FingerprintChallengeResponse)
def fingerprint_challenge(
    payload: FingerprintChallengeRequest,
    request: Request,
    kiosk: KioskAgent = Depends(get_kiosk_agent),
    db: Session = Depends(get_db),
):
    wait = ratelimit.retry_after_seconds(request)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many attempts from this kiosk. Try again shortly.",
            headers={"Retry-After": str(wait)},
        )

    user = db.query(User).filter(User.username == payload.username).first()
    credential = _active_credential(db, user.id, BiometricKind.FINGERPRINT) if user else None

    if user is None or credential is None or not user.is_active:
        ratelimit.record_failure(request)
        # Deliberately the same 404 either way: a kiosk probing usernames
        # cannot tell "no such account" from "no fingerprint enrolled".
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No fingerprint enrolled for that username."
        )

    challenge = FingerprintChallenge(
        kiosk_agent_id=kiosk.id,
        user_id=user.id,
        nonce=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=CHALLENGE_TTL_SECONDS),
    )
    db.add(challenge)
    db.commit()

    return FingerprintChallengeResponse(
        challenge_nonce=challenge.nonce,
        template_base64=credential.template,
        expires_in_seconds=CHALLENGE_TTL_SECONDS,
    )


@router.post("/fingerprint/login", response_model=LoginResponse)
def fingerprint_login(
    payload: FingerprintLoginRequest,
    request: Request,
    kiosk: KioskAgent = Depends(get_kiosk_agent),
    db: Session = Depends(get_db),
):
    challenge = (
        db.query(FingerprintChallenge)
        .filter(
            FingerprintChallenge.nonce == payload.challenge_nonce,
            FingerprintChallenge.kiosk_agent_id == kiosk.id,
        )
        .first()
    )

    generic_failure = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Fingerprint not recognised. Try again or use another sign-in method."
    )

    if challenge is None:
        ratelimit.record_failure(request)
        raise generic_failure
    if challenge.consumed_at is not None:
        # A nonce is spent the instant it is used, successfully or not —
        # otherwise a network retry of a genuine match could mint two
        # sessions from one capture.
        raise generic_failure
    # Consumed here, before the score is even checked, and committed
    # immediately rather than left pending on the session — every branch
    # below raises before reaching the commit at the bottom of this
    # function, and an uncommitted change is rolled back when get_db()
    # closes the session on the way out. Without this commit, a failed
    # attempt would leave the nonce looking un-consumed and replayable.
    challenge.consumed_at = datetime.now(timezone.utc)
    db.commit()

    if datetime.now(timezone.utc) > challenge.expires_at:
        ratelimit.record_failure(request)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "That fingerprint attempt timed out. Try again."
        )

    if payload.score < MIN_FINGERPRINT_MATCH_SCORE:
        ratelimit.record_failure(request)
        raise generic_failure

    user = db.get(User, challenge.user_id)
    if user is None or not user.is_active:
        ratelimit.record_failure(request)
        raise generic_failure

    ratelimit.clear(request)
    return _issue_login(db, user, action="auth.login_fingerprint")


# ------------------------------------------------------------- step-up ----
#
# A fresh re-confirmation of an ALREADY signed-in officer's identity,
# before one specific high-impact action — never a login. See
# app.dependencies.verify_stepup for how the resulting token is checked at
# the action itself (POST /cases/{id}/hold, or advancing into a
# consequential stage). No rate limiter shared with login: an officer
# retrying their own already-authenticated capture a few times is not a
# credential-guessing surface the way an anonymous login attempt is.

STEPUP_CHALLENGE_TTL_SECONDS = 60


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


@router.post("/fingerprint/stepup/start", response_model=FingerprintStepUpStartResponse)
def fingerprint_stepup_start(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Handed straight to the browser, not fetched by a kiosk — unlike the
    login challenge above, this caller already knows exactly who is
    asking."""
    credential = _active_credential(db, user.id, BiometricKind.FINGERPRINT)
    if credential is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No fingerprint is enrolled on this account. Enrol one from Security first.",
        )

    challenge = StepUpChallenge(
        user_id=user.id,
        nonce=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=STEPUP_CHALLENGE_TTL_SECONDS),
    )
    db.add(challenge)
    db.commit()

    return FingerprintStepUpStartResponse(
        nonce=challenge.nonce,
        template_base64=credential.template,
        expires_in_seconds=STEPUP_CHALLENGE_TTL_SECONDS,
    )


@router.post("/fingerprint/stepup/report", response_model=StepUpResponse)
def fingerprint_stepup_report(
    payload: FingerprintStepUpReportRequest,
    kiosk: KioskAgent = Depends(get_kiosk_agent),
    db: Session = Depends(get_db),
):
    challenge = (
        db.query(StepUpChallenge).filter(StepUpChallenge.nonce == payload.nonce).first()
    )
    generic_failure = HTTPException(status.HTTP_401_UNAUTHORIZED, "Fingerprint not recognised.")

    if challenge is None or challenge.consumed_at is not None:
        raise generic_failure
    # Consumed before the score is even checked, same replay-prevention
    # reasoning as the login challenge above.
    challenge.consumed_at = datetime.now(timezone.utc)
    challenge.kiosk_agent_id = kiosk.id
    db.commit()

    if datetime.now(timezone.utc) > challenge.expires_at:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That confirmation timed out. Try again.")
    if payload.score < MIN_FINGERPRINT_MATCH_SCORE:
        raise generic_failure

    user = db.get(User, challenge.user_id)
    if user is None or not user.is_active:
        raise generic_failure

    audit.record(db, user, action="stepup.fingerprint", entity_type="user", entity_id=user.id)
    db.commit()
    return StepUpResponse(
        stepup_token=create_stepup_token(user.id),
        expires_in_seconds=STEPUP_TOKEN_EXPIRE_MINUTES * 60,
    )
