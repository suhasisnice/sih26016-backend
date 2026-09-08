"""Default statutes and their per-stage section references.

Seeded idempotently at boot (app.main's lifespan), the same pattern
app.services.sla.seed_defaults already uses for stage_sla — config data
belongs in a service that can grow the set safely, not baked into the
migration that only shapes the table. See StatuteStageReference's
docstring in app.models.tables for what this table is for and,
deliberately, is not for.

Section references for RFCTLARR 2013 mirror sih26016-frontend's own
STAGE_SECTION table (src/lib/labels.js) — the two must agree, since both
describe the same nine stages under the same act. National Highways Act
1956 references (Sections 3A/3C/3D/3G/3E) are the ones already cited in
docs/BUILD_BRIEF.md's stage table; stages the NH Act does not mandate on
its own (a Social Impact Assessment, the Second Schedule R&R entitlement
list, an independent land-verification step) are marked not applicable
rather than given an invented citation.
"""

from sqlalchemy.orm import Session

from app.core.enums import Stage
from app.models import Statute, StatuteStageReference

RFCTLARR = "RFCTLARR_2013"
NH_ACT = "NH_ACT_1956"

STATUTES = {
    RFCTLARR: "Right to Fair Compensation and Transparency in Land Acquisition, "
    "Rehabilitation and Resettlement Act, 2013",
    NH_ACT: "National Highways Act, 1956",
}

# stage -> (is_applicable, section_reference, note)
RFCTLARR_STAGES: dict[Stage, tuple[bool, str | None, str | None]] = {
    Stage.PRELIMINARY_NOTIFICATION: (True, "Section 11", None),
    Stage.SOCIAL_IMPACT_ASSESSMENT: (True, "Sections 4–9", None),
    Stage.LAND_VERIFICATION: (True, "Section 12", None),
    Stage.OBJECTION_PERIOD: (True, "Section 15", None),
    Stage.DECLARATION: (True, "Section 19", None),
    Stage.AWARD: (True, "Sections 23–30", None),
    Stage.REHABILITATION_RESETTLEMENT: (True, "Second Schedule", None),
    Stage.POSSESSION: (True, "Section 38", None),
    Stage.MONITORING: (True, "Section 48", None),
}

NH_ACT_STAGES: dict[Stage, tuple[bool, str | None, str | None]] = {
    Stage.PRELIMINARY_NOTIFICATION: (True, "Section 3A", None),
    Stage.SOCIAL_IMPACT_ASSESSMENT: (
        False, None,
        "Not mandated under the National Highways Act itself, which is listed among the "
        "enactments in RFCTLARR's Fourth Schedule.",
    ),
    Stage.LAND_VERIFICATION: (
        False, None, "No independent verification stage of its own under this Act.",
    ),
    Stage.OBJECTION_PERIOD: (True, "Section 3C", None),
    Stage.DECLARATION: (True, "Section 3D", None),
    Stage.AWARD: (True, "Section 3G", None),
    Stage.REHABILITATION_RESETTLEMENT: (
        False, None,
        "No Second Schedule entitlement list of its own; R&R for NH Act acquisitions "
        "follows whatever scheme the requiring body separately adopts.",
    ),
    Stage.POSSESSION: (True, "Section 3E", None),
    Stage.MONITORING: (False, None, "No specific provision under this Act."),
}


def seed_defaults(db: Session) -> int:
    """Write STATUTES and their stage references for any statute code not
    already present. Idempotent, so it is safe to call on every startup.
    Returns how many statute rows it added."""
    existing = {row.code for row in db.query(Statute).all()}
    added = 0

    for code, name in STATUTES.items():
        if code in existing:
            continue
        statute = Statute(code=code, name=name)
        db.add(statute)
        db.flush()  # statute.id, for the FK below

        stages = RFCTLARR_STAGES if code == RFCTLARR else NH_ACT_STAGES
        for stage, (is_applicable, section_reference, note) in stages.items():
            db.add(
                StatuteStageReference(
                    statute_id=statute.id,
                    stage=stage,
                    is_applicable=is_applicable,
                    section_reference=section_reference,
                    note=note,
                )
            )
        added += 1

    return added
