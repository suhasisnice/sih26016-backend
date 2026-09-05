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
    """Who can be on the system.

    The first six are district-and-below. REQUIRING_BODY, STATE_OFFICER and
    MINISTRY_OFFICER were added for the proposal workflow: a proposal is
    submitted by the body that wants the land, scrutinised by the state, and
    sanctioned centrally, so each tier needs an account of its own. Without
    them the "approval" in "submission, verification, approval" would just be
    the same district officer signing their own paperwork.
    """
    LANDOWNER = "landowner"
    FIELD_OFFICER = "field_officer"
    SLAO = "slao"
    RNR_OFFICER = "rnr_officer"
    DISTRICT_OFFICER = "district_officer"
    # Submits proposals. Sees its own proposals and the cases they became,
    # and nothing else — a requiring body is a petitioner, not an officer.
    REQUIRING_BODY = "requiring_body"
    # Scrutinises proposals for one state; sees every case in that state.
    STATE_OFFICER = "state_officer"
    # Sanctions proposals; reads nationally, writes nothing operational.
    MINISTRY_OFFICER = "ministry_officer"
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


class ProposalStatus(str, Enum):
    """Where a proposal sits in the approval chain.

    DRAFT and SUBMITTED belong to the requiring body; UNDER_SCRUTINY to the
    state; the three terminal values to the ministry. RETURNED is distinct
    from REJECTED on purpose: returned means "fix this and resubmit", which
    is by far the commonest real outcome, and collapsing it into rejected
    would lose the difference between a correctable defect and a refusal.
    """
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_SCRUTINY = "under_scrutiny"
    RETURNED = "returned"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class NoticeType(str, Enum):
    """The published instruments the Act requires, in issue order.

    These are what "notifications issued" and "awards declared" actually
    count. Before this existed both numbers had to be inferred from a case's
    CURRENT stage, so a case that moved past declaration stopped counting as
    ever having been notified — which is the opposite of what a cumulative
    figure means.
    """
    PRELIMINARY_NOTIFICATION = "preliminary_notification"   # s.11
    DECLARATION = "declaration"                             # s.19
    AWARD = "award"                                         # s.23
    POSSESSION_NOTICE = "possession_notice"                 # s.38


class TimelineStatus(str, Enum):
    """How a case is tracking against its stage deadline.

    Derived, never stored on the case: it is a function of today's date, so
    a stored copy would be stale the morning after it was written.
    """
    ON_TIME = "on_time"
    AT_RISK = "at_risk"
    BREACHED = "breached"


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"


class RiskBand(str, Enum):
    """Output of the predictive layer. Bands rather than a bare score,
    because a raw 0.61 invites false precision from a model built on a few
    hundred historical transitions."""
    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    SEVERE = "severe"


class MutationStatus(str, Enum):
    """Where a push to the state land-record portal stands. Distinct from
    the read-only LandRecordsProvider lookups (Sec. 12 verification) — this
    is the write direction: telling the revenue record that the government
    now owns the parcel, after possession."""
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
