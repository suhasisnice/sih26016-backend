"""Project-level rollups for the Projects workspace.

Project itself (app.schemas.reference.ProjectOut) is a plain reference row
— a name, a requiring body, a district. It carries no status, no dates, no
progress of its own. Everything here is computed live from the cases under
it, the same "never store what a query can answer live" rule every other
aggregate in this system follows (see CaseDetail's consent_obtained_pct for
the same discipline).
"""

from pydantic import BaseModel

from app.core.enums import Stage, TimelineStatus
from app.schemas.provenance import ProvenanceOut


class ProjectWorkspaceOut(BaseModel):
    id: int
    name: str
    requiring_body: str
    district_id: int
    district_name: str

    case_count: int
    # Every parcel under the project's cases, regardless of status — the
    # area the project was notified for. `affected_area_ha` is the subset
    # actually acquired or possessed; the gap between the two is the
    # project's real remaining work; neither is invented from a proposal's
    # earlier estimate, which is a different, pre-survey figure.
    required_area_ha: float
    affected_area_ha: float

    # None when the project's cases sit at more than one stage — a single
    # project routinely spans several, and picking one would misstate the
    # rest. Frontend shows "Multiple stages" in that case.
    current_stage: Stage | None
    # Fraction of required_area_ha already affected, 0-100. None when the
    # project has no parcels yet (division by zero is "no data", not 0%).
    overall_progress_pct: float | None

    # Whoever most recently moved any case in this project — the closest
    # honest stand-in for "responsible officer" available; Project and Case
    # both lack an assignee column of their own.
    responsible_officer_name: str | None

    pending_action_count: int
    # Worst of the project's own cases' timeline_status — breached beats
    # at_risk beats on_time beats untracked, the same ranking
    # app.services.sla already establishes for one case.
    deadline_status: TimelineStatus
    provenance: ProvenanceOut


class ProjectWorkspaceList(BaseModel):
    items: list[ProjectWorkspaceOut]
    total: int
