"""Wipes and regenerates the whole demo dataset in one call: reference data,
eleven demo accounts, the acquisition caseload and the deliberate anomalies
the alert rules exist to catch.

A fixed random seed (`RANDOM_SEED`) makes every run produce the same shape
-- same ids, same case numbers, same anomaly placement -- with dates slid
to sit relative to `constants.anchor_date()`. Reseeding is meant to be
cheap enough to rerun casually; on a laptop this is a few seconds, not
minutes.
"""

import random
from datetime import timedelta

from sqlalchemy import func, text

from app.ai_layer.constants import (
    ANOMALY_AWARDS_FORCED_UNPAID,
    ANOMALY_DOCUMENTS_REMOVED,
    ANOMALY_OBJECTIONS_FORCED_OPEN,
    ANOMALY_POSSESSION_BEFORE_RNR,
    ANOMALY_STALLED_CRITICAL_CASES,
    ANOMALY_STALLED_WARNING_CASES,
    ANOMALY_TIMELINE_BREACHED,
    DISTRICT_NAMES,
    PERSON_COUNT_MIN,
    PROPOSAL_COUNT_RANGE,
    RANDOM_SEED,
    SECONDARY_CASES_PER_DISTRICT,
    STALLED_CRITICAL_DAYS,
    STALLED_DAYS,
    anchor_date,
)
from app.ai_layer.rules.unused_land import FIVE_YEARS_DAYS
from app.ai_layer.seed.generators import STAGE_ORDER, build_case, make_people
from app.ai_layer.seed.reference import (
    seed_projects,
    seed_required_documents,
    seed_states_and_districts,
    seed_users,
    seed_villages,
)

from app.config import settings
from app.core.enums import ProposalStatus, Stage
from app.database import Base, engine
from app.models import Parcel, Proposal
from app.services import alerts, numbering, sla

# Front-loaded: earlier stages carry more of the caseload, which is what a
# real office's pipeline looks like -- most files are still in motion,
# fewer have made it all the way through.
STAGE_WEIGHTS = [20, 16, 14, 12, 10, 9, 8, 6, 5]
assert len(STAGE_WEIGHTS) == len(STAGE_ORDER)

# Every data-bearing table, in no particular order -- TRUNCATE ... CASCADE
# handles the foreign keys, so ordering them by dependency is unnecessary.
_TABLES = [
    "alerts", "audit_log", "notifications", "case_stage_history", "compensation",
    "rnr_records", "affected_families", "objections", "documents", "statutory_notices",
    "proposal_reviews", "proposals", "parcels", "cases", "invite_codes", "users",
    "required_documents", "stage_sla", "projects", "people", "villages", "districts",
    "states",
]


def _is_local_database() -> bool:
    url = settings.database_url
    return "@db:" in url or "localhost" in url or "127.0.0.1" in url


