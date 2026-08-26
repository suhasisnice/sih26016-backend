"""Builds every row of the demo database, in dependency order.

Deliberate flaws are NOT introduced here — see anomalies.py, which runs
last. Everything this module produces is legally coherent, so no alert rule
fires on it by accident. That matters: when the baseline data trips a rule
at random, the deliberate anomalies are lost in the noise and the alert
counts stop meaning anything.
"""

import random
from datetime import date, timedelta

from app.ai_layer import constants as c
from app.ai_layer.seed import reference as ref
from app.ai_layer.seed.geo import random_point_wkt
from app.core.enums import (
    CaseStatus,
    CompensationStatus,
    DocType,
    ObjectionStatus,
    ParcelStatus,
    RnRStatus,
    Role,
    Stage,
)
from app.core.security import hash_password
from app.services.numbering import build_case_number
from app.models import (
    AffectedFamily,
    Case,
    CaseStageHistory,
    Compensation,
    District,
    Document,
    Objection,
    Parcel,
    Person,
    Project,
    RequiredDocument,
    RnRRecord,
    User,
    Village,
)

STAGE_ORDER = list(Stage)
STAGE_WEIGHTS = {
    Stage.PRELIMINARY_NOTIFICATION: 20,
    Stage.SOCIAL_IMPACT_ASSESSMENT: 16,
    Stage.LAND_VERIFICATION: 14,
    Stage.OBJECTION_PERIOD: 12,
    Stage.DECLARATION: 10,
    Stage.AWARD: 10,
    Stage.REHABILITATION_RESETTLEMENT: 8,
    Stage.POSSESSION: 6,
    Stage.MONITORING: 4,
}

SIA_INDEX = STAGE_ORDER.index(Stage.SOCIAL_IMPACT_ASSESSMENT)
OBJECTION_INDEX = STAGE_ORDER.index(Stage.OBJECTION_PERIOD)
AWARD_INDEX = STAGE_ORDER.index(Stage.AWARD)
RNR_INDEX = STAGE_ORDER.index(Stage.REHABILITATION_RESETTLEMENT)
POSSESSION_INDEX = STAGE_ORDER.index(Stage.POSSESSION)


def generate_districts(session) -> dict[str, District]:
    districts = {}
    for name in c.DISTRICT_NAMES:
        district = District(name=name, state=c.STATE, code=ref.DISTRICT_ABBR[name])
        session.add(district)
        districts[name] = district
    session.flush()
    return districts


def generate_villages(session, districts: dict[str, District]) -> dict[str, Village]:
    villages = {}
    for district_name, village_names in ref.DISTRICT_VILLAGES.items():
        for village_name in village_names:
            village = Village(name=village_name, district_id=districts[district_name].id)
            session.add(village)
            villages[village_name] = village
    session.flush()
    return villages


def generate_projects(session, districts: dict[str, District]) -> list[Project]:
    projects = []
    for entry in ref.PROJECTS:
        project = Project(
            name=entry["name"],
            requiring_body=entry["requiring_body"],
            district_id=districts[entry["district_name"]].id,
        )
        session.add(project)
        projects.append(project)
    session.flush()
    return projects


