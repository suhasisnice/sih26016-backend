"""Where a record's data actually came from — see DataSource's docstring in
app.core.enums. One shape, nested into every *Out schema for an entity that
carries the five provenance columns, the same way CompensationOut and RnROut
nest into AffectedPersonOut rather than flattening their fields onto it.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.core.enums import DataSource, ProvenanceStatus


class ProvenanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    data_source: DataSource
    source_name: str | None
    source_url: str | None
    retrieved_at: date | None
    provenance_status: ProvenanceStatus
