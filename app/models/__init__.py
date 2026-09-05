"""Every table in the system.

Imported as a package so `from app import models` registers all of them on
Base.metadata — SQLAlchemy only knows about a model once its module has
been imported, and create_all silently skips anything it has not seen.
"""

from app.models.tables import (
    AffectedFamily,
    Alert,
    AuditLog,
    Case,
    CaseStageHistory,
    Compensation,
    District,
    Document,
    FundDeposit,
    InviteCode,
    MutationRequest,
    Notification,
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

__all__ = [
    "AffectedFamily",
    "Alert",
    "AuditLog",
    "Case",
    "CaseStageHistory",
    "Compensation",
    "District",
    "Document",
    "FundDeposit",
    "InviteCode",
    "MutationRequest",
    "Notification",
    "Objection",
    "Parcel",
    "Person",
    "Project",
    "Proposal",
    "ProposalReview",
    "RequiredDocument",
    "RnRRecord",
    "StageSla",
    "State",
    "StatutoryNotice",
    "User",
    "Village",
]
