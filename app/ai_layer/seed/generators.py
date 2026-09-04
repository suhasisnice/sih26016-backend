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
from app.ai_layer.seed.geo import case_site, parcel_polygon_wkt, parcel_positions, rng_for
from app.core.enums import (
    CaseStatus,
    CompensationStatus,
    DocType,
    NoticeType,
    ObjectionStatus,
    ParcelStatus,
    ProposalStatus,
    RnRStatus,
    Role,
    Stage,
)
from app.core.security import hash_password
from app.services.numbering import build_case_number, build_proposal_number
from app.services import sla as sla_service
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
    Proposal,
    ProposalReview,
    RequiredDocument,
    RnRRecord,
    StageSla,
    State,
    StatutoryNotice,
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


def generate_states(session) -> dict[str, State]:
    """The primary demo state plus a few secondaries.

    More than one state on purpose: a national dashboard demonstrated on a
    single state demonstrates nothing, and the state filter, the rollup and
    the state-prefixed case numbers all need a second row to be believable.

    LGD codes are the real ones. They are the identifier every other Indian
    government system joins states and districts on, so seeding the genuine
    values is what makes an integration a mapping exercise rather than a
    name-matching heuristic.
    """
    states: dict[str, State] = {}

    primary = State(
        name=c.STATE, code=c.STATE_CODE, lgd_code=c.STATE_LGD, is_union_territory=False
    )
    session.add(primary)
    states[c.STATE] = primary

    for name, code, lgd, is_ut, _districts in c.SECONDARY_STATES:
        state = State(name=name, code=code, lgd_code=lgd, is_union_territory=is_ut)
        session.add(state)
        states[name] = state

    session.flush()
    return states


def generate_districts(session, states: dict[str, State]) -> dict[str, District]:
    districts = {}
    for name in c.DISTRICT_NAMES:
        district = District(
            name=name,
            state_id=states[c.STATE].id,
            code=ref.DISTRICT_ABBR[name],
            lgd_code=c.DISTRICT_LGD.get(name),
        )
        session.add(district)
        districts[name] = district

    for state_name, _code, _lgd, _is_ut, district_specs in c.SECONDARY_STATES:
        for district_name, district_code, district_lgd in district_specs:
            district = District(
                name=district_name,
                state_id=states[state_name].id,
                code=district_code,
                lgd_code=district_lgd,
            )
            session.add(district)
            districts[district_name] = district

    session.flush()
    return districts


def generate_stage_sla(session) -> int:
    """Stage deadlines, without which timeline adherence has no denominator."""
    added = sla_service.seed_defaults(session)
    session.flush()
    return added


def generate_villages(session, districts: dict[str, District]) -> dict[str, Village]:
    """Villages for every district, LGD-coded.

    Karnataka's are real place names from reference.py. The secondary states
    get generated names — they exist to make the national rollup real, and
    inventing plausible-looking real village names for states we have not
    checked would be worse than an obviously synthetic label.
    """
    villages = {}
    lgd_seq = 100_000

    for district_name, village_names in ref.DISTRICT_VILLAGES.items():
        for village_name in village_names:
            lgd_seq += 1
            village = Village(
                name=village_name,
                district_id=districts[district_name].id,
                lgd_code=str(lgd_seq),
            )
            session.add(village)
            villages[village_name] = village

    for _state_name, _code, _lgd, _is_ut, district_specs in c.SECONDARY_STATES:
        for district_name, _dc, _dl in district_specs:
            for index in range(1, 3):
                lgd_seq += 1
                village_name = f"{district_name} Block {index}"
                village = Village(
                    name=village_name,
                    district_id=districts[district_name].id,
                    lgd_code=str(lgd_seq),
                )
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

    # One project per secondary-state district, so those districts have
    # something for their cases to belong to.
    for _state_name, _code, _lgd, _is_ut, district_specs in c.SECONDARY_STATES:
        for district_name, _dc, _dl in district_specs:
            project = Project(
                name=f"{district_name} Corridor Development",
                requiring_body="National Highways Authority of India",
                district_id=districts[district_name].id,
            )
            session.add(project)
            projects.append(project)

    session.flush()
    return projects


