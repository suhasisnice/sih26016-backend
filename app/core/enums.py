"""
Single source of truth for every enum used in the API contract.

Rules (see CLAUDE.md):
- All values are lowercase strings, snake_case where multi-word.
- Nothing outside this file should hardcode a status/stage/role string.
- GET /meta/enums publishes these so Frontend never hardcodes them either.
"""

from enum import Enum


class Stage(str, Enum):
    """The nine legal stages of a case under RFCTLARR Act 2013, in order."""
    PRELIMINARY_NOTIFICATION = "preliminary_notification"
    SOCIAL_IMPACT_ASSESSMENT = "social_impact_assessment"
    LAND_VERIFICATION = "land_verification"
    OBJECTION_PERIOD = "objection_period"
    DECLARATION = "declaration"
    AWARD = "award"
    REHABILITATION_RESETTLEMENT = "rehabilitation_resettlement"
    POSSESSION = "possession"
    MONITORING = "monitoring"


class CaseStatus(str, Enum):
    ACTIVE = "active"
    STALLED = "stalled"
    CLOSED = "closed"


class ParcelStatus(str, Enum):
    """Where an individual parcel has reached.

    Tracked per parcel rather than per case because two of the five
    dashboard figures need that granularity: area notified vs acquired,
    and possession, which the problem statement counts in parcels. Parcels
    within one case do not all clear together in practice.
    """
    NOTIFIED = "notified"
    UNDER_ACQUISITION = "under_acquisition"
    ACQUIRED = "acquired"
    POSSESSION_TAKEN = "possession_taken"


class CompensationStatus(str, Enum):
    PENDING = "pending"
    ASSESSED = "assessed"
    AWARDED = "awarded"
    PAID = "paid"
    DISPUTED = "disputed"


class RnRStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISPUTED = "disputed"


class ObjectionStatus(str, Enum):
    FILED = "filed"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class AlertSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Role(str, Enum):
    LANDOWNER = "landowner"
    FIELD_OFFICER = "field_officer"
    SLAO = "slao"
    RNR_OFFICER = "rnr_officer"
    DISTRICT_OFFICER = "district_officer"
    ADMIN = "admin"


class DocType(str, Enum):
    NOTIFICATION_COPY = "notification_copy"
    GAZETTE_PUBLICATION = "gazette_publication"
    SIA_REPORT = "sia_report"
    PUBLIC_HEARING_MINUTES = "public_hearing_minutes"
    LAND_RECORD = "land_record"
    SURVEY_MAP = "survey_map"
    OWNERSHIP_PROOF = "ownership_proof"
    OBJECTION_FORM = "objection_form"
    HEARING_NOTICE = "hearing_notice"
    DECLARATION_COPY = "declaration_copy"
    AWARD_COPY = "award_copy"
    COMPENSATION_ASSESSMENT = "compensation_assessment"
    RNR_ENTITLEMENT_LIST = "rnr_entitlement_list"
    RNR_SCHEME_DOCUMENT = "rnr_scheme_document"
    POSSESSION_CERTIFICATE = "possession_certificate"
    MONITORING_REPORT = "monitoring_report"
