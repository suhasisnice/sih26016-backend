"""The fifteen tables behind the API.

Conventions that hold everywhere in here, per CLAUDE.md:
- Column names are snake_case; enum values come from app.core.enums and are
  never re-declared as loose strings.
- Money is whole rupees as Integer, never Float — rounding errors in a
  compensation figure are not acceptable in a land record.
- Area is hectares as Float.
- Compensation and R&R are separate tables with separate statuses. A tenant
  farmer can be owed R&R while owning no land and receiving no
  compensation, so one merged column could not represent them.
"""

from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    AlertSeverity,
    CaseStatus,
    CompensationStatus,
    DocType,
    ObjectionStatus,
    ParcelStatus,
    RnRStatus,
    Role,
    Stage,
)
from app.database import Base


def _enum(enum_cls, name: str):
    """Store enums by VALUE (the lowercase string), not by python member
    name. Without values_callable SQLAlchemy would persist "AWARD" while
    the API returns "award", and every comparison would need translating."""
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(60), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(_enum(Role, "role"), nullable=False)
    # Officers are scoped to a district; landowners and admins are not.
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id"), nullable=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    district: Mapped["District | None"] = relationship()


class District(Base):
    __tablename__ = "districts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(80), nullable=False)
    # Short code used in case numbers (KA/BRU/2026/001). Stored rather than
    # derived from the name, so the seed and the create-case route cannot
    # abbreviate the same district two different ways.
    code: Mapped[str] = mapped_column(String(4), nullable=False, unique=True)

    villages: Mapped[list["Village"]] = relationship(back_populates="district")


class Village(Base):
    __tablename__ = "villages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"), nullable=False, index=True)

    district: Mapped[District] = relationship(back_populates="villages")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    requiring_body: Mapped[str] = mapped_column(String(120), nullable=False)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"), nullable=False, index=True)

    district: Mapped[District] = relationship()


class Person(Base):
    """An individual on record. Whether they are affected by a particular
    case lives in AffectedFamily, not here — the same person can be
    affected by one acquisition and not another."""

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    village_id: Mapped[int] = mapped_column(ForeignKey("villages.id"), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(15), nullable=True)
    has_land_title: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    village: Mapped[Village] = relationship()


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"), nullable=False, index=True)
    village_id: Mapped[int] = mapped_column(ForeignKey("villages.id"), nullable=False, index=True)
    stage: Mapped[Stage] = mapped_column(_enum(Stage, "stage"), nullable=False, index=True)
    status: Mapped[CaseStatus] = mapped_column(_enum(CaseStatus, "case_status"), nullable=False)
    # Read by every alert rule that asks "how long has this sat still".
    stage_changed_at: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[date] = mapped_column(Date, nullable=False)

    project: Mapped[Project] = relationship()
    district: Mapped[District] = relationship()
    village: Mapped[Village] = relationship()
    parcels: Mapped[list["Parcel"]] = relationship(back_populates="case")


class CaseStageHistory(Base):
    """Append-only record of every stage transition. Distinct from the
    audit log: this is the case's legal timeline, which Frontend draws as
    the stage timeline component."""

    __tablename__ = "case_stage_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    from_stage: Mapped[Stage | None] = mapped_column(_enum(Stage, "stage"), nullable=True)
    to_stage: Mapped[Stage] = mapped_column(_enum(Stage, "stage"), nullable=False)
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    changed_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class Parcel(Base):
    """One piece of land. Status is per parcel, not per case: parcels in a
    single case genuinely do not all clear together, and both the area and
    possession dashboard figures are counted parcel by parcel."""

    __tablename__ = "parcels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    survey_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    area_ha: Mapped[float] = mapped_column(Float, nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("people.id"), nullable=False, index=True)
    status: Mapped[ParcelStatus] = mapped_column(_enum(ParcelStatus, "parcel_status"), nullable=False)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=False)

    case: Mapped[Case] = relationship(back_populates="parcels")
    owner: Mapped[Person] = relationship()


