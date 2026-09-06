"""Pydantic schemas — the published API contract.

Every endpoint declares an explicit response model. No route returns a bare
dict or a raw ORM object, so /docs always shows the true shape and the
other two teams can build against it without asking.

Field names are snake_case, dates serialise as ISO strings, and enum values
come from app.core.enums.
"""

from app.schemas.auth import LoginResponse, UserOut
from app.schemas.case import (
    CaseCreate,
    CaseDetail,
    CaseHoldRequest,
    CaseListItem,
    CaseResumeRequest,
    CaseStageAdvance,
    CaseStageHistoryOut,
    CaseUpdate,
    PaginatedCases,
)
from app.schemas.common import Message
from app.schemas.geo import ParcelFeature, ParcelFeatureCollection, ParcelGeometry, ParcelOut

__all__ = [
    "CaseCreate",
    "CaseDetail",
    "CaseHoldRequest",
    "CaseListItem",
    "CaseResumeRequest",
    "CaseStageAdvance",
    "CaseStageHistoryOut",
    "CaseUpdate",
    "LoginResponse",
    "Message",
    "PaginatedCases",
    "ParcelFeature",
    "ParcelFeatureCollection",
    "ParcelGeometry",
    "ParcelOut",
    "UserOut",
]
