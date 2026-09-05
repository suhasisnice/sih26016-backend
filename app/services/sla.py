"""Stage deadlines: when a case's current stage is due, and how it is tracking.

The problem statement asks for "timeline monitoring and milestone tracking"
and names "timeline adherence" as a dashboard tile. Neither is answerable
without somewhere to record how long a stage is *supposed* to take, which is
what stage_sla holds and what this module reads.

Two decisions worth stating:

- The due date is written onto the case (`stage_due_on`) rather than computed
  on every read. Timeline adherence is a dashboard-wide aggregate over
  thousands of cases, and deriving it per row would mean joining stage_sla
  into every KPI query. This module is the only writer, and it always writes
  the due date in the same transaction as the stage change, so the column
  cannot drift from the stage it describes.

- The *status* (on time / at risk / breached) is never stored. It is a
  function of today's date, so a stored copy is wrong the next morning.

The day counts default to the Act's own limits where it sets them and to
administrative practice where it does not. They live in the database rather
than in code so a state can tune them without a deployment — the same reason
required_documents is a table.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.enums import Stage, TimelineStatus
from app.models import Case, StageSla

# Seeded into stage_sla, and the fallback if a stage has no row yet.
#
# statutory_days is the limit in the RFCTLARR Act 2013 where one exists:
# s.19(2) requires the declaration within 12 months of the SIA report, and
# s.25 requires the award within 12 months of the declaration. The rest are
# administrative targets, which is why standard_days is shorter throughout —
# an office that only ever meets the statutory maximum is not performing
# well, it is performing legally.
DEFAULT_SLA: dict[Stage, dict] = {
    Stage.PRELIMINARY_NOTIFICATION: {
        "standard_days": 30,
        "statutory_days": None,
        "warn_at_fraction": 0.8,
        "basis": "Administrative target for publication and record preparation",
    },
    Stage.SOCIAL_IMPACT_ASSESSMENT: {
        "standard_days": 180,
        "statutory_days": 180,
        "warn_at_fraction": 0.85,
        "basis": "s.4(2) — SIA to be completed within six months",
    },
    Stage.LAND_VERIFICATION: {
        "standard_days": 60,
        "statutory_days": None,
        "warn_at_fraction": 0.8,
        "basis": "Administrative target for survey and record verification",
    },
    Stage.OBJECTION_PERIOD: {
        "standard_days": 60,
        "statutory_days": 60,
        "warn_at_fraction": 0.75,
        "basis": "s.15 — objections within 60 days of the notification",
    },
    Stage.DECLARATION: {
        "standard_days": 90,
        "statutory_days": 365,
        "warn_at_fraction": 0.8,
        "basis": "s.19(2) — declaration within 12 months of the SIA report",
    },
    Stage.AWARD: {
        "standard_days": 180,
        "statutory_days": 365,
        "warn_at_fraction": 0.8,
        "basis": "s.25 — award within 12 months of the declaration",
    },
    Stage.REHABILITATION_RESETTLEMENT: {
        "standard_days": 180,
        "statutory_days": None,
        "warn_at_fraction": 0.8,
        "basis": "Second Schedule — entitlements to be delivered before displacement",
    },
    Stage.POSSESSION: {
        "standard_days": 90,
        "statutory_days": None,
        "warn_at_fraction": 0.8,
        "basis": "s.38 — possession after compensation and R&R are settled",
    },
    Stage.MONITORING: {
        "standard_days": 365,
        "statutory_days": None,
        "warn_at_fraction": 0.9,
        "basis": "Ongoing post-possession monitoring cycle",
    },
}


def load_sla(db: Session) -> dict[Stage, dict]:
    """Every stage's SLA, falling back to DEFAULT_SLA for any stage with no
    row yet.

    Falling back rather than raising matters: a database seeded before
    stage_sla existed would otherwise turn every timeline query into a 500,
    and a missing tuning row is not a reason to take the dashboard down.
    """
    loaded = dict(DEFAULT_SLA)
    for row in db.query(StageSla).all():
        loaded[row.stage] = {
            "standard_days": row.standard_days,
            "statutory_days": row.statutory_days,
            "warn_at_fraction": row.warn_at_fraction,
            "basis": row.basis,
        }
    return loaded


def due_date_for(stage: Stage, stage_started_on: date, sla: dict[Stage, dict] | None = None) -> date:
    """When a case that entered `stage` on `stage_started_on` is due to leave it."""
    table = sla or DEFAULT_SLA
    entry = table.get(stage) or DEFAULT_SLA[stage]
    return stage_started_on + timedelta(days=entry["standard_days"])


def apply_due_date(db: Session, case: Case, sla: dict[Stage, dict] | None = None) -> Case:
    """Set `case.stage_due_on` from the case's current stage and start date.

    Called by the workflow service on every stage change, and by the seed.
    Does not commit — the caller owns the transaction, so the due date and
    the stage change it describes land together or not at all.
    """
    case.stage_due_on = due_date_for(case.stage, case.stage_changed_at, sla or load_sla(db))
    return case


def timeline_status(due_on: date | None, stage: Stage, as_of: date,
                    sla: dict[Stage, dict] | None = None) -> TimelineStatus:
    """How a case is tracking against its deadline, as of a given day.

    A case with no due date reads ON_TIME rather than raising. That is the
    honest answer for a case created before deadlines existed: we do not know
    of a missed milestone, and inventing a breach would be worse than
    admitting we have no target on file.
    """
    if due_on is None:
        return TimelineStatus.ON_TIME
    if as_of > due_on:
        return TimelineStatus.BREACHED

    table = sla or DEFAULT_SLA
    entry = table.get(stage) or DEFAULT_SLA[stage]
    warn_window = entry["standard_days"] * (1.0 - entry["warn_at_fraction"])
    if (due_on - as_of).days <= warn_window:
        return TimelineStatus.AT_RISK
    return TimelineStatus.ON_TIME


def days_remaining(due_on: date | None, as_of: date) -> int | None:
    """Days until the deadline; negative once it has passed. None if no
    deadline is on file, which the frontend renders as a dash rather than
    as zero — zero would read as "due today"."""
    if due_on is None:
        return None
    return (due_on - as_of).days


def seed_defaults(db: Session) -> int:
    """Write DEFAULT_SLA into stage_sla for any stage that has no row.

    Idempotent, so it is safe to call on every startup and from the seed.
    Returns how many rows it added.
    """
    existing = {row.stage for row in db.query(StageSla).all()}
    added = 0
    for stage, entry in DEFAULT_SLA.items():
        if stage in existing:
            continue
        db.add(
            StageSla(
                stage=stage,
                standard_days=entry["standard_days"],
                statutory_days=entry["statutory_days"],
                warn_at_fraction=entry["warn_at_fraction"],
                basis=entry["basis"],
            )
        )
        added += 1
    return added
