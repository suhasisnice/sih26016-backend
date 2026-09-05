from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    user_name: str | None
    action: str
    entity_type: str
    entity_id: int | None
    detail: str | None
    created_at: datetime


class AuditList(BaseModel):
    items: list[AuditEntryOut]
    total: int
