"""Database model for curated public-source acquisition records."""

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PublicAcquisitionRecord(Base):
    """A verified public record, kept separate from operational parcels.

    Public notices may omit geometry, ULPIN and payment confirmation. Missing
    values remain NULL rather than being invented.
    """

    __tablename__ = "public_acquisition_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    record_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    project_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    case_number_public: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(160), nullable=True)
    implementing_agency: Mapped[str | None] = mapped_column(String(180), nullable=True)
    district: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    taluk: Mapped[str | None] = mapped_column(String(100), nullable=True)
    village: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    survey_number: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    land_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    nature_of_land: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notification_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notification_no: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notification_date: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    area_ha: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_acres: Mapped[float | None] = mapped_column(Float, nullable=True)
    owner_name_public: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_data_status: Mapped[str | None] = mapped_column(String(160), nullable=True)
    compensation_awarded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compensation_paid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_verified_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
