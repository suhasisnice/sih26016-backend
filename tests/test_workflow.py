"""Tests for the case-stage state machine's legal gates.

These exist because the state machine is the one place this whole system's
claim to legal correctness actually lives — a judge asking "how do you know
an officer can't skip a stage" deserves a green test run, not just a
confident answer.
"""

import pytest
from fastapi import HTTPException

from app.core.enums import CaseStatus, ObjectionStatus, Role, Stage, SurveyTaskStatus
from app.models import CaseStageHistory
from app.services import workflow


# ---------------------------------------------------------------------
# Pure functions — no database at all, so these are the cheapest possible
# check that the Act's nine-stage order is encoded correctly.
# ---------------------------------------------------------------------

def test_allowed_transitions_first_stage_has_no_backward_move():
    allowed = workflow.allowed_transitions(Stage.PRELIMINARY_NOTIFICATION)
    assert allowed == [Stage.SOCIAL_IMPACT_ASSESSMENT]


def test_allowed_transitions_last_stage_has_no_forward_move():
    allowed = workflow.allowed_transitions(workflow.TERMINAL_STAGE)
    assert allowed == [workflow.STAGE_ORDER[-2]]


def test_allowed_transitions_middle_stage_allows_both_neighbours():
    allowed = workflow.allowed_transitions(Stage.DECLARATION)
    index = workflow.STAGE_ORDER.index(Stage.DECLARATION)
    assert set(allowed) == {workflow.STAGE_ORDER[index - 1], workflow.STAGE_ORDER[index + 1]}


def test_next_stage_returns_none_at_the_terminal_stage():
    assert workflow.next_stage(workflow.TERMINAL_STAGE) is None


def test_stage_order_matches_the_documented_nine_stages():
    # If this ever drifts, every dashboard aggregate and the SLA table
    # silently start describing a different Act than the one deployed.
    assert len(workflow.STAGE_ORDER) == 9
    assert workflow.STAGE_ORDER[0] is Stage.PRELIMINARY_NOTIFICATION
    assert workflow.STAGE_ORDER[-1] is Stage.MONITORING


# ---------------------------------------------------------------------
# advance_case — the DB-touching gates. Each test's real assertion is
# "raises HTTPException", i.e. the illegal move never reaches the database.
# ---------------------------------------------------------------------

def test_advance_case_refuses_to_skip_a_stage(db, make_case, make_user):
    case = make_case(stage=Stage.PRELIMINARY_NOTIFICATION)
    officer = make_user(Role.DISTRICT_OFFICER)

    with pytest.raises(HTTPException) as exc:
        workflow.advance_case(db, case, Stage.OBJECTION_PERIOD, officer)

    assert exc.value.status_code == 400
    assert case.stage is Stage.PRELIMINARY_NOTIFICATION  # unchanged


def test_advance_case_refuses_to_move_to_the_same_stage(db, make_case, make_user):
    case = make_case(stage=Stage.DECLARATION)
    officer = make_user(Role.DISTRICT_OFFICER)

    with pytest.raises(HTTPException) as exc:
        workflow.advance_case(db, case, Stage.DECLARATION, officer)

    assert exc.value.status_code == 400


def test_advance_case_refuses_a_stalled_case(db, make_case, make_user):
    case = make_case(stage=Stage.AWARD, status=CaseStatus.STALLED)
    officer = make_user(Role.DISTRICT_OFFICER)

    with pytest.raises(HTTPException):
        workflow.advance_case(db, case, Stage.REHABILITATION_RESETTLEMENT, officer)


def test_advance_case_blocks_declaration_with_an_open_objection(
    db, make_case, make_user, make_objection
):
    case = make_case(stage=Stage.OBJECTION_PERIOD)
    make_objection(case, ObjectionStatus.FILED)
    officer = make_user(Role.DISTRICT_OFFICER)

    with pytest.raises(HTTPException) as exc:
        workflow.advance_case(db, case, Stage.DECLARATION, officer)

    assert "objection" in exc.value.detail.lower()
    assert case.stage is Stage.OBJECTION_PERIOD


def test_advance_case_allows_declaration_once_the_objection_is_resolved(
    db, make_case, make_user, make_objection
):
    case = make_case(stage=Stage.OBJECTION_PERIOD)
    make_objection(case, ObjectionStatus.RESOLVED)
    officer = make_user(Role.DISTRICT_OFFICER)

    workflow.advance_case(db, case, Stage.DECLARATION, officer)

    assert case.stage is Stage.DECLARATION


