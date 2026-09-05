"""Managing your own account's authenticator app.

Distinct from the login-time redemption in app.routers.auth — these three
endpoints are Security-page actions on an already-authenticated session,
the same shape as biometrics enrollment: no role check, because setting
up a second factor is something every account does to itself.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db
from app.models import User
from app.schemas.common import Message
from app.schemas.mfa import MfaStatus, TotpConfirmRequest, TotpSetupResponse
from app.services import audit, totp

router = APIRouter(prefix="/mfa", tags=["mfa"])


@router.get("/status", response_model=MfaStatus)
def status_(user: User = Depends(get_current_user)):
    return MfaStatus(totp_enabled=user.totp_secret is not None)


@router.post("/totp/setup", response_model=TotpSetupResponse)
def setup_totp(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Mint a new secret and hold it as pending. Calling this again before
    confirming just replaces the pending secret — there is never more than
    one enrollment in flight, and nothing is active until it's confirmed."""
    secret = totp.generate_secret()
    user.totp_pending_secret = secret
    db.commit()

    uri = totp.provisioning_uri(secret, user.username)
    return TotpSetupResponse(secret=secret, otpauth_uri=uri, qr_code=totp.qr_data_uri(uri))


@router.post("/totp/confirm", response_model=Message)
def confirm_totp(
    payload: TotpConfirmRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.totp_pending_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="There's no authenticator setup in progress. Start again from Security.",
        )
    if not totp.verify(user.totp_pending_secret, payload.code):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That code wasn't right. Check the time on your device and try again.",
        )

    user.totp_secret = user.totp_pending_secret
    user.totp_pending_secret = None
    audit.record(db, user, action="mfa.totp_enabled", entity_type="user", entity_id=user.id)
    db.commit()
    return Message(detail="Authenticator app enabled.")


@router.post("/totp/disable", response_model=Message)
def disable_totp(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.totp_secret = None
    user.totp_pending_secret = None
    audit.record(db, user, action="mfa.totp_disabled", entity_type="user", entity_id=user.id)
    db.commit()
    return Message(detail="Authenticator app turned off.")
