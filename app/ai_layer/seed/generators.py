"""People, cases, parcels and everything that hangs off a case: affected
families, objections, compensation, R&R, documents and statutory notices.

One case is built end-to-end by `build_case`, which walks the case forward
from stage zero to its assigned current stage, dating every transition
along the way. That is what gives `case_stage_history` a real distribution
of stage durations for `app.ai_layer.predict` to learn from, instead of
rows with no relationship to each other.
"""

import random
from datetime import date, timedelta
from typing import Callable

from app.ai_layer.constants import (
    COMPENSATION_RATE_PER_HA_RANGE,
    DISPLACED_FRACTION_LANDLESS,
    DISPLACED_FRACTION_LANDOWNER,
    FAKE_PHONE_PREFIX,
    LANDLESS_AFFECTED_FRACTION,
    PARCEL_AREA_HA_RANGE,
)
import app.ai_layer.seed.geo as geo
from app.ai_layer.seed.reference import REQUIRED_DOCUMENTS, SYNTHETIC_PROVENANCE
from app.core.enums import (
    CaseStatus,
    CompensationStatus,
    NoticeType,
    ObjectionStatus,
    ParcelStatus,
    RnRStatus,
    Stage,
)
from app.models import (
    AffectedFamily,
    Case,
    CaseStageHistory,
    Compensation,
    Document,
    FundDeposit,
    Objection,
    Parcel,
    Person,
    RnRRecord,
    StatutoryNotice,
    Village,
)
from app.services import numbering, sla

STAGE_ORDER = list(Stage)

FIRST_NAMES_KA = [
    "Manjunath", "Siddaraju", "Nagaraj", "Puttaswamy", "Shivakumar", "Chandrashekar",
    "Basavaraj", "Ramesh", "Suresh", "Krishnappa", "Venkatesh", "Mahadevappa",
    "Lakshmamma", "Gowramma", "Nagamma", "Puttamma", "Yashodamma", "Rathnamma",
    "Savitha", "Chandrika", "Vijayalakshmi", "Anitha", "Kavitha", "Sujatha",
]
LAST_NAMES_KA = [
    "Gowda", "Reddy", "Naidu", "Setty", "Hegde", "Naik", "Poojary", "Achar",
    "Shetty", "Rao", "Iyengar", "Urs",
]

FIRST_NAMES_GENERIC = [
    "Rajesh", "Sanjay", "Vikram", "Anil", "Deepak", "Prakash", "Meena", "Sunita",
    "Geeta", "Kavita", "Pooja", "Ramesh",
]
LAST_NAMES_GENERIC = ["Patil", "Sharma", "Deshmukh", "Kulkarni", "Pillai", "Yadav"]

SURVEY_LETTERS = ["", "A", "B", "1A", "2B"]


def random_person_name(rng: random.Random, in_karnataka: bool) -> str:
    first_pool = FIRST_NAMES_KA if in_karnataka else FIRST_NAMES_GENERIC
    last_pool = LAST_NAMES_KA if in_karnataka else LAST_NAMES_GENERIC
    return f"{rng.choice(first_pool)} {rng.choice(last_pool)}"


def make_people(
    db, rng: random.Random, villages_by_district: dict[str, list[Village]], karnataka_districts: set[str], count: int
) -> list[Person]:
    """`count` people, distributed across every district's villages, with a
    fixed fraction carrying no land title -- the households the R&R figures
    depend on to be more than a synonym for landowners."""
    people: list[Person] = []
    all_villages = [(d, v) for d, vs in villages_by_district.items() for v in vs]
    for i in range(count):
        district_name, village = rng.choice(all_villages)
        in_karnataka = district_name in karnataka_districts
        person = Person(
            name=random_person_name(rng, in_karnataka),
            village_id=village.id,
            phone=f"{FAKE_PHONE_PREFIX}{i:05d}",
            has_land_title=rng.random() >= LANDLESS_AFFECTED_FRACTION,
            **SYNTHETIC_PROVENANCE,
        )
        db.add(person)
        people.append(person)
    db.flush()
    return people


def _survey_number(rng: random.Random) -> str:
    return f"{rng.randint(1, 320)}/{rng.randint(1, 9)}{rng.choice(SURVEY_LETTERS)}"


def _parcel_status_for_stage(stage: Stage, rng: random.Random) -> ParcelStatus:
    index = STAGE_ORDER.index(stage)
    award_index = STAGE_ORDER.index(Stage.AWARD)
    possession_index = STAGE_ORDER.index(Stage.POSSESSION)
    if index < award_index:
        return ParcelStatus.NOTIFIED
    if index < possession_index:
        return ParcelStatus.UNDER_ACQUISITION
    if index == possession_index:
        return rng.choice(
            [ParcelStatus.ACQUIRED, ParcelStatus.ACQUIRED, ParcelStatus.POSSESSION_TAKEN]
        )
    return ParcelStatus.POSSESSION_TAKEN


