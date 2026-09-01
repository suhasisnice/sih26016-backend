"""KPI 6 - timeline adherence.

The problem statement names "timeline adherence" as a dashboard tile and
"timeline monitoring and milestone tracking" as a live parameter. Neither
was answerable before stage_sla existed: the system recorded when a stage
changed but never what it was supposed to cost, so there was no milestone to
miss.

Counted in cases, not in stages. A case is one file moving through an
office, and "38 cases past their deadline" is a sentence somebody can act
on; "112 stage-instances breached" is not.
"""

from datetime import date

from sqlalchemy import Integer
from sqlalchemy import case as sql_case
from sqlalchemy import cast, func

from app.core.enums import TimelineStatus
from app.models import Case

# The last fifth of a stage's allowance reads as "at risk" rather than "on
# time". Proportional rather than a fixed number of days on purpose: seven
# days' warning on a 30-day stage is useful, seven days on a 180-day stage is
# not warning anybody about anything.
#
# app.services.sla holds a per-stage warn_at_fraction and is what a single
# case's detail page uses. This flat 20% is the dashboard aggregate's
# approximation of it — matching per stage would mean branching on nine
# stage values inside one aggregate to move a handful of cases between two
# adjacent buckets, and the breached count, which is the number anybody
# actually acts on, is exact either way.
AT_RISK_FRACTION = 0.2


def compute_timeline(db, case_ids: list[int], as_of: date | None = None) -> dict:
    """On-time / at-risk / breached counts, plus an adherence percentage.

    The buckets are computed in SQL rather than by loading every case and
    classifying it in Python — this runs on the front page of a national
    dashboard, and the difference is one aggregate row versus a hundred
    thousand ORM objects.

    Note on the arithmetic: in Postgres `date - date` yields an INTEGER
    number of days, not an interval, so these are plain integer comparisons.
    Wrapping them in date_part() looks natural and fails outright.
    """
    as_of = as_of or date.today()

    empty = {
        "timeline_on_time_count": 0,
        "timeline_at_risk_count": 0,
        "timeline_breached_count": 0,
        "timeline_untracked_count": 0,
        "timeline_adherence_pct": None,
    }
    if not case_ids:
        return empty

    tracked = Case.stage_due_on.isnot(None)
    # Days the stage was allowed, and days still left on the clock. Both are
    # integers straight out of Postgres.
    allowance = cast(Case.stage_due_on - Case.stage_changed_at, Integer)
    remaining = cast(Case.stage_due_on - as_of, Integer)

    not_yet_due = tracked & (Case.stage_due_on >= as_of)
    is_at_risk = not_yet_due & (remaining <= allowance * AT_RISK_FRACTION)

    def count_where(condition):
        return func.coalesce(func.sum(sql_case((condition, 1), else_=0)), 0)

    breached, at_risk, on_time, untracked = db.query(
        count_where(tracked & (Case.stage_due_on < as_of)),
        count_where(is_at_risk),
        count_where(not_yet_due & (remaining > allowance * AT_RISK_FRACTION)),
        # A case with no due date is counted separately rather than folded
        # into on-time. Folding it in would flatter the headline number with
        # cases nobody has set a target for, which is exactly what this tile
        # exists to expose.
        count_where(Case.stage_due_on.is_(None)),
    ).filter(Case.id.in_(case_ids)).one()

    total_tracked = int(breached) + int(at_risk) + int(on_time)
    # Adherence is "not breached", over the cases that HAVE a deadline. An
    # at-risk case has not missed anything yet, so counting it as a failure
    # would report a breach that has not happened. None rather than 100 when
    # nothing is tracked: an empty denominator is unknown, not perfect, and a
    # spurious 100% is the one number on this screen nobody would question.
    adherence = (
        round(100.0 * (total_tracked - int(breached)) / total_tracked, 1)
        if total_tracked
        else None
    )

    return {
        "timeline_on_time_count": int(on_time),
        "timeline_at_risk_count": int(at_risk),
        "timeline_breached_count": int(breached),
        "timeline_untracked_count": int(untracked),
        "timeline_adherence_pct": adherence,
    }


def classify(due_on: date | None, as_of: date) -> TimelineStatus:
    """Single-case version of the breached test.

    app.services.sla.timeline_status is the fuller one, with the per-stage
    at-risk window. This exists for callers that only have a due date.
    """
    if due_on is None:
        return TimelineStatus.ON_TIME
    if due_on < as_of:
        return TimelineStatus.BREACHED
    return TimelineStatus.ON_TIME
