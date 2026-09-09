"""The actual Web Push (VAPID) send — shared by both MockMessagingProvider
and LiveMessagingProvider.

Every other channel here has a real mock/live split because the thing
being simulated is a vendor account: Twilio and an SMTP relay both have a
real "not configured, or configured but in a restricted trial state"
condition worth modeling separately from "actually sends". VAPID has
nothing equivalent — it is a keypair this deployment generated for itself
(see config.DEV_VAPID_PRIVATE_KEY), not a third-party credential with its
own approval process, so there is no meaningful "simulated" push distinct
from a real one. Both providers call this same function; NOTIFICATION_PROVIDER
still gates SMS and email exactly as before.
"""

import json
import logging

from pywebpush import WebPushException, webpush

from app.config import settings
from app.integrations.messaging.base import MessagingUnavailable

logger = logging.getLogger("bhoomimitra.messaging")


def send_push_notification(subscription_json: str, title: str, body: str) -> None:
    try:
        subscription_info = json.loads(subscription_json)
    except (TypeError, ValueError) as exc:
        raise MessagingUnavailable("Stored push subscription is not valid JSON.") from exc

    endpoint = subscription_info.get("endpoint", "")
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": f"mailto:{settings.vapid_claims_email}"},
        )
    except WebPushException as exc:
        # A 404/410 means the browser itself revoked this subscription
        # (tab closed for good, permission withdrawn, profile reset) — the
        # push service is telling us, not failing transiently. Reported the
        # same as any other MessagingUnavailable; app.services.landowner_notify
        # already logs every attempt, so a dead subscription shows up in
        # NotificationLog rather than silently vanishing.
        #
        # Logs the response body too, temporarily verbose while diagnosing
        # a real failure — push services (FCM/Mozilla/Apple) put the actual
        # reason there; the bare status code alone isn't enough to tell
        # "wrong VAPID key for this subscription" apart from "subscription
        # genuinely expired" apart from "malformed request".
        status = getattr(exc.response, "status_code", None)
        body_text = None
        if exc.response is not None:
            try:
                body_text = exc.response.text
            except Exception:
                body_text = None
        logger.warning(
            "[WEB PUSH] failed (status=%s) endpoint=%s body=%r: %s",
            status, endpoint[:60], body_text, exc,
        )
        raise MessagingUnavailable(str(exc)) from exc
