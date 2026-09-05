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

    # --- version control ---
    version: int = 1
    supersedes_id: int | None = None
    is_current: bool = True
    # Published so a reviewer can verify a downloaded file against the
    # record without needing access to the server. It identifies the bytes;
    # it is not a secret and reveals nothing about the content.
    sha256: str | None = None


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int
    # How many rows are superseded versions rather than live documents, so
    # the UI can offer "show 3 earlier versions" instead of listing every
    # revision of everything by default.
    superseded_count: int = 0


class DocumentVersionHistory(BaseModel):
    """Every version of one doc_type on one case, newest first."""

    case_id: int
    doc_type: DocType
    versions: list[DocumentOut]


class MissingDocuments(BaseModel):
    """What the case's CURRENT stage still needs. Drives the missing
    document indicator on the case page."""

    case_id: int
    stage: str
    required: list[DocType]
    present: list[DocType]
    missing: list[DocType]
