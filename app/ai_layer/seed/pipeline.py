"""Seed generators for the two things added after the original build: the
register of published instruments, and the proposal approval pipeline.

Kept in their own module rather than appended to generators.py, which was
already the longest file in the seed. Same conventions apply — everything
here is legally coherent, so no alert rule fires on it by accident, and
anomalies.py remains the only place that deliberately breaks anything.
"""

import random
from datetime import date, timedelta

from sqlalchemy import func

from app.ai_layer import constants as c
from app.ai_layer.seed import reference as ref
from app.core.enums import NoticeType, ProposalStatus, Role, Stage
from app.models import (
    Case,
    Compensation,
    District,
    Proposal,
    ProposalReview,
    State,
    StatutoryNotice,
    User,
    Village,
)
from app.services.numbering import build_proposal_number

STAGE_ORDER = list(Stage)

# Which instruments a case at a given stage must already have published. A
# case at award has necessarily been notified and declared first, so the
# register is built cumulatively rather than as one notice per case. That is
# what makes "notifications issued" a figure that only ever rises — unlike
# the stage-inferred number it replaces, which fell as cases progressed.
NOTICE_MINIMUM_INDEX = {
    NoticeType.PRELIMINARY_NOTIFICATION: STAGE_ORDER.index(Stage.PRELIMINARY_NOTIFICATION),
    NoticeType.DECLARATION: STAGE_ORDER.index(Stage.DECLARATION),
    NoticeType.AWARD: STAGE_ORDER.index(Stage.AWARD),
    NoticeType.POSSESSION_NOTICE: STAGE_ORDER.index(Stage.POSSESSION),
}

NOTICE_SECTION = {
    NoticeType.PRELIMINARY_NOTIFICATION: "Section 11(1)",
    NoticeType.DECLARATION: "Section 19(1)",
    NoticeType.AWARD: "Section 23",
    NoticeType.POSSESSION_NOTICE: "Section 38(1)",
}


def generate_statutory_notices(
    session,
    cases: list[Case],
    districts: dict[str, District],
    rng: random.Random,
    anchor: date,
) -> int:
    """Build each case's register backwards from the stage it has reached.

    A case at possession has been notified, declared, awarded and taken —
    four instruments, each with its own date and gazette reference, dated in
    proportion to how far past that stage the case has travelled so the
    register reads as a sequence rather than four notices issued the same
    afternoon.
    """
    district_by_id = {d.id: d for d in districts.values()}

    # Award beneficiary counts and totals, in one grouped query.
    awards_by_case = {
        case_id: (int(count), int(total))
        for case_id, count, total in session.query(
            Compensation.case_id,
            func.count(Compensation.id),
            func.coalesce(func.sum(Compensation.amount_awarded), 0),
        )
        .group_by(Compensation.case_id)
        .all()
    }

    written = 0
    for case in cases:
        stage_index = STAGE_ORDER.index(case.stage)
        district = district_by_id.get(case.district_id)
        authority = f"Office of the Deputy Commissioner, {district.name}" if district else "—"

        for notice_type, minimum_index in NOTICE_MINIMUM_INDEX.items():
            if stage_index < minimum_index:
                continue

            steps_since = stage_index - minimum_index
            issued_on = case.stage_changed_at - timedelta(
                days=steps_since * rng.randint(20, 60) + rng.randint(0, 10)
            )
            # Clamped into the case's own lifetime: an instrument dated
            # before the file was opened, or after today, is a data error
            # that would show up on the public notice board.
            issued_on = min(max(issued_on, case.created_at), anchor)

            beneficiary_count = None
            total_amount = None
            if notice_type is NoticeType.AWARD:
                beneficiary_count, total_amount = awards_by_case.get(case.id, (0, 0))

            session.add(
                StatutoryNotice(
                    case_id=case.id,
                    notice_type=notice_type,
                    section_reference=NOTICE_SECTION[notice_type],
                    gazette_number=f"KGZ/{issued_on.year}/{rng.randint(1000, 9999)}",
                    issuing_authority=authority,
                    issued_on=issued_on,
                    document_id=None,
                    issued_by_user_id=None,
                    beneficiary_count=beneficiary_count,
                    total_amount=total_amount,
                )
            )
            written += 1

    session.flush()
    return written


