"""Statutory notices — the published instruments under the Act.

The public list at GET /notices stays deliberately narrow (see the router).
These schemas cover the authenticated side: issuing an instrument, and the
register of what has been issued for a case.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import NoticeType


class StatutoryNoticeCreate(BaseModel):
    case_id: int
    notice_type: NoticeType
    section_reference: str = Field(min_length=1, max_length=40)
    issuing_authority: str = Field(min_length=3, max_length=160)
    gazette_number: str | None = Field(default=None, max_length=60)
    issued_on: date | None = None
    document_id: int | None = None
    # Award notices only. Validated in the route against the notice type,
    # because "beneficiaries on a preliminary notification" is meaningless.
    beneficiary_count: int | None = Field(default=None, ge=0)
    total_amount: int | None = Field(default=None, ge=0)


class StatutoryNoticeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    case_number: str | None = None
    notice_type: NoticeType
    section_reference: str
    gazette_number: str | None
    issuing_authority: str
    issued_on: date
    document_id: int | None
    issued_by_user_id: int | None
    beneficiary_count: int | None
    total_amount: int | None


class StatutoryNoticeList(BaseModel):
    items: list[StatutoryNoticeOut]
    total: int