def generate_people(session, villages: dict[str, Village], rng: random.Random) -> list[Person]:
    people = []
    village_list = list(villages.values())
    # Enough per village that every case in every district has owners to
    # draw on — including the secondary states, which have fewer villages.
    per_village = max(8, c.PERSON_COUNT_MIN // len(village_list) + 1)
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


def generate_users(
    session,
    districts: dict[str, District],
    people: list[Person],
    states: dict[str, State],
) -> list[User]:
    """One demo login per role, so two different roles can be shown side by
    side getting genuinely different responses from the same endpoint."""
    password_hash = hash_password(c.DEMO_PASSWORD)
    bru = districts["Bengaluru Rural"].id
    tum = districts["Tumakuru"].id
    karnataka_id = states[c.STATE].id
    landowner_person = next(p for p in people if p.has_land_title)

    # (username, full_name, role, district_id, person_id, state_id, organisation)
    specs = [
        ("admin", "Anita Desai", Role.ADMIN, None, None, None, None),
        ("dc.bengaluru", "Ravi Kulkarni", Role.DISTRICT_OFFICER, bru, None, None, None),
        ("dc.tumakuru", "Meera Joshi", Role.DISTRICT_OFFICER, tum, None, None, None),
        ("slao.bengaluru", "Prakash Rao", Role.SLAO, bru, None, None, None),
        ("field.bengaluru", "Sunil Gowda", Role.FIELD_OFFICER, bru, None, None, None),
        ("rnr.bengaluru", "Latha Shetty", Role.RNR_OFFICER, bru, None, None, None),
        ("landowner", landowner_person.name, Role.LANDOWNER, None, landowner_person.id, None, None),
        # The three tiers the proposal workflow needs. Without accounts for
        # them the approval chain cannot be demonstrated end to end, which is
        # the whole point of having built it.
        ("state.karnataka", "Vikram Hegde", Role.STATE_OFFICER, None, None, karnataka_id, None),
        ("ministry", "Sanjay Menon", Role.MINISTRY_OFFICER, None, None, None, None),
        (
            "nhai",
            "NHAI Project Cell",
            Role.REQUIRING_BODY,
            None,
            None,
            None,
            "National Highways Authority of India",
        ),
        (
            "kiadb",
            "KIADB Land Cell",
            Role.REQUIRING_BODY,
            None,
            None,
            None,
            "Karnataka Industrial Area Development Board",
        ),
    ]

    users = []
    for username, full_name, role, district_id, person_id, state_id, organisation in specs:
        user = User(
            username=username,
            full_name=full_name,
            password_hash=password_hash,
            role=role,
            district_id=district_id,
            state_id=state_id,
            organisation=organisation,
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
    states: dict[str, State],
    rng: random.Random,
    anchor: date,
) -> list[Case]:
    """Cases across every state, numbered with their own state's prefix.

    Case numbers used to hardcode "KA/". They now take the prefix from the
    state row, which is what makes MH/PUN/2026/001 and KA/BRU/2026/001 able
    to coexist in one table — and what makes the platform national rather
    than one state's system with a national dashboard bolted on.
    """
    total_cases = rng.randint(*c.CASE_COUNT_RANGE)
    stages = list(STAGE_WEIGHTS.keys())
    weights = list(STAGE_WEIGHTS.values())

    district_by_id = {d.id: d for d in districts.values()}
    district_name_by_id = {d.id: name for name, d in districts.items()}
    state_code_by_id = {st.id: st.code for st in states.values()}

    villages_by_district_id: dict[int, list[Village]] = {}
    for village in villages.values():
        villages_by_district_id.setdefault(village.district_id, []).append(village)

    seq_by_district: dict[int, int] = {}
    sla_table = sla_service.load_sla(session)

    def build_case(project: Project, district: District) -> Case:
        village_pool = villages_by_district_id.get(district.id, [])
        if not village_pool:
            return None
        village = rng.choice(village_pool)
        stage = rng.choices(stages, weights=weights, k=1)[0]

        seq_by_district[district.id] = seq_by_district.get(district.id, 0) + 1
        case_number = build_case_number(
            state_code_by_id[district.state_id],
            district.code,
            2026,
            seq_by_district[district.id],
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
            district_id=district.id,
            village_id=village.id,
            stage=stage,
            status=CaseStatus.CLOSED if stage is Stage.MONITORING else CaseStatus.ACTIVE,
            stage_changed_at=stage_changed_at,
            created_at=created_at,
        )
        # Every case gets a deadline at birth. Without one it reads as
        # "untracked" on the adherence tile, and a dashboard where most
        # cases are untracked is not measuring anything.
        sla_service.apply_due_date(session, case, sla_table)
        return case

    cases = []
    karnataka_projects = [
        p for p in projects if district_by_id[p.district_id].state_id == states[c.STATE].id
    ]
    for _ in range(total_cases):
        project = rng.choice(karnataka_projects)
        case = build_case(project, district_by_id[project.district_id])
        if case is not None:
            session.add(case)
            cases.append(case)

    # A handful in each secondary state, so the national rollup and the state
    # filter have something real behind them.
    for project in projects:
        district = district_by_id[project.district_id]
        if district.state_id == states[c.STATE].id:
            continue
        for _ in range(rng.randint(*c.SECONDARY_CASES_PER_DISTRICT)):
            case = build_case(project, district)
            if case is not None:
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
    villages: dict[str, Village],
    rng: random.Random,
) -> tuple[dict[int, list[Person]], dict[tuple[int, int], float]]:
    """Returns case.id -> its unique owners, and (case.id, owner.id) -> the
    hectares that owner holds in the case, so compensation can be priced off
    land actually owned rather than a second unrelated random number."""
    district_name_by_id = {d.id: name for name, d in districts.items()}
    village_name_by_id = {v.id: name for name, v in villages.items()}

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

        # One site per case, with the case's parcels laid out contiguously
        # around it, on the farmland outside the case's OWN village.
        #
        # Previously every parcel took an independent random point anywhere in
        # the district. That was wrong twice over: the plots of a single
        # acquisition sat up to 40 km apart, so "click a project and see its
        # plots" showed a scatter rather than a corridor or a block; and a
        # uniform draw inside a district box lands in reservoirs, which put
        # parcels on open water the moment there was a real basemap under
        # them. Anchoring on the village fixes both, and makes the geometry
        # agree with the village_id the case already carries.
        # Geometry runs off a generator private to this case rather than the
        # shared one. Rejection sampling against the water list consumes a
        # variable number of draws, so on the shared rng one exclusion shifted
        # every later case's position, area and survey number — which made
        # "fix the parcels that landed in water" a game of whack-a-mole that
        # could not converge. See geo.rng_for.
        geo_rng = rng_for(case.case_number)
        village_name = village_name_by_id[case.village_id]
        site_lat, site_lon = case_site(village_name, district_name, geo_rng)
        positions = parcel_positions(site_lat, site_lon, parcel_count, geo_rng)

        owner_ids = set()
        for (parcel_lat, parcel_lon) in positions:
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
                    geom=f"SRID=4326;POINT({parcel_lon} {parcel_lat})",
                    # Scaled to area_ha, so ST_Area on this polygon returns
                    # the hectares the dashboard is totalling for the parcel.
                    boundary=parcel_polygon_wkt(parcel_lat, parcel_lon, area_ha, geo_rng),
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
            session.add(
                AffectedFamily(
                    case_id=case.id,
                    person_id=owner.id,
                    is_landowner=True,
                    # A landowner is displaced only if a dwelling stood on
                    # the acquired parcel, which is the minority case — most
                    # lose farmland and keep their house.
                    is_displaced=rng.random() < c.DISPLACED_FRACTION_LANDOWNER,
                )
            )

        landless_here: list[Person] = []
        if STAGE_ORDER.index(case.stage) >= SIA_INDEX:
            pool = landless_by_village.get(case.village_id, [])
            sample_size = min(len(pool), rng.randint(2, 5))
            landless_here = rng.sample(pool, sample_size) if sample_size else []
            for person in landless_here:
                session.add(
                    AffectedFamily(
                        case_id=case.id,
                        person_id=person.id,
                        is_landowner=False,
                        # Landless households are displaced far more often:
                        # their dwelling is typically ON the acquired land,
                        # which is precisely why the Act treats affected and
                        # displaced as two different figures.
                        is_displaced=rng.random() < c.DISPLACED_FRACTION_LANDLESS,
                    )
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
    Gaps are anomalies.py's job alone.

    A minority are filed twice, so the repository's version control is
    visible in seeded data rather than only reachable by uploading something
    during a demo. A superseded document keeps its row, its place in the
    trail and its bytes; only is_current moves. Corrected award copies and
    re-issued survey maps are the commonest real example, which is why the
    revision is attached to a document type rather than sprinkled at random.

    **Every extra draw comes off a per-document generator, never the shared
    one.** The revision decision and the replacement's date and size are
    derived from the case number and doc type, so this function consumes
    exactly the same values from `rng` as it did before revisions existed.
    That matters more than it looks: the shared stream also decides which
    proposals get approved, and drawing two extra numbers here re-rolled that
    far downstream — one sanctioned proposal produced a case with no
    documents at all, which showed up as a fourth missing-document alert that
    anomalies.py had not asked for. Seeded flaws are anomalies.py's job
    alone; a generator that quietly adds one has broken the only property
    that makes the alert counts mean anything.
    """
    # Types where a corrected re-issue is ordinary rather than remarkable.
    REVISABLE = {DocType.AWARD_COPY, DocType.SURVEY_MAP, DocType.LAND_RECORD}

    def _document(
        case, doc_type, uploaded_on, version, is_current, size_bytes, supersedes_id=None
    ):
        return Document(
            case_id=case.id,
            doc_type=doc_type,
            filename=(
                f"{doc_type.value}_{case.case_number.replace('/', '_')}"
                f"{'' if version == 1 else f'_rev{version}'}.pdf"
            ),
            stored_name=f"seed_{case.id}_{doc_type.value}_v{version}.pdf",
            content_type="application/pdf",
            size_bytes=size_bytes,
            uploaded_by_user_id=None,
            uploaded_on=uploaded_on,
            version=version,
            is_current=is_current,
            supersedes_id=supersedes_id,
            # Seeded documents have no real bytes on disk, so they have no
            # hash. Left null rather than filled with a plausible-looking
            # fake: a checksum that does not match anything is worse than an
            # absent one.
            sha256=None,
        )

    for case in cases:
        for doc_type in ref.REQUIRED_DOCUMENTS.get(case.stage, []):
            # These two draws, in this order, are what the original filed —
            # they stay on the shared generator so the stream is unchanged.
            uploaded_on = min(
                anchor, case.stage_changed_at + timedelta(days=rng.randint(0, 5))
            )
            size_bytes = rng.randint(80_000, 2_400_000)

            doc_rng = rng_for(f"docrev:{case.case_number}:{doc_type.value}")
            revised = doc_type in REVISABLE and doc_rng.random() < 0.35
            if not revised:
                session.add(_document(case, doc_type, uploaded_on, 1, True, size_bytes))
                continue

            # The original goes in first and is flushed, so the replacement
            # can point at a real id — supersedes_id is the link that makes
            # the chain navigable rather than a list of rows that happen to
            # share a doc_type.
            original = _document(case, doc_type, uploaded_on, 1, False, size_bytes)
            session.add(original)
            session.flush()
            session.add(
                _document(
                    case,
                    doc_type,
                    min(anchor, uploaded_on + timedelta(days=doc_rng.randint(4, 40))),
                    2,
                    True,
                    doc_rng.randint(80_000, 2_400_000),
                    supersedes_id=original.id,
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
