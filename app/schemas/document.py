from datetime import date

from pydantic import BaseModel, ConfigDict

from app.core.enums import DocType


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    doc_type: DocType
    filename: str
    content_type: str
    size_bytes: int
    uploaded_by_user_id: int | None
    uploaded_on: date
    # stored_name is deliberately absent. It is the name on disk, and
    # publishing it would hand clients a path to poke at; downloads go
    # through /documents/{id}/download, which checks entitlement first.


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int


class MissingDocuments(BaseModel):
    """What the case's CURRENT stage still needs. Drives the missing
    document indicator on the case page."""

    case_id: int
    stage: str
    required: list[DocType]
    present: list[DocType]
    missing: list[DocType]