def test_advance_case_blocks_leaving_verification_with_an_open_survey_task(
    db, make_case, make_user, make_survey_task
):
    case = make_case(stage=Stage.LAND_VERIFICATION)
    make_survey_task(case, SurveyTaskStatus.IN_PROGRESS)
    officer = make_user(Role.DISTRICT_OFFICER)

    with pytest.raises(HTTPException) as exc:
        workflow.advance_case(db, case, Stage.OBJECTION_PERIOD, officer)

    assert "survey" in exc.value.detail.lower()


def test_advance_case_allows_leaving_verification_once_surveys_are_done(
    db, make_case, make_user, make_survey_task
):
    case = make_case(stage=Stage.LAND_VERIFICATION)
    make_survey_task(case, SurveyTaskStatus.APPROVED)
    officer = make_user(Role.DISTRICT_OFFICER)

    workflow.advance_case(db, case, Stage.OBJECTION_PERIOD, officer)

    assert case.stage is Stage.OBJECTION_PERIOD


def test_advance_case_writes_stage_history_and_sets_the_due_date(db, make_case, make_user):
    case = make_case(stage=Stage.PRELIMINARY_NOTIFICATION)
    officer = make_user(Role.DISTRICT_OFFICER)

    workflow.advance_case(db, case, Stage.SOCIAL_IMPACT_ASSESSMENT, officer, note="test move")

    assert case.stage is Stage.SOCIAL_IMPACT_ASSESSMENT
    assert case.stage_due_on is not None  # sla.apply_due_date ran
    assert case.status is CaseStatus.ACTIVE

    # SessionLocal runs with autoflush=False (app.database), so the history
    # row advance_case just added is only visible to a fresh query once
    # explicitly flushed.
    db.flush()
    history = db.query(CaseStageHistory).filter(CaseStageHistory.case_id == case.id).one()
    assert history.from_stage is Stage.PRELIMINARY_NOTIFICATION
    assert history.to_stage is Stage.SOCIAL_IMPACT_ASSESSMENT
    assert history.note == "test move"


def test_advance_case_closes_the_case_at_the_terminal_stage(db, make_case, make_user):
    penultimate = workflow.STAGE_ORDER[-2]
    case = make_case(stage=penultimate)
    officer = make_user(Role.DISTRICT_OFFICER)

    workflow.advance_case(db, case, workflow.TERMINAL_STAGE, officer)

    assert case.stage is workflow.TERMINAL_STAGE
    assert case.status is CaseStatus.CLOSED


# ---------------------------------------------------------------------
# can_advance — who may push a case out of its CURRENT stage. Admin,
# District Officer and SLAO administer a case end to end; a Field Officer
# or R&R Officer may only act on the one stage that is actually theirs, per
# STAGE_RESPONSIBLE_ROLE. This is the exact permission POST
# /cases/{id}/advance checks, and what the frontend's Advance Stage button
# mirrors, so a role that fails here would also 403 for real.
# ---------------------------------------------------------------------


def test_case_stage_owners_may_advance_any_stage(db, make_case, make_user):
    case = make_case(stage=Stage.REHABILITATION_RESETTLEMENT)  # not their named stage
    for role in (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO):
        officer = make_user(role)
        assert workflow.can_advance(officer, case) is True


def test_rnr_officer_may_advance_only_at_the_rnr_stage(db, make_case, make_user):
    rnr_officer = make_user(Role.RNR_OFFICER)

    rnr_case = make_case(stage=Stage.REHABILITATION_RESETTLEMENT)
    other_case = make_case(stage=Stage.AWARD)

    assert workflow.can_advance(rnr_officer, rnr_case) is True
    assert workflow.can_advance(rnr_officer, other_case) is False


def test_field_officer_may_advance_only_at_their_named_stage(db, make_case, make_user):
    """STAGE_RESPONSIBLE_ROLE names Field Officer for Land Verification
    specifically — not for every stage FIELD_OFFICER_STAGES makes visible
    to them (Social Impact Assessment and Objection Period are on-ground
    work, not stages they formally advance)."""
    field_officer = make_user(Role.FIELD_OFFICER)

    verification_case = make_case(stage=Stage.LAND_VERIFICATION)
    sia_case = make_case(stage=Stage.SOCIAL_IMPACT_ASSESSMENT)

    assert workflow.can_advance(field_officer, verification_case) is True
    assert workflow.can_advance(field_officer, sia_case) is False


def test_landowner_may_never_advance_a_case(db, make_case, make_user):
    landowner = make_user(Role.LANDOWNER, district_id=None)
    case = make_case(stage=Stage.AWARD)

    assert workflow.can_advance(landowner, case) is False