def generate_people(session, villages: dict[str, Village], rng: random.Random) -> list[Person]:
    people = []
    village_list = list(villages.values())
    per_village = max(1, c.PERSON_COUNT_MIN // len(village_list) + 1)
    phone_seq = 0

    for village in village_list:
        for _ in range(per_village):
            is_male = rng.random() < 0.5
            first = rng.choice(ref.FIRST_NAMES_MALE if is_male else ref.FIRST_NAMES_FEMALE)
            last = rng.choice(ref.LAST_NAMES)
            has_land_title = rng.random() >= c.LANDLESS_AFFECTED_FRACTION
            phone_seq += 1
            person = Person(
                name=f"{first} {last}",
                village_id=village.id,
                phone=f"{c.FAKE_PHONE_PREFIX}{phone_seq:05d}",
                has_land_title=has_land_title,
            )
            session.add(person)
            people.append(person)
    session.flush()
    return people


def generate_users(session, districts: dict[str, District], people: list[Person]) -> list[User]:
    """One demo login per role, so two different roles can be shown side by
    side getting genuinely different responses from the same endpoint."""
    password_hash = hash_password(c.DEMO_PASSWORD)
    bru = districts["Bengaluru Rural"].id
    tum = districts["Tumakuru"].id
    landowner_person = next(p for p in people if p.has_land_title)

    specs = [
        ("admin", "Anita Desai", Role.ADMIN, None, None),
        ("dc.bengaluru", "Ravi Kulkarni", Role.DISTRICT_OFFICER, bru, None),
        ("dc.tumakuru", "Meera Joshi", Role.DISTRICT_OFFICER, tum, None),
        ("slao.bengaluru", "Prakash Rao", Role.SLAO, bru, None),
        ("field.bengaluru", "Sunil Gowda", Role.FIELD_OFFICER, bru, None),
        ("rnr.bengaluru", "Latha Shetty", Role.RNR_OFFICER, bru, None),
        ("landowner", landowner_person.name, Role.LANDOWNER, None, landowner_person.id),
    ]

    users = []
    for username, full_name, role, district_id, person_id in specs:
        user = User(
            username=username,
            full_name=full_name,
            password_hash=password_hash,
            role=role,
            district_id=district_id,
            person_id=person_id,
            is_active=True,
        )
        session.add(user)
        users.append(user)
    session.flush()
    return users


def generate_cases(
    session,
    projects: list[Project],
    districts: dict[str, District],
    villages: dict[str, Village],
    rng: random.Random,
    anchor: date,
) -> list[Case]:
    total_cases = rng.randint(*c.CASE_COUNT_RANGE)
    stages = list(STAGE_WEIGHTS.keys())
    weights = list(STAGE_WEIGHTS.values())

    district_name_by_id = {d.id: name for name, d in districts.items()}
    villages_by_district = {
        name: [v for v in villages.values() if district_name_by_id[v.district_id] == name]
        for name in c.DISTRICT_NAMES
    }
    seq_by_district = {name: 0 for name in c.DISTRICT_NAMES}

    cases = []
    for _ in range(total_cases):
        project = rng.choice(projects)
        district_name = district_name_by_id[project.district_id]
        village = rng.choice(villages_by_district[district_name])
        stage = rng.choices(stages, weights=weights, k=1)[0]

        seq_by_district[district_name] += 1
        case_number = build_case_number(
            ref.DISTRICT_ABBR[district_name], 2026, seq_by_district[district_name]
        )
        title = rng.choice(ref.CASE_TITLE_TEMPLATES).format(
            project=project.name, village=village.name
        )

        # Held under STALLED_DAYS on purpose so the baseline never trips
        # the stalled-case rule; anomalies.py pushes chosen cases past it.
        stage_changed_at = anchor - timedelta(days=rng.randint(0, c.STALLED_DAYS - 1))
        created_at = stage_changed_at - timedelta(days=rng.randint(30, 300))

        case = Case(
            case_number=case_number,
            title=title[:200],
            project_id=project.id,
            district_id=project.district_id,
            village_id=village.id,
            stage=stage,
            status=CaseStatus.CLOSED if stage is Stage.MONITORING else CaseStatus.ACTIVE,
            stage_changed_at=stage_changed_at,
            created_at=created_at,
        )
        session.add(case)
        cases.append(case)
    session.flush()
    return cases


def generate_stage_history(session, cases: list[Case]) -> None:
    """Walk each case from the first stage up to where it sits now, so the
    timeline component has a real history behind it rather than a single
    entry appearing from nowhere."""
    for case in cases:
        target_index = STAGE_ORDER.index(case.stage)
        total_days = max((case.stage_changed_at - case.created_at).days, target_index or 1)
        step = max(1, total_days // (target_index + 1))

        previous = None
        for index in range(target_index + 1):
            if index == target_index:
                changed_on = case.stage_changed_at
            else:
                changed_on = case.created_at + timedelta(days=step * index)
            session.add(
                CaseStageHistory(
                    case_id=case.id,
                    from_stage=previous,
                    to_stage=STAGE_ORDER[index],
                    changed_by_user_id=None,
                    changed_on=min(changed_on, case.stage_changed_at),
                    note="Case opened" if previous is None else None,
                )
            )
            previous = STAGE_ORDER[index]
    session.flush()


def _parcel_status_for(case: Case, rng: random.Random) -> ParcelStatus:
    stage_index = STAGE_ORDER.index(case.stage)
    if stage_index >= POSSESSION_INDEX:
        return rng.choices(
            [ParcelStatus.POSSESSION_TAKEN, ParcelStatus.ACQUIRED], weights=[85, 15], k=1
        )[0]
    if stage_index >= AWARD_INDEX:
        return rng.choices(
            [ParcelStatus.ACQUIRED, ParcelStatus.UNDER_ACQUISITION], weights=[80, 20], k=1
        )[0]
    if stage_index >= OBJECTION_INDEX:
        return rng.choices(
            [ParcelStatus.UNDER_ACQUISITION, ParcelStatus.NOTIFIED], weights=[60, 40], k=1
        )[0]
    return ParcelStatus.NOTIFIED


def generate_parcels(
    session,
    cases: list[Case],
    people: list[Person],
    districts: dict[str, District],
    rng: random.Random,
) -> tuple[dict[int, list[Person]], dict[tuple[int, int], float]]:
    """Returns case.id -> its unique owners, and (case.id, owner.id) -> the
    hectares that owner holds in the case, so compensation can be priced off
    land actually owned rather than a second unrelated random number."""
    district_name_by_id = {d.id: name for name, d in districts.items()}

    landowners_by_village: dict[int, list[Person]] = {}
    for person in people:
        if person.has_land_title:
            landowners_by_village.setdefault(person.village_id, []).append(person)
    all_landowners = [p for p in people if p.has_land_title]

    total_parcels = rng.randint(*c.PARCEL_COUNT_RANGE)
    base = total_parcels // len(cases)
    remainder = total_parcels % len(cases)

    owners_by_case: dict[int, list[Person]] = {}
    area_by_case_owner: dict[tuple[int, int], float] = {}

    for index, case in enumerate(cases):
        parcel_count = max(1, base + (1 if index < remainder else 0))
        candidates = landowners_by_village.get(case.village_id) or all_landowners
        district_name = district_name_by_id[case.district_id]

        owner_ids = set()
        for _ in range(parcel_count):
            owner = rng.choice(candidates)
            owner_ids.add(owner.id)

            survey_number = f"{rng.randint(1, 300)}/{rng.randint(1, 6)}"
            if rng.random() < 0.4:
                survey_number += rng.choice("ABC")

            area_ha = round(rng.uniform(*c.PARCEL_AREA_HA_RANGE), 4)
            session.add(
                Parcel(
                    case_id=case.id,
                    survey_number=survey_number,
                    area_ha=area_ha,
                    owner_id=owner.id,
                    status=_parcel_status_for(case, rng),
                    geom=random_point_wkt(district_name, rng),
                )
            )
            key = (case.id, owner.id)
            area_by_case_owner[key] = round(area_by_case_owner.get(key, 0.0) + area_ha, 4)

        owners_by_case[case.id] = [p for p in candidates if p.id in owner_ids]

    session.flush()
    return owners_by_case, area_by_case_owner


def generate_affected_families(
    session,
    cases: list[Case],
    owners_by_case: dict[int, list[Person]],
    people: list[Person],
    rng: random.Random,
) -> dict[int, list[Person]]:
    """Write one AffectedFamily row per affected household per case.

    Returns case.id -> the landless households among them, so R&R
    entitlements go to exactly the households already identified as
    affected and the two tables can never disagree.

    Landowners are known from land records at notification. Landless
    households — tenant farmers, labourers — are what the Social Impact
    Assessment exists to find, so they appear from that stage onward.
    """
    landless_by_village: dict[int, list[Person]] = {}
    for person in people:
        if not person.has_land_title:
            landless_by_village.setdefault(person.village_id, []).append(person)

    landless_by_case: dict[int, list[Person]] = {}
    for case in cases:
        for owner in owners_by_case.get(case.id, []):
            session.add(AffectedFamily(case_id=case.id, person_id=owner.id, is_landowner=True))

        landless_here: list[Person] = []
        if STAGE_ORDER.index(case.stage) >= SIA_INDEX:
            pool = landless_by_village.get(case.village_id, [])
            sample_size = min(len(pool), rng.randint(2, 5))
            landless_here = rng.sample(pool, sample_size) if sample_size else []
            for person in landless_here:
                session.add(
                    AffectedFamily(case_id=case.id, person_id=person.id, is_landowner=False)
                )
        landless_by_case[case.id] = landless_here

    session.flush()
    return landless_by_case


def generate_compensation_and_rnr(
    session,
    cases: list[Case],
    owners_by_case: dict[int, list[Person]],
    area_by_case_owner: dict[tuple[int, int], float],
    landless_by_case: dict[int, list[Person]],
    rng: random.Random,
    anchor: date,
) -> None:
    for case in cases:
        stage_index = STAGE_ORDER.index(case.stage)
        owners = owners_by_case.get(case.id, [])

        if stage_index >= AWARD_INDEX:
            rate_per_ha = rng.randint(*c.COMPENSATION_RATE_PER_HA_RANGE)
            for owner in owners:
                owned_ha = area_by_case_owner.get((case.id, owner.id), 0.0)
                amount_awarded = int(round(owned_ha * rate_per_ha))
                # PAID or AWARDED-but-unpaid. Nothing is left at PENDING or
                # ASSESSED once the award stage is reached, so the
                # award_unpaid rule keys cleanly off AWARDED.
                status = rng.choices(
                    [CompensationStatus.PAID, CompensationStatus.AWARDED], weights=[65, 35], k=1
                )[0]
                session.add(
                    Compensation(
                        case_id=case.id,
                        person_id=owner.id,
                        amount_awarded=amount_awarded,
                        amount_paid=amount_awarded if status is CompensationStatus.PAID else 0,
                        status=status,
                        # Recent enough not to trip award_unpaid by accident.
                        awarded_on=anchor - timedelta(days=rng.randint(0, 20)),
                    )
                )

        if stage_index >= RNR_INDEX:
            # A case at possession or beyond should show R&R settled — the
            # law expects resettlement done before displacement. Leaving
            # the baseline complete means possession_before_rnr fires only
            # on the case anomalies.py deliberately breaks.
            if stage_index >= POSSESSION_INDEX:
                choices, weights = [RnRStatus.COMPLETED], [100]
            else:
                choices = [RnRStatus.PENDING, RnRStatus.IN_PROGRESS, RnRStatus.COMPLETED]
                weights = [30, 40, 30]

            for person in owners + landless_by_case.get(case.id, []):
                session.add(
                    RnRRecord(
                        case_id=case.id,
                        person_id=person.id,
                        status=rng.choices(choices, weights=weights, k=1)[0],
                        entitlement=rng.choice(ref.RNR_ENTITLEMENTS),
                        updated_on=case.stage_changed_at - timedelta(days=rng.randint(0, 10)),
                    )
                )
    session.flush()


def generate_required_documents(session) -> None:
    for stage, doc_types in ref.REQUIRED_DOCUMENTS.items():
        for doc_type in doc_types:
            session.add(RequiredDocument(stage=stage, doc_type=doc_type))
    session.flush()


def generate_documents(session, cases: list[Case], rng: random.Random, anchor: date) -> None:
    """Every case gets its current stage's required documents, complete.
    Gaps are anomalies.py's job alone."""
    for case in cases:
        for doc_type in ref.REQUIRED_DOCUMENTS.get(case.stage, []):
            uploaded_on = min(
                anchor, case.stage_changed_at + timedelta(days=rng.randint(0, 5))
            )
            session.add(
                Document(
                    case_id=case.id,
                    doc_type=doc_type,
                    filename=f"{doc_type.value}_{case.case_number.replace('/', '_')}.pdf",
                    stored_name=f"seed_{case.id}_{doc_type.value}.pdf",
                    content_type="application/pdf",
                    size_bytes=rng.randint(80_000, 2_400_000),
                    uploaded_by_user_id=None,
                    uploaded_on=uploaded_on,
                )
            )
    session.flush()


def generate_objections(
    session, cases: list[Case], people: list[Person], rng: random.Random, anchor: date
) -> None:
    eligible = [case for case in cases if STAGE_ORDER.index(case.stage) >= OBJECTION_INDEX]
    if not eligible:
        return

    people_by_village: dict[int, list[Person]] = {}
    for person in people:
        people_by_village.setdefault(person.village_id, []).append(person)

    for _ in range(rng.randint(*c.OBJECTION_COUNT_RANGE)):
        case = rng.choice(eligible)
        person = rng.choice(people_by_village.get(case.village_id) or people)

        filed_on = min(anchor, case.stage_changed_at + timedelta(days=rng.randint(0, 8)))
        status = rng.choices(
            [ObjectionStatus.RESOLVED, ObjectionStatus.REJECTED, ObjectionStatus.UNDER_REVIEW],
            weights=[55, 20, 25],
            k=1,
        )[0]
        responded_on = None
        response = None
        if status in (ObjectionStatus.RESOLVED, ObjectionStatus.REJECTED):
            responded_on = min(anchor, filed_on + timedelta(days=rng.randint(3, 18)))
            response = (
                "Objection considered at the hearing; boundary re-verified and record updated."
                if status is ObjectionStatus.RESOLVED
                else "Objection considered and rejected; valuation upheld per the approved rate."
            )

        session.add(
            Objection(
                case_id=case.id,
                person_id=person.id,
                grounds=rng.choice(ref.OBJECTION_GROUNDS),
                status=status,
                filed_on=filed_on,
                response=response,
                responded_on=responded_on,
            )
        )
    session.flush()
