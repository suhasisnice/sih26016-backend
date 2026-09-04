"""Running the alert rules on a clock, so "automated alerts" is true.

The rules and the fan-out were always here; what was missing was anything to
run them. Until now the only trigger was POST /admin/run-rules, which meant
"the system notices something is overdue and tells the responsible officer"
was really "somebody remembers to call an endpoint". A monitoring system that
has to be reminded to monitor is not one.

**Why in-process rather than a platform cron.** A cron service is the better
answer where the platform offers one, and `python -m app.services.scheduler`
exists precisely so a cron entry can call it. But scheduled jobs are not on
every hosting tier, and a deployment that silently has no scheduler is the
failure this module exists to prevent. An asyncio task costs nothing on an
idle API process and cannot be forgotten.

**Safe to run more than once.** Two instances, or a cron job overlapping the
in-process loop, do not corrupt anything: the alerts table is rebuilt from
scratch on every run by design, and the fan-out is guarded by a partial
unique index over unread notifications, so a second run in the same night
adds nothing rather than handing everyone a duplicate.

Off unless configured. RULES_INTERVAL_MINUTES=0 (the default) means no loop,
because a developer running the API locally against a seeded database does
not want rules quietly rewriting the alert table underneath them.
"""

import asyncio
import logging
from datetime import date

from app.config import settings
from app.database import SessionLocal
from app.services import alerts, notify

logger = logging.getLogger(__name__)

# How long to wait before the first run after boot. Not zero: a deploy
# restarts the process, and running a full rule sweep while the instance is
# still warming up competes with the requests it exists to serve.
STARTUP_DELAY_SECONDS = 60


def run_once(as_of: date | None = None) -> dict:
    """One sweep: recompute every alert, then deliver what is new.

    Owns its own session and transaction, because it is called from a
    background task and from the command line, neither of which has a request
    scope to borrow one from.
    """
    with SessionLocal() as session:
        summary = alerts.regenerate_alerts(session, as_of=as_of)
        session.flush()
        delivery = notify.fan_out(session)
        session.commit()

    summary["notifications_created"] = delivery["notifications_created"]
    summary["notification_recipients"] = delivery["recipients"]
    return summary


async def _loop(interval_seconds: int) -> None:
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            # to_thread because everything underneath is blocking SQLAlchemy.
            # Run inline it would stall the event loop for the length of a
            # full national rule sweep, which is exactly the kind of pause
            # that looks like an outage.
            summary = await asyncio.to_thread(run_once)
            logger.info(
                "rule sweep: %s alerts from %s cases, %s notifications to %s recipients",
                summary["alerts_generated"],
                summary["cases_evaluated"],
                summary["notifications_created"],
                summary["notification_recipients"],
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed sweep must not kill the loop
            logger.exception("rule sweep failed; will retry at the next interval")
        await asyncio.sleep(interval_seconds)


def start(app) -> asyncio.Task | None:
    """Begin the sweep loop, if this deployment asked for one."""
    minutes = settings.rules_interval_minutes
    if minutes <= 0:
        logger.info("rule scheduler disabled (RULES_INTERVAL_MINUTES=%s)", minutes)
        return None

    task = asyncio.create_task(_loop(minutes * 60), name="rule-sweep")
    logger.info("rule scheduler started, every %s minutes", minutes)
    return task


async def stop(task: asyncio.Task | None) -> None:
    """Cancel the loop and wait for it, so a reload does not leave a sweep
    half-committed against a database the next process is about to use."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    # The cron entrypoint: `python -m app.services.scheduler`.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run_once()
    for key, value in result.items():
        print(f"  {key}: {value}")