def _truncate_all(db) -> None:
    db.execute(text(f"TRUNCATE TABLE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
    db.commit()


def run_seed(db, *, allow_remote: bool = False, rebuild: bool = False) -> dict:
    """Wipe every table and regenerate the demo dataset. Commits on success.

    Refuses to run against anything that does not look like the local
    compose database unless `allow_remote=True` is passed explicitly --
    this deletes every row in every table, and `DATABASE_URL` pointing at a
    shared deployment during integration week is exactly the moment a
    reflexive rerun would be catastrophic.
    """
    if not allow_remote and not _is_local_database():
        raise RuntimeError(
            "DATABASE_URL does not look like the local compose database. "
            "Pass allow_remote=True (or --allow-remote on the CLI) if this "
            "is really intended -- this command deletes every row in every table."
        )

    if rebuild:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _truncate_all(db)

    rng = random.Random(RANDOM_SEED)
    as_of = anchor_date()

    districts, states = seed_states_and_districts(db)
    villages = seed_villages(db, districts)
    seed_required_documents(db)
    projects = seed_projects(db, districts)
    users = seed_users(db, districts, states)
    sla.seed_defaults(db)
    db.flush()

    karnataka_names = set(DISTRICT_NAMES)
    people = make_people(db, rng, villages, karnataka_names, count=max(PERSON_COUNT_MIN, 320))
    sla_table = sla.load_sla(db)
    district_by_id = {d.id: d for d in districts.values()}

    sequence_counters: dict[tuple[int, int], int] = {}

    def next_sequence(district, year: int) -> int:
        key = (district.id, year)
        sequence_counters[key] = sequence_counters.get(key, 0) + 1
        return sequence_counters[key]

    def stage_index_choice() -> int:
        return rng.choices(range(len(STAGE_ORDER)), weights=STAGE_WEIGHTS, k=1)[0]

    cases = []

    # --- baseline caseload: every project gets a handful of cases, never
    # deliberately tripping a rule ---
    for project in projects:
        district = district_by_id[project.district_id]
        is_karnataka = district.name in karnataka_names
        case_count = rng.randint(4, 8) if is_karnataka else rng.randint(*SECONDARY_CASES_PER_DISTRICT)
        parcel_range = (2, 5) if is_karnataka else (1, 3)
        district_villages = villages[district.name]

        for _ in range(case_count):
            cases.append(
                build_case(
                    db, rng,
                    project=project,
                    district=district,
                    village=rng.choice(district_villages),
                    stage_index=stage_index_choice(),
                    sequence_provider=next_sequence,
                    people_pool=people,
                    sla_table=sla_table,
                    as_of=as_of,
                    parcel_count=rng.randint(*parcel_range),
                )
            )

    # --- deliberate anomalies: dedicated extra cases, each built so exactly
    # one rule has something real to fire on ---
    karnataka_projects = [
        p for p in projects if district_by_id[p.district_id].name in karnataka_names
    ]
    objection_period_index = STAGE_ORDER.index(Stage.OBJECTION_PERIOD)
    award_index = STAGE_ORDER.index(Stage.AWARD)
    possession_index = STAGE_ORDER.index(Stage.POSSESSION)

    def anomaly_case(stage_index: int, **forces) -> None:
        project = rng.choice(karnataka_projects)
        district = district_by_id[project.district_id]
        cases.append(
            build_case(
                db, rng,
                project=project,
                district=district,
                village=rng.choice(villages[district.name]),
                stage_index=stage_index,
                sequence_provider=next_sequence,
                people_pool=people,
                sla_table=sla_table,
                as_of=as_of,
                parcel_count=rng.randint(2, 5),
                **forces,
            )
        )

    for _ in range(ANOMALY_STALLED_CRITICAL_CASES):
        anomaly_case(stage_index_choice(), force_stalled_days=STALLED_CRITICAL_DAYS + 5)
    for _ in range(ANOMALY_STALLED_WARNING_CASES):
        anomaly_case(stage_index_choice(), force_stalled_days=STALLED_DAYS + 4)
    for _ in range(ANOMALY_DOCUMENTS_REMOVED):
        anomaly_case(stage_index_choice(), force_missing_docs=True)
    for _ in range(ANOMALY_OBJECTIONS_FORCED_OPEN):
        anomaly_case(rng.randint(objection_period_index, len(STAGE_ORDER) - 1), force_objection_open=True)
    for _ in range(ANOMALY_AWARDS_FORCED_UNPAID):
        anomaly_case(rng.randint(award_index + 1, len(STAGE_ORDER) - 1), force_unpaid_award=True)
    for _ in range(ANOMALY_POSSESSION_BEFORE_RNR):
        anomaly_case(rng.randint(possession_index, len(STAGE_ORDER) - 1), force_possession_before_rnr=True)
    for _ in range(ANOMALY_TIMELINE_BREACHED):
        anomaly_case(stage_index_choice(), force_breach=True)

    # Two more rules added after the original anomaly set above, following
    # the same one-case-per-finding pattern.
    for _ in range(2):
        anomaly_case(award_index + 1, force_no_fund_deposit=True)
    # Possession taken well over five years ago, and the case never
    # advanced past it -- unused_land's exact condition (Sec. 101).
    for _ in range(2):
        anomaly_case(possession_index, force_stalled_days=FIVE_YEARS_DAYS + 90)

    # The demo landowner account needs a real person behind it, or
    # scope_cases_to_user has nothing to key off and the account sees
    # nothing at all.
    first_owner_id = db.query(Parcel.owner_id).filter(Parcel.case_id == cases[0].id).first()
    if first_owner_id:
        users["landowner"].person_id = first_owner_id[0]

    # --- proposal pipeline: submission -> state scrutiny -> ministry
    # sanction, so the requiring-body, state and ministry accounts have
    # something to act on. scope_cases_to_user for REQUIRING_BODY reads
    # Proposal, not Project, so without this those accounts would see
    # nothing despite Projects existing in their name.
    requiring_body_users = {
        u.organisation: u for u in users.values() if u.organisation is not None
    }
    karnataka_state = states["Karnataka"]
    case_ids_by_project: dict[int, list[int]] = {}
    for case in cases:
        case_ids_by_project.setdefault(case.project_id, []).append(case.id)

    status_cycle = (
        [ProposalStatus.DRAFT] * 2
        + [ProposalStatus.SUBMITTED] * 3
        + [ProposalStatus.UNDER_SCRUTINY] * 3
        + [ProposalStatus.RETURNED] * 2
        + [ProposalStatus.APPROVED] * 5
        + [ProposalStatus.REJECTED] * 2
        + [ProposalStatus.WITHDRAWN] * 1
    )
    proposal_count = rng.randint(*PROPOSAL_COUNT_RANGE)
    for i in range(proposal_count):
        project = rng.choice(karnataka_projects)
        district = district_by_id[project.district_id]
        village = rng.choice(villages[district.name])
        status = status_cycle[i % len(status_cycle)]
        submitted_by = requiring_body_users.get(project.requiring_body)

        created_at = as_of - timedelta(days=rng.randint(20, 400))
        submitted_on = None if status is ProposalStatus.DRAFT else created_at + timedelta(days=rng.randint(1, 5))
        status_changed_on = submitted_on or created_at

        decided_on = None
        decided_by_id = None
        decision_note = None
        case_id = None
        if status in (ProposalStatus.APPROVED, ProposalStatus.REJECTED):
            decided_on = status_changed_on + timedelta(days=rng.randint(10, 60))
            decided_by_id = users["ministry"].id
            decision_note = (
                "Sanctioned under Section 3(1)."
                if status is ProposalStatus.APPROVED
                else "Returned for want of budgetary provision."
            )
            status_changed_on = decided_on
            if status is ProposalStatus.APPROVED:
                linked = case_ids_by_project.get(project.id)
                if linked:
                    case_id = rng.choice(linked)

        db.add(
            Proposal(
                proposal_number=numbering.build_proposal_number(
                    karnataka_state.code, created_at.year, i + 1
                ),
                title=f"{project.name} — land acquisition proposal",
                purpose=f"Acquisition of land for {project.name}.",
                requiring_body=project.requiring_body,
                state_id=karnataka_state.id,
                district_id=district.id,
                village_id=village.id,
                estimated_area_ha=round(rng.uniform(5, 120), 2),
                estimated_families=rng.randint(20, 300),
                estimated_cost=round(rng.uniform(5_00_00_000, 80_00_00_000)),
                status=status,
                submitted_by_user_id=submitted_by.id if submitted_by else None,
                submitted_on=submitted_on,
                decided_by_user_id=decided_by_id,
                decided_on=decided_on,
                decision_note=decision_note,
                case_id=case_id,
                project_id=project.id,
                created_at=created_at,
                status_changed_on=status_changed_on,
            )
        )

    db.commit()

    rules_summary = alerts.regenerate_alerts(db, as_of=as_of)
    db.commit()

    parcel_count = db.query(func.count(Parcel.id)).scalar() or 0

    return {
        "as_of": as_of.isoformat(),
        "states": len(states),
        "districts": len(districts),
        "villages": sum(len(v) for v in villages.values()),
        "projects": len(projects),
        "people": len(people),
        "cases": len(cases),
        "parcels": int(parcel_count),
        **rules_summary,
    }