# Spatial index — without it the map's bbox query does a full table scan.
Index("ix_parcels_geom", Parcel.geom, postgresql_using="gist")


class Compensation(Base):
    """Money for land taken. Never merged with RnRRecord."""

    __tablename__ = "compensation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), nullable=False, index=True)
    amount_awarded: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_paid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[CompensationStatus] = mapped_column(
        _enum(CompensationStatus, "compensation_status"), nullable=False
    )
    awarded_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    case: Mapped[Case] = relationship()
    person: Mapped[Person] = relationship()


class RnRRecord(Base):
    """Rehabilitation and resettlement entitlement. Deliberately a separate
    table from Compensation with its own status — see the module docstring."""

    __tablename__ = "rnr_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), nullable=False, index=True)
    status: Mapped[RnRStatus] = mapped_column(_enum(RnRStatus, "rnr_status"), nullable=False)
    entitlement: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_on: Mapped[date] = mapped_column(Date, nullable=False)

    case: Mapped[Case] = relationship()
    person: Mapped[Person] = relationship()


class AffectedFamily(Base):
    """One affected household per row, linked to the case affecting it.

    The problem statement counts affected FAMILIES, which is broader than
    landowners: a tenant farmer's household is affected while holding no
    parcel. Owners are reachable through parcels, but landless households
    had no link to a case at all without this table, so every case before
    the R&R stage would have counted zero affected families.

    Under the Act a household is identified as affected at the Social
    Impact Assessment, well before entitlements are processed — so these
    rows exist from that stage onward, independently of rnr_records, which
    tracks entitlement progress separately.
    """

    __tablename__ = "affected_families"
    __table_args__ = (UniqueConstraint("case_id", "person_id", name="uq_affected_family_case_person"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), nullable=False, index=True)
    is_landowner: Mapped[bool] = mapped_column(Boolean, nullable=False)

    case: Mapped["Case"] = relationship()
    person: Mapped[Person] = relationship()


class RequiredDocument(Base):
    """Which document types each stage requires. Lookup table so the
    missing-document rule has something authoritative to check against."""

    __tablename__ = "required_documents"
    __table_args__ = (UniqueConstraint("stage", "doc_type", name="uq_required_documents_stage_doc"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[Stage] = mapped_column(_enum(Stage, "stage"), nullable=False, index=True)
    doc_type: Mapped[DocType] = mapped_column(_enum(DocType, "doc_type"), nullable=False)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    doc_type: Mapped[DocType] = mapped_column(_enum(DocType, "doc_type"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Name on disk, generated by us. Never the client's filename, which is
    # attacker-controlled and would allow writing outside the upload dir.
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_on: Mapped[date] = mapped_column(Date, nullable=False)

    case: Mapped[Case] = relationship()


class Objection(Base):
    __tablename__ = "objections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), nullable=False, index=True)
    grounds: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ObjectionStatus] = mapped_column(
        _enum(ObjectionStatus, "objection_status"), nullable=False
    )
    filed_on: Mapped[date] = mapped_column(Date, nullable=False)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    case: Mapped[Case] = relationship()
    person: Mapped[Person] = relationship()


class Alert(Base):
    """Written by the AI Layer's rule runner, read by the dashboard.

    Carries case ids and counts only — never a name or phone number. Alerts
    surface on dashboards visible to roles that may not be entitled to
    person-level detail.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    rule: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    severity: Mapped[AlertSeverity] = mapped_column(
        _enum(AlertSeverity, "alert_severity"), nullable=False
    )
    message: Mapped[str] = mapped_column(String(300), nullable=False)
    detected_on: Mapped[date] = mapped_column(Date, nullable=False)
    # Rule-specific extras: days_stalled, missing_document_types, and so
    # on. Free-form because each rule has different things worth showing,
    # but counts and ids only — never a name or phone number.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    case: Mapped[Case] = relationship()


class AuditLog(Base):
    """Who did what, when. Written by app.services.audit on every mutating
    route. The problem statement asks for this directly, so it is not
    optional — and it is append-only: nothing in the API updates or deletes
    a row here."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
