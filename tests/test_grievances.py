"""Tests for the Grievance feature: numbering, status history, and the
same case-level entitlement every other case-related record relies on.

Grievance access itself is not re-tested here — GET/POST /grievances scope
through app.dependencies.scope_cases_to_user, the exact function
test_rbac_scoping.py already proves fails closed for a landowner and every
officer role. What is specific to grievances is the number sequence and
the status-history timeline, so those are what this file checks.
"""

from datetime import date

from app.core.enums import GrievanceCategory, GrievanceStatus, Role
from app.dependencies import scope_cases_to_user
from app.models import Case, Grievance, GrievanceStatusHistory
from app.services import numbering
from app.services.grievances import GRIEVANCE_CATEGORY_ROLE, record_status_change


def _make_grievance(db, case, person, filed_by, number):
    grievance = Grievance(
        grievance_number=number,
        case_id=case.id,
        person_id=person.id,
        filed_by_user_id=filed_by.id,
        category=GrievanceCategory.COMPENSATION,
        subject="Test subject",
        description="Test description of the complaint, long enough to pass validation.",
        status=GrievanceStatus.SUBMITTED,
        filed_on=date(2026, 1, 1),
    )
    db.add(grievance)
    db.flush()
    return grievance


# ---------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------


def test_next_grievance_number_starts_at_one_for_a_new_year(db):
    assert numbering.next_grievance_number(db, 2031) == "GRV-2031-00001"


def test_next_grievance_number_increments_from_the_highest_issued(
    db, make_case, make_person, make_user
):
    case = make_case()
    person = make_person()
    officer = make_user(Role.ADMIN, district_id=None)

    _make_grievance(db, case, person, officer, "GRV-2032-00001")
    _make_grievance(db, case, person, officer, "GRV-2032-00007")

    assert numbering.next_grievance_number(db, 2032) == "GRV-2032-00008"


def test_next_grievance_number_is_independent_per_year(db, make_case, make_person, make_user):
    case = make_case()
    person = make_person()
    officer = make_user(Role.ADMIN, district_id=None)
    _make_grievance(db, case, person, officer, "GRV-2033-00099")

    # A different year starts its own sequence, same as case numbering
    # restarts per district per year.
    assert numbering.next_grievance_number(db, 2034) == "GRV-2034-00001"


# ---------------------------------------------------------------------
# Status history / timeline
# ---------------------------------------------------------------------


def test_record_status_change_updates_status_and_writes_history(
    db, make_case, make_person, make_user
):
    case = make_case()
    person = make_person()
    officer = make_user(Role.DISTRICT_OFFICER)
    grievance = _make_grievance(db, case, person, officer, "GRV-2035-00001")

    record_status_change(db, grievance, GrievanceStatus.ASSIGNED, officer, note="Routed to SLAO")

    assert grievance.status is GrievanceStatus.ASSIGNED
    db.flush()
    history = (
        db.query(GrievanceStatusHistory)
        .filter(GrievanceStatusHistory.grievance_id == grievance.id)
        .one()
    )
    assert history.from_status is GrievanceStatus.SUBMITTED
    assert history.to_status is GrievanceStatus.ASSIGNED
    assert history.note == "Routed to SLAO"
    assert history.changed_by_user_id == officer.id


def test_record_status_change_preserves_earlier_history_rows(
    db, make_case, make_person, make_user
):
    """Append-only, same guarantee CaseStageHistory gives a case's
    timeline — a later transition must not erase an earlier one."""
    case = make_case()
    person = make_person()
    officer = make_user(Role.DISTRICT_OFFICER)
    grievance = _make_grievance(db, case, person, officer, "GRV-2036-00001")

    record_status_change(db, grievance, GrievanceStatus.ASSIGNED, officer)
    record_status_change(db, grievance, GrievanceStatus.UNDER_REVIEW, officer)
    record_status_change(db, grievance, GrievanceStatus.RESOLVED, officer, note="Paid in full")

    db.flush()
    rows = (
        db.query(GrievanceStatusHistory)
        .filter(GrievanceStatusHistory.grievance_id == grievance.id)
        .order_by(GrievanceStatusHistory.id)
        .all()
    )
    assert [r.to_status for r in rows] == [
        GrievanceStatus.ASSIGNED,
        GrievanceStatus.UNDER_REVIEW,
        GrievanceStatus.RESOLVED,
    ]
    assert grievance.status is GrievanceStatus.RESOLVED


# ---------------------------------------------------------------------
# Category-to-role mapping (display only) and case-level scoping, which
# GET /grievances relies on exactly the way objections.py does.
# ---------------------------------------------------------------------


def test_every_grievance_category_maps_to_a_role():
    for category in GrievanceCategory:
        assert category in GRIEVANCE_CATEGORY_ROLE


def test_landowner_scoping_extends_to_their_grievances_through_the_case(
    db, geography, make_case, make_person, make_user
):
    """A grievance has no visibility rule of its own — GET /grievances
    filters through scope_cases_to_user on the case, so proving a
    landowner's case entitlement here is proving their grievance
    entitlement too."""
    _, district, village = geography
    from app.models import Parcel

    person = make_person()
    case = make_case()
    db.add(
        Parcel(
            case_id=case.id,
            survey_number="1/1",
            area_ha=1.0,
            owner_id=person.id,
            status="notified",
            geom="SRID=4326;POINT(77.5 13.0)",
        )
    )
    db.flush()

    landowner = make_user(Role.LANDOWNER, district_id=None)
    landowner.person_id = person.id
    db.flush()

    other_case = make_case()

    visible_ids = {c.id for c in scope_cases_to_user(db.query(Case), landowner).all()}
    assert case.id in visible_ids
    assert other_case.id not in visible_ids
