"""Turns database rows into the plain dicts the rules consume.

Lives next to the rules that define what it must contain, so the query and
the rules cannot drift apart. Loads in a fixed number of queries regardless
of how many cases exist, rather than walking relationships per case.

Dates stay as date objects here — the rules do date arithmetic, and
serialising to ISO strings only to parse them straight back would be work
that exists purely to be undone.
"""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.core.enums import Stage
from app.models import (
    Case,
    CaseStageHistory,
    Compensation,
    Document,
    FundDeposit,
    Objection,
    RequiredDocument,
    RnRRecord,
    StageSla,
)


def load_cases(db: Session, case_ids: list[int] | None = None) -> list[dict]:
    """Build the rule input. Pass case_ids to restrict it, or None for all."""
    scoped = case_ids is not None

    rnr_statuses = defaultdict(list)
    query = db.query(RnRRecord.case_id, RnRRecord.status)
    if scoped:
        query = query.filter(RnRRecord.case_id.in_(case_ids))
    for case_id, status in query.all():
        rnr_statuses[case_id].append(status.value)

    document_types = defaultdict(list)
    # Only CURRENT versions count as on file. A superseded award copy is
    # history, not a satisfied requirement — without this filter, replacing
    # a document would leave the old row still answering for it.
    query = db.query(Document.case_id, Document.doc_type).filter(Document.is_current.is_(True))
    if scoped:
        query = query.filter(Document.case_id.in_(case_ids))
    for case_id, doc_type in query.all():
        document_types[case_id].append(doc_type.value)

    # Stage -> required doc types, read from the table rather than a
    # constant, so the lookup Backend owns is the single source of truth.
    required_by_stage = defaultdict(list)
    for stage, doc_type in db.query(RequiredDocument.stage, RequiredDocument.doc_type).all():
        required_by_stage[stage].append(doc_type.value)

    objections = defaultdict(list)
    query = db.query(Objection.id, Objection.case_id, Objection.status, Objection.filed_on)
    if scoped:
        query = query.filter(Objection.case_id.in_(case_ids))
    for row in query.all():
        objections[row.case_id].append(
            {"id": row.id, "status": row.status.value, "filed_on": row.filed_on}
        )

    compensations = defaultdict(list)
    query = db.query(
        Compensation.case_id,
        Compensation.status,
        Compensation.amount_awarded,
        Compensation.amount_paid,
        Compensation.awarded_on,
    )
    if scoped:
        query = query.filter(Compensation.case_id.in_(case_ids))
    for row in query.all():
        compensations[row.case_id].append(
            {
                "status": row.status.value,
                "amount_awarded": row.amount_awarded,
                "amount_paid": row.amount_paid,
                "awarded_on": row.awarded_on,
            }
        )

    # Stage allowances, so the timeline rule can judge a case against its
    # own stage rather than one flat threshold for all nine.
    standard_days_by_stage = {
        stage: standard_days
        for stage, standard_days in db.query(StageSla.stage, StageSla.standard_days).all()
    }

    # Earliest date each case reached POSSESSION, for the unused_land rule
    # (Sec. 101: land sitting unutilised more than five years after
    # possession). Earliest, not latest — a case sent back and re-advanced
    # through possession a second time is still measured from when
    # government first took the land.
    possession_since: dict[int, object] = {}
    query = db.query(CaseStageHistory.case_id, CaseStageHistory.changed_on).filter(
        CaseStageHistory.to_stage == Stage.POSSESSION
    )
    if scoped:
        query = query.filter(CaseStageHistory.case_id.in_(case_ids))
    for case_id, changed_on in query.all():
        if case_id not in possession_since or changed_on < possession_since[case_id]:
            possession_since[case_id] = changed_on

    # Whether the requiring body has deposited ANY funds against the case —
    # the fund_deposit_missing rule only needs presence, not the ledger.
    deposited_case_ids: set[int] = set()
    query = db.query(FundDeposit.case_id).distinct()
    if scoped:
        query = query.filter(FundDeposit.case_id.in_(case_ids))
    for (case_id,) in query.all():
        deposited_case_ids.add(case_id)

    case_query = db.query(Case).order_by(Case.id)
    if scoped:
        case_query = case_query.filter(Case.id.in_(case_ids))

    return [
        {
            "id": case.id,
            "case_number": case.case_number,
            "stage": case.stage.value,
            "status": case.status.value,
            "stage_changed_at": case.stage_changed_at,
            "stage_due_on": case.stage_due_on,
            "stage_standard_days": standard_days_by_stage.get(case.stage),
            "rnr_statuses": rnr_statuses.get(case.id, []),
            "document_types": document_types.get(case.id, []),
            "required_document_types": required_by_stage.get(case.stage, []),
            "objections": objections.get(case.id, []),
            "compensations": compensations.get(case.id, []),
            "possession_since": possession_since.get(case.id),
            "fund_deposited": case.id in deposited_case_ids,
        }
        for case in case_query.all()
    ]
