"""Deliberately breaks a slice of cases so each alert rule has something
real to find. Runs last, mutating rows the generators already created.

Every query here is ordered by id. Postgres gives no row-order guarantee
without an ORDER BY, so an unordered .first() or .limit() could pick
different rows on different runs even with the random seed fixed — which
would quietly break the "regenerating produces identical data" guarantee
the whole demo rests on.
"""

import random
from datetime import date, timedelta

from app.ai_layer import constants as c
from app.core.enums import CaseStatus, CompensationStatus, ObjectionStatus, RnRStatus, Stage
from app.models import Case, Compensation, Document, Objection, RnRRecord
from app.services import sla as sla_service

POSSESSION_STAGES = (Stage.POSSESSION, Stage.MONITORING)


def apply_anomalies(session, cases: list[Case], rng: random.Random, anchor: date) -> dict:
    summary = {
        "cases_stalled_warning": 0,
        "cases_stalled_critical": 0,
        "documents_removed": 0,
        "objections_forced_open": 0,
        "awards_forced_unpaid": 0,
        "possession_before_rnr_forced": 0,
        "timeline_breaches_forced": 0,
    }

    flawed_count = max(1, round(len(cases) * c.ANOMALY_FRACTION))
    flawed_cases = rng.sample(cases, min(flawed_count, len(cases)))

    # 1. Cases that have not moved. Also flip status to STALLED so the
    #    case list shows it without waiting for the rule runner.
    stalled_total = c.ANOMALY_STALLED_CRITICAL_CASES + c.ANOMALY_STALLED_WARNING_CASES
    for index, case in enumerate(flawed_cases[:stalled_total]):
        if index < c.ANOMALY_STALLED_CRITICAL_CASES:
            days_back = c.STALLED_CRITICAL_DAYS + rng.randint(1, 15)
            summary["cases_stalled_critical"] += 1
        else:
            days_back = c.STALLED_DAYS + rng.randint(
                1, c.STALLED_CRITICAL_DAYS - c.STALLED_DAYS - 1
            )
            summary["cases_stalled_warning"] += 1
        case.stage_changed_at = anchor - timedelta(days=days_back)
        case.status = CaseStatus.STALLED
        # The due date is derived from stage_changed_at, so moving one
        # without the other would leave a case whose deadline predates the
        # stage it belongs to.
        sla_service.apply_due_date(session, case)

    # 2. A required document missing from a few of the flawed cases.
    flawed_ids = [case.id for case in flawed_cases]
    documents = (
        session.query(Document)
        .filter(Document.case_id.in_(flawed_ids))
        .order_by(Document.case_id, Document.id)
        .all()
    )
    seen: set[int] = set()
    for document in documents:
        if summary["documents_removed"] >= c.ANOMALY_DOCUMENTS_REMOVED:
            break
        if document.case_id in seen:
            continue
        seen.add(document.case_id)
        session.delete(document)
        summary["documents_removed"] += 1

    # 3. Objections still unanswered past the response window.
    stale_objections = (
        session.query(Objection)
        .filter(Objection.status.in_([ObjectionStatus.RESOLVED, ObjectionStatus.REJECTED]))
        .order_by(Objection.id)
        .limit(c.ANOMALY_OBJECTIONS_FORCED_OPEN)
        .all()
    )
    for objection in stale_objections:
        objection.status = ObjectionStatus.FILED
        objection.response = None
        objection.responded_on = None
        objection.filed_on = anchor - timedelta(
            days=c.OBJECTION_RESPONSE_DAYS + rng.randint(1, 15)
        )
        summary["objections_forced_open"] += 1

    # 4. An award made but never disbursed.
    stale_awards = (
        session.query(Compensation)
        .filter(Compensation.status == CompensationStatus.AWARDED)
        .order_by(Compensation.id)
        .limit(c.ANOMALY_AWARDS_FORCED_UNPAID)
        .all()
    )
    for compensation in stale_awards:
        compensation.awarded_on = anchor - timedelta(
            days=c.AWARD_PAYMENT_DAYS + rng.randint(1, 20)
        )
        summary["awards_forced_unpaid"] += 1

    # 5. Possession taken before resettlement finished. The most
    #    domain-aware rule we have, so the seed must always contain one.
    possession_cases = sorted(
        (case for case in cases if case.stage in POSSESSION_STAGES), key=lambda case: case.id
    )
    for target in rng.sample(
        possession_cases, min(c.ANOMALY_POSSESSION_BEFORE_RNR, len(possession_cases))
    ):
        record = (
            session.query(RnRRecord)
            .filter(RnRRecord.case_id == target.id)
            .order_by(RnRRecord.id)
            .first()
        )
        if record:
            record.status = RnRStatus.IN_PROGRESS
            summary["possession_before_rnr_forced"] += 1

    # 6. Cases pushed past their STAGE's own deadline, for timeline_breach.
    #    Chosen from cases not already force-stalled above, so the breach set
    #    is a distinct set of case ids rather than the same handful wearing a
    #    second label. These cases do also trip case_stalled, and that is
    #    correct: the overshoot below is a full stage allowance plus a margin,
    #    thirty-five days at the very least, against a flat ten-day stalled
    #    window. The two rules are answering different questions about the
    #    same case — "nobody has touched this" and "this stage is over its
    #    allowance" — and an officer wants to be told both.
    stalled_ids = {case.id for case in flawed_cases[:stalled_total]}
    sla_table = sla_service.load_sla(session)
    breach_candidates = sorted(
        (case for case in cases if case.id not in stalled_ids),
        key=lambda case: case.id,
    )
    for case in rng.sample(
        breach_candidates, min(c.ANOMALY_TIMELINE_BREACHED, len(breach_candidates))
    ):
        entry = sla_table.get(case.stage) or sla_service.DEFAULT_SLA[case.stage]
        # Between one and one-and-a-half times the allowance past the start,
        # so the breach is unambiguous and the severity spread is visible.
        overshoot = entry["standard_days"] + rng.randint(5, max(6, entry["standard_days"] // 2))
        case.stage_changed_at = anchor - timedelta(days=overshoot)
        sla_service.apply_due_date(session, case, sla_table)
        summary["timeline_breaches_forced"] += 1

    session.flush()
    return summary
