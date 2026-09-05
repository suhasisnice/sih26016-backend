from pydantic import BaseModel, ConfigDict


class PublicAcquisitionRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    record_type: str
    project_id: str | None = None
    project_name: str | None = None
    case_number_public: str | None = None
    department: str | None = None
    implementing_agency: str | None = None
    district: str | None = None
    taluk: str | None = None
    village: str | None = None
    survey_number: str | None = None
    land_type: str | None = None
    nature_of_land: str | None = None
    notification_type: str | None = None
    notification_no: str | None = None
    notification_date: str | None = None
    status: str | None = None
    area_ha: float | None = None
    area_acres: float | None = None
    owner_name_public: str | None = None
    owner_data_status: str | None = None
    compensation_awarded: int | None = None
    compensation_paid: int | None = None
    payment_status: str | None = None
    source: str | None = None
    source_reference: str | None = None
    is_verified_public: bool
