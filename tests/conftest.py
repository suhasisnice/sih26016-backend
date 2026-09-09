"""Shared fixtures for the test suite.

Every test opens its own session against the same database the app itself
would use (DATABASE_URL — the docker-compose Postgres by default) and rolls
its transaction back when the test ends. Nothing a test writes is ever
visible to another test or left behind in the seeded demo data.

This relies on one real thing about the code under test: `advance_case` in
app.services.workflow, like every service function in this codebase, never
commits — "the caller owns the transaction" is the module's own docstring.
A rollback here discards everything a test did, cleanly, every time.
"""

from datetime import date

import pytest

from app.core.enums import CaseStatus, Role, Stage
from app.database import SessionLocal
from app.models import Case, District, Objection, Person, Project, State, SurveyTask, User, Village


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def geography(db):
    """One state, one district in it, one village in that district — just
    deep enough for a Case or a Person to attach to."""
    state = State(name="Test State", code="TS")
    db.add(state)
    db.flush()
    district = District(name="Test District", state_id=state.id, code="TSTD")
    db.add(district)
    db.flush()
    village = Village(name="Test Village", district_id=district.id)
    db.add(village)
    db.flush()
    return state, district, village


@pytest.fixture
def project(db, geography):
    _, district, _ = geography
    p = Project(name="Test Project", requiring_body="Test Requiring Body", district_id=district.id)
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def make_user(db, geography):
    """Factory: build a User with a given role, defaulting to this
    fixture's own district/state so scoping tests can override just the
    one attribute they're testing."""
    state, district, _ = geography
    counter = {"n": 0}

    def _make(role, district_id=..., state_id=None):
        counter["n"] += 1
        user = User(
            username=f"test.user.{counter['n']}",
            full_name="Test User",
            password_hash="not-a-real-hash",
            role=role,
            district_id=district.id if district_id is ... else district_id,
            state_id=state_id,
        )
        db.add(user)
        db.flush()
        return user

    return _make


@pytest.fixture
def make_case(db, geography, project):
    """Factory: build a Case at a given stage, in the fixture geography's
    own district — override district_id to test cross-district scoping."""
    _, district, village = geography
    counter = {"n": 0}

    def _make(stage=Stage.PRELIMINARY_NOTIFICATION, status=CaseStatus.ACTIVE, district_id=...):
        counter["n"] += 1
        case = Case(
            case_number=f"TS/TSTD/2026/{counter['n']:03d}",
            title="Test acquisition",
            project_id=project.id,
            district_id=district.id if district_id is ... else district_id,
            village_id=village.id,
            stage=stage,
            status=status,
            stage_changed_at=date(2026, 1, 1),
            created_at=date(2026, 1, 1),
        )
        db.add(case)
        db.flush()
        return case

    return _make


@pytest.fixture
def make_person(db, geography):
    _, _, village = geography
    counter = {"n": 0}

    def _make():
        counter["n"] += 1
        person = Person(name=f"Test Person {counter['n']}", village_id=village.id)
        db.add(person)
        db.flush()
        return person

    return _make


@pytest.fixture
def make_objection(db, make_person):
    def _make(case, status):
        obj = Objection(
            case_id=case.id,
            person_id=make_person().id,
            grounds="Test objection",
            status=status,
            filed_on=date(2026, 1, 5),
        )
        db.add(obj)
        db.flush()
        return obj

    return _make


@pytest.fixture
def make_survey_task(db, make_user):
    def _make(case, status):
        officer = make_user(Role.FIELD_OFFICER)
        task = SurveyTask(case_id=case.id, assigned_to_user_id=officer.id, status=status)
        db.add(task)
        db.flush()
        return task

    return _make