# How seeded proposals spread across the chain. Weighted so every status is
# represented — a pipeline screen with four of its seven columns empty looks
# broken rather than quiet — while leaving the bulk in the states an officer
# would actually be working on.
PROPOSAL_STATUS_WEIGHTS = {
    ProposalStatus.DRAFT: 12,
    ProposalStatus.SUBMITTED: 22,
    ProposalStatus.UNDER_SCRUTINY: 24,
    ProposalStatus.RETURNED: 14,
    ProposalStatus.APPROVED: 18,
    ProposalStatus.REJECTED: 6,
    ProposalStatus.WITHDRAWN: 4,
}

# The route a proposal took to reach each status, so the review trail on the
# detail page is a real history instead of one row appearing from nowhere.
PATH_TO_STATUS = {
    ProposalStatus.DRAFT: [ProposalStatus.DRAFT],
    ProposalStatus.SUBMITTED: [ProposalStatus.DRAFT, ProposalStatus.SUBMITTED],
    ProposalStatus.UNDER_SCRUTINY: [
        ProposalStatus.DRAFT,
        ProposalStatus.SUBMITTED,
        ProposalStatus.UNDER_SCRUTINY,
    ],
    ProposalStatus.RETURNED: [
        ProposalStatus.DRAFT,
        ProposalStatus.SUBMITTED,
        ProposalStatus.RETURNED,
    ],
    ProposalStatus.APPROVED: [
        ProposalStatus.DRAFT,
        ProposalStatus.SUBMITTED,
        ProposalStatus.UNDER_SCRUTINY,
        ProposalStatus.APPROVED,
    ],
    ProposalStatus.REJECTED: [
        ProposalStatus.DRAFT,
        ProposalStatus.SUBMITTED,
        ProposalStatus.UNDER_SCRUTINY,
        ProposalStatus.REJECTED,
    ],
    ProposalStatus.WITHDRAWN: [
        ProposalStatus.DRAFT,
        ProposalStatus.SUBMITTED,
        ProposalStatus.WITHDRAWN,
    ],
}

# Which tier performs each hand-off. Mirrors the TRANSITIONS table in
# app.services.proposals — the seed must not produce a history the live
# workflow would have refused.
ROLE_FOR_STATUS = {
    ProposalStatus.DRAFT: Role.REQUIRING_BODY,
    ProposalStatus.SUBMITTED: Role.REQUIRING_BODY,
    ProposalStatus.UNDER_SCRUTINY: Role.STATE_OFFICER,
    ProposalStatus.RETURNED: Role.STATE_OFFICER,
    ProposalStatus.APPROVED: Role.MINISTRY_OFFICER,
    ProposalStatus.REJECTED: Role.MINISTRY_OFFICER,
    ProposalStatus.WITHDRAWN: Role.REQUIRING_BODY,
}

NOTE_FOR_STATUS = {
    ProposalStatus.DRAFT: lambda rng: "Proposal opened",
    ProposalStatus.RETURNED: lambda rng: rng.choice(ref.RETURN_NOTES),
    ProposalStatus.UNDER_SCRUTINY: lambda rng: rng.choice(ref.SCRUTINY_NOTES),
    ProposalStatus.APPROVED: lambda rng: rng.choice(ref.APPROVAL_NOTES),
    ProposalStatus.REJECTED: lambda rng: rng.choice(ref.REJECTION_NOTES),
}