def build_case(
    db,
    rng: random.Random,
    *,
    project,
    district,
    village: Village,
    stage_index: int,
    sequence_provider: Callable[[object, int], int],
    people_pool: list[Person],
    sla_table: dict,
    as_of: date,
    parcel_count: int,
    force_stalled_days: int | None = None,
    force_breach: bool = False,
    force_missing_docs: bool = False,
    force_unpaid_award: bool = False,
    force_possession_before_rnr: bool = False,
    force_objection_open: bool = False,
    force_no_fund_deposit: bool = False,
) -> Case:
    stage = STAGE_ORDER[stage_index]

    # Walk the case forward from stage zero, dating each transition so the
    # history is a real, orderable sequence rather than independent rows.
    transition_dates: list[date] = []
    # Duration in the CURRENT stage: short for a healthy case (well inside
    # any stage's SLA), forced long for the stalled anomalies.
    if force_stalled_days is not None:
        days_in_current = force_stalled_days
    else:
        days_in_current = rng.randint(0, 8)
    cursor = as_of - timedelta(days=days_in_current)
    transition_dates.insert(0, cursor)

    for earlier_index in range(stage_index - 1, -1, -1):
        earlier_stage = STAGE_ORDER[earlier_index]
        target_days = (sla_table.get(earlier_stage) or sla_table[earlier_stage])["standard_days"]
        duration = max(3, round(target_days * rng.uniform(0.55, 1.15)))
        cursor = cursor - timedelta(days=duration)
        transition_dates.insert(0, cursor)

    created_at = transition_dates[0]
    stage_changed_at = transition_dates[-1]

    stage_due_on = sla.due_date_for(stage, stage_changed_at, sla_table)
    if force_breach:
        stage_due_on = as_of - timedelta(days=rng.randint(3, 20))

    year = created_at.year
    sequence = sequence_provider(district, year)
    case_number = numbering.build_case_number(district.state.code, district.code, year, sequence)

    # Sec. 2(2): 70% for a PPP undertaking, 80% for a private company, no
    # threshold at all for a government project. Nothing in the schema
    # distinguishes project ownership type, so this is assigned per case
    # rather than derived -- about a third of cases get each behaviour.
    consent_roll = rng.random()
    consent_threshold_pct = 70.0 if consent_roll < 0.33 else 80.0 if consent_roll < 0.66 else None

    case = Case(
        case_number=case_number,
        title=f"{project.name} — {village.name}",
        project_id=project.id,
        district_id=district.id,
        village_id=village.id,
        stage=stage,
        status=CaseStatus.ACTIVE if stage_index < len(STAGE_ORDER) - 1 else CaseStatus.CLOSED,
        stage_changed_at=stage_changed_at,
        stage_due_on=stage_due_on,
        created_at=created_at,
        consent_threshold_pct=consent_threshold_pct,
        **SYNTHETIC_PROVENANCE,
    )
    db.add(case)
    db.flush()

    # --- stage history ---
    previous_stage = None
    for index, changed_on in enumerate(transition_dates):
        db.add(
            CaseStageHistory(
                case_id=case.id,
                from_stage=previous_stage,
                to_stage=STAGE_ORDER[index],
                changed_on=changed_on,
                note="Seed data",
            )
        )
        previous_stage = STAGE_ORDER[index]

    # --- parcels + owners ---
    owners: list[Person] = rng.sample(people_pool, k=min(parcel_count, len(people_pool)))
    parcel_status = _parcel_status_for_stage(stage, rng)
    if force_possession_before_rnr:
        parcel_status = ParcelStatus.POSSESSION_TAKEN

    parcels: list[Parcel] = []
    for i in range(parcel_count):
        owner = owners[i % len(owners)]
        lat, lon = geo.random_point(rng, district.name)
        area_ha = round(rng.uniform(*PARCEL_AREA_HA_RANGE), 4)
        parcel = Parcel(
            case_id=case.id,
            survey_number=_survey_number(rng),
            area_ha=area_ha,
            owner_id=owner.id,
            status=parcel_status,
            geom=geo.point_ewkt(lat, lon),
            boundary=geo.small_square_ewkt(lat, lon),
            **SYNTHETIC_PROVENANCE,
        )
        db.add(parcel)
        parcels.append(parcel)
    db.flush()

    # ULPIN coverage is partial in real life -- DILRMP has not reached every
    # parcel yet -- so only a majority, not all, get one. Derived from the
    # parcel's own id, which is unique and known only after the flush above,
    # so uniqueness across the whole seed run is automatic.
    for parcel in parcels:
        if rng.random() < 0.7:
            parcel.ulpin = f"IN{parcel.id:012d}"

    db.flush()

    # --- affected families: every owner, plus a landless share on top ---
    landowner_ids = {p.id for p in owners}
    families: list[AffectedFamily] = []
    # Consent is meaningful only when the case's project needs it. A
    # threshold-bearing case gets a realistic spread of given/not-given
    # rather than uniformly full or empty consent, so the figure has
    # somewhere real to sit relative to the threshold.
    consent_given_probability = 0.65 if case.consent_threshold_pct is not None else 0.0

    for person in owners:
        is_displaced = rng.random() < DISPLACED_FRACTION_LANDOWNER
        family = AffectedFamily(
            case_id=case.id,
            person_id=person.id,
            is_landowner=True,
            is_displaced=is_displaced,
            consent_given=rng.random() < consent_given_probability,
        )
        db.add(family)
        families.append(family)

    landless_candidates = [p for p in people_pool if p.id not in landowner_ids]
    landless_count = max(1, round(len(owners) * 0.6))
    for person in rng.sample(landless_candidates, k=min(landless_count, len(landless_candidates))):
        is_displaced = rng.random() < DISPLACED_FRACTION_LANDLESS
        family = AffectedFamily(
            case_id=case.id,
            person_id=person.id,
            is_landowner=False,
            is_displaced=is_displaced,
            consent_given=rng.random() < consent_given_probability,
        )
        db.add(family)
        families.append(family)

    # The possession-before-R&R anomaly needs at least one genuinely
    # displaced family to have something outstanding to flag -- without
    # this, a case could roll no displaced families at all and the rule
    # would find nothing to fire on.
    if force_possession_before_rnr and not any(f.is_displaced for f in families):
        families[0].is_displaced = True

    db.flush()

    # --- documents: everything required through every completed stage ---
    for index in range(stage_index + 1):
        completed_stage = STAGE_ORDER[index]
        if force_missing_docs and completed_stage == stage:
            continue  # the anomaly: current stage's paperwork not yet on file
        doc_date = transition_dates[index]
        for position, doc_type in enumerate(REQUIRED_DOCUMENTS.get(completed_stage, [])):
            db.add(
                Document(
                    case_id=case.id,
                    doc_type=doc_type,
                    filename=f"{doc_type.value}.pdf",
                    stored_name=f"seed/{case.case_number}/{completed_stage.value}-{position}.pdf",
                    content_type="application/pdf",
                    size_bytes=rng.randint(40_000, 900_000),
                    uploaded_on=doc_date,
                    version=1,
                    is_current=True,
                    sha256=None,
                )
            )

    # --- objections: only once the case has reached the objection stage ---
    objection_period_index = STAGE_ORDER.index(Stage.OBJECTION_PERIOD)
    if stage_index >= objection_period_index and (force_objection_open or rng.random() < 0.55):
        window_start = transition_dates[objection_period_index]
        # The window an objection could have been filed in: up to "now" if
        # the case is still IN the objection period, otherwise the full
        # span the case actually spent there.
        window_end = as_of if stage_index == objection_period_index else transition_dates[objection_period_index + 1]
        span = max((window_end - window_start).days, 1)
        filed_on = window_start + timedelta(days=rng.randint(0, span))
        filer = rng.choice(owners)

        if force_objection_open:
            # The anomaly: still unresolved well past OBJECTION_RESPONSE_DAYS,
            # regardless of how far the case itself has since moved on --
            # which is precisely why this alert carries legal weight.
            filed_on = as_of - timedelta(days=rng.randint(25, 45))
            status = rng.choice([ObjectionStatus.FILED, ObjectionStatus.UNDER_REVIEW])
            responded_on = None
            response = None
        elif stage_index == objection_period_index:
            status = rng.choice([ObjectionStatus.FILED, ObjectionStatus.UNDER_REVIEW])
            responded_on = None
            response = None
        else:
            status = rng.choice([ObjectionStatus.RESOLVED, ObjectionStatus.REJECTED])
            responded_on = filed_on + timedelta(days=rng.randint(5, 20))
            response = "Objection considered and disposed as per Section 15 hearing."

        db.add(
            Objection(
                case_id=case.id,
                person_id=filer.id,
                grounds="Compensation quantum and survey boundary dispute.",
                status=status,
                filed_on=filed_on,
                response=response,
                responded_on=responded_on,
            )
        )

    # --- compensation: once the award stage has been reached ---
    award_index = STAGE_ORDER.index(Stage.AWARD)
    if stage_index >= award_index:
        award_date = transition_dates[award_index]
        rate = rng.uniform(*COMPENSATION_RATE_PER_HA_RANGE)
        total_amount = 0
        for parcel, owner in zip(parcels, owners):
            amount_awarded = round(parcel.area_ha * rate)
            if force_unpaid_award:
                amount_paid = 0
                comp_status = CompensationStatus.AWARDED
            elif stage_index == award_index:
                amount_paid = round(amount_awarded * rng.uniform(0.0, 0.4))
                comp_status = CompensationStatus.AWARDED if amount_paid else CompensationStatus.ASSESSED
            else:
                amount_paid = amount_awarded
                comp_status = CompensationStatus.PAID
            db.add(
                Compensation(
                    case_id=case.id,
                    person_id=owner.id,
                    amount_awarded=amount_awarded,
                    amount_paid=amount_paid,
                    status=comp_status,
                    awarded_on=award_date,
                )
            )
            total_amount += amount_awarded

        db.add(
            StatutoryNotice(
                case_id=case.id,
                notice_type=NoticeType.AWARD,
                section_reference="Section 23",
                gazette_number=f"KA-GAZ-{case.id:05d}-AWD",
                issuing_authority=f"{district.name} SLAO",
                issued_on=award_date,
                beneficiary_count=len(owners),
                total_amount=total_amount,
            )
        )

        # The requiring body's deposit, dated a little after the award.
        # Left off roughly one case in five (and always for the deliberate
        # anomaly) so fund_deposit_missing has real, not universal, ground
        # to fire on -- an office where every award is instantly funded
        # would not need the alert at all.
        if not force_unpaid_award and not force_no_fund_deposit and rng.random() < 0.8:
            db.add(
                FundDeposit(
                    case_id=case.id,
                    amount=total_amount,
                    deposited_on=award_date + timedelta(days=rng.randint(3, 25)),
                    reference=f"CHALLAN/{case.case_number.replace('/', '-')}",
                )
            )

    # --- R&R: once the case reaches the R&R stage ---
    rnr_index = STAGE_ORDER.index(Stage.REHABILITATION_RESETTLEMENT)
    if stage_index >= rnr_index:
        rnr_start = transition_dates[rnr_index]
        for family in families:
            if not family.is_displaced:
                continue
            if force_possession_before_rnr:
                status = rng.choice([RnRStatus.PENDING, RnRStatus.IN_PROGRESS])
            elif stage_index == rnr_index:
                status = rng.choice([RnRStatus.PENDING, RnRStatus.IN_PROGRESS])
            else:
                status = rng.choice(
                    [RnRStatus.COMPLETED, RnRStatus.COMPLETED, RnRStatus.IN_PROGRESS]
                )
            db.add(
                RnRRecord(
                    case_id=case.id,
                    person_id=family.person_id,
                    status=status,
                    entitlement=rng.choice(["Alternate land", "House site", "Job training", "Annuity"]),
                    updated_on=rnr_start + timedelta(days=rng.randint(0, 30)),
                )
            )

    # --- the rest of the statutory notices ---
    db.add(
        StatutoryNotice(
            case_id=case.id,
            notice_type=NoticeType.PRELIMINARY_NOTIFICATION,
            section_reference="Section 11(1)",
            gazette_number=f"KA-GAZ-{case.id:05d}-PN",
            issuing_authority=f"{district.name} Collectorate",
            issued_on=transition_dates[0],
        )
    )
    declaration_index = STAGE_ORDER.index(Stage.DECLARATION)
    if stage_index >= declaration_index:
        db.add(
            StatutoryNotice(
                case_id=case.id,
                notice_type=NoticeType.DECLARATION,
                section_reference="Section 19",
                gazette_number=f"KA-GAZ-{case.id:05d}-DECL",
                issuing_authority=f"{district.name} Collectorate",
                issued_on=transition_dates[declaration_index],
            )
        )
    possession_index = STAGE_ORDER.index(Stage.POSSESSION)
    if stage_index >= possession_index:
        db.add(
            StatutoryNotice(
                case_id=case.id,
                notice_type=NoticeType.POSSESSION_NOTICE,
                section_reference="Section 38",
                gazette_number=f"KA-GAZ-{case.id:05d}-POSS",
                issuing_authority=f"{district.name} Collectorate",
                issued_on=transition_dates[possession_index],
            )
        )

    return case
