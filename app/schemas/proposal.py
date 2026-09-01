"""The proposal contract — submission, scrutiny, sanction.

Field names mirror the model exactly so the frontend never has to translate,
and every response carries `allowed_transitions` for the calling user, so the
buttons a screen renders match what the server will actually accept. That is
a convenience only: the server re-checks the role on the way in, because a
frontend that hides a button is not access control.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ProposalStatus, Role


class ProposalCreate(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    purpose: str = Field(min_length=20, max_length=4000)
    # The village fixes the district and the district fixes the state, so
    # neither is accepted from the client — the three can never contradict
    # each other if only one of them is an input.
    village_id: int
    estimated_area_ha: float = Field(gt=0, le=100_000)
    estimated_families: int | None = Field(default=None, ge=0, le=1_000_000)
    estimated_cost: int | None = Field(default=None, ge=0)
    # Only an admin may set this; a requiring-body account submits for its own
    # organisation and the route overrides whatever is sent here.
    requiring_body: str | None = Field(default=None, max_length=120)


class ProposalUpdate(BaseModel):
    """Editable while the proposal is with the requiring body.

    Status is not here. It moves only through POST /proposals/{id}/transition,
    which checks the move against the approval chain and writes the review
    record — the same separation the case stage has, for the same reason.
    """

    title: str | None = Field(default=None, min_length=5, max_length=200)
    purpose: str | None = Field(default=None, min_length=20, max_length=4000)
    village_id: int | None = None
    estimated_area_ha: float | None = Field(default=None, gt=0, le=100_000)
    estimated_families: int | None = Field(default=None, ge=0, le=1_000_000)
    estimated_cost: int | None = Field(default=None, ge=0)


class ProposalTransition(BaseModel):
    to_status: ProposalStatus
    note: str | None = Field(default=None, max_length=500)


class ProposalReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_status: ProposalStatus | None
    to_status: ProposalStatus
    actor_user_id: int | None
    actor_name: str | None = None
    actor_role: Role | None
    note: str | None
    created_on: date


class ProposalListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_number: str
    title: str
    requiring_body: str
    status: ProposalStatus
    # Which tier is holding the file, derived from status rather than stored
    # so the two can never disagree.
    held_by: str
    state_id: int
    state_name: str
    district_id: int
    district_name: str
    village_name: str
    estimated_area_ha: float
    estimated_families: int | None
    submitted_on: date | None
    status_changed_on: date
    days_in_status: int
    case_id: int | None


class PaginatedProposals(BaseModel):
    items: list[ProposalListItem]
    total: int
    limit: int
    offset: int
    # Counts per status for the pipeline strip above the table, aggregated in
    # the database rather than by counting the page that was returned.
    by_status: dict[str, int]


class ProposalDetail(ProposalListItem):
    purpose: str
    estimated_cost: int | None
    submitted_by_user_id: int | None
    decided_by_user_id: int | None
    decided_on: date | None
    decision_note: str | None
    case_number: str | None = None
    project_id: int | None = None
    created_at: date
    reviews: list[ProposalReviewOut]
    # What the CALLING user may do next. Empty means the file is with
    # somebody else, which the UI renders as "awaiting <held_by>".
    allowed_transitions: list[ProposalStatus]