def generate_proposals(
    session,
    states: dict[str, State],
    districts: dict[str, District],
    villages: dict[str, Village],
    users: list[User],
    cases: list[Case],
    rng: random.Random,
    anchor: date,
) -> dict:
    """The proposal pipeline, spread across every status in the chain.

    Sanctioned proposals are linked to a case that already exists rather than
    creating new ones. That keeps the case count and every case id stable
    across a reseed — which the whole demo rests on — while still showing the
    provenance link a sanctioned proposal is supposed to leave behind.
    """
    user_by_role: dict[Role, list[User]] = {}
    for user in users:
        user_by_role.setdefault(user.role, []).append(user)

    requiring_bodies = user_by_role.get(Role.REQUIRING_BODY, [])
    primary_state = states[c.STATE]
    primary_districts = [d for d in districts.values() if d.state_id == primary_state.id]
    if not primary_districts:
        return {"proposals": 0, "proposals_by_status": {}}

    villages_by_district: dict[int, list[Village]] = {}
    for village in villages.values():
        villages_by_district.setdefault(village.district_id, []).append(village)

    primary_district_ids = {d.id for d in primary_districts}
    # Cases a sanctioned proposal may claim, each at most once. Sorted by id
    # before shuffling so the shuffle is reproducible under the fixed seed.
    claimable = sorted(
        (case for case in cases if case.district_id in primary_district_ids),
        key=lambda case: case.id,
    )
    rng.shuffle(claimable)
    claim_index = 0

    statuses = list(PROPOSAL_STATUS_WEIGHTS.keys())
    weights = list(PROPOSAL_STATUS_WEIGHTS.values())
    sequence = 0

    summary = {status.value: 0 for status in ProposalStatus}
    total = rng.randint(*c.PROPOSAL_COUNT_RANGE)
    written = 0

    for _ in range(total):
        template = rng.choice(ref.PROPOSAL_TEMPLATES)
        district = rng.choice(primary_districts)
        village_pool = villages_by_district.get(district.id, [])
        if not village_pool:
            continue
        village = rng.choice(village_pool)
        final_status = rng.choices(statuses, weights=weights, k=1)[0]

        sequence += 1
        submitter = rng.choice(requiring_bodies) if requiring_bodies else None

        created_at = anchor - timedelta(days=rng.randint(20, 200))
        proposal = Proposal(
            proposal_number=build_proposal_number(primary_state.code, 2026, sequence),
            title=f"{template['title']} - {village.name}"[:200],
            purpose=template["purpose"],
            requiring_body=(submitter.organisation if submitter else template["requiring_body"]),
            state_id=primary_state.id,
            district_id=district.id,
            village_id=village.id,
            estimated_area_ha=round(rng.uniform(2.0, 90.0), 2),
            estimated_families=rng.randint(4, 180),
            estimated_cost=rng.randint(5_000_000, 900_000_000),
            status=final_status,
            submitted_by_user_id=submitter.id if submitter else None,
            created_at=created_at,
            status_changed_on=created_at,
        )
        session.add(proposal)
        session.flush()

        # Walk the trail forward from the day it was opened.
        cursor = created_at
        previous_status = None
        for step_status in PATH_TO_STATUS[final_status]:
            if previous_status is not None:
                cursor = min(anchor, cursor + timedelta(days=rng.randint(3, 30)))

            actor_role = ROLE_FOR_STATUS[step_status]
            actor_pool = user_by_role.get(actor_role, [])
            actor = rng.choice(actor_pool) if actor_pool else None
            note_fn = NOTE_FOR_STATUS.get(step_status)

            session.add(
                ProposalReview(
                    proposal_id=proposal.id,
                    from_status=previous_status,
                    to_status=step_status,
                    actor_user_id=actor.id if actor else None,
                    actor_role=actor_role,
                    note=note_fn(rng) if note_fn else None,
                    created_on=cursor,
                )
            )

            if step_status is ProposalStatus.SUBMITTED:
                proposal.submitted_on = cursor
            if step_status in (ProposalStatus.APPROVED, ProposalStatus.REJECTED):
                proposal.decided_on = cursor
                proposal.decided_by_user_id = actor.id if actor else None
                proposal.decision_note = note_fn(rng) if note_fn else None

            previous_status = step_status

        proposal.status_changed_on = cursor

        if final_status is ProposalStatus.APPROVED and claim_index < len(claimable):
            case = claimable[claim_index]
            claim_index += 1
            proposal.case_id = case.id
            proposal.project_id = case.project_id

        summary[final_status.value] += 1
        written += 1

    session.flush()
    return {"proposals": written, "proposals_by_status": summary}
