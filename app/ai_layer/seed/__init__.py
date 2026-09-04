"""run_seed() — the one command that resets the database to a known good
demo state. Fixed random seed, so it produces identical data every time,
right down to the primary keys.
"""

import random
from urllib.parse import urlparse

from sqlalchemy import text

from app.ai_layer import constants as c
from app.ai_layer.seed.anomalies import apply_anomalies
from app.ai_layer.seed.generators import (
    generate_cases,
    generate_compensation_and_rnr,
    generate_districts,
    generate_documents,
    generate_objections,
    generate_parcels,
    generate_people,
    generate_projects,
    generate_required_documents,
    generate_stage_history,
    generate_stage_sla,
    generate_states,
    generate_users,
    generate_affected_families,
    generate_villages,
)
from app.ai_layer.seed.pipeline import generate_proposals, generate_statutory_notices
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import (
    AffectedFamily,
    Alert,
    AuditLog,
    Case,
    CaseStageHistory,
    Compensation,
    District,
    Document,
    Notification,
    Objection,
    Parcel,
    Person,
    Project,
    Proposal,
    ProposalReview,
    RequiredDocument,
    RnRRecord,
    StageSla,
    State,
    StatutoryNotice,
    User,
    Village,
)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db", ""}

# Children before parents, so foreign keys never block the wipe.
WIPE_ORDER = (
    Alert,
    Notification,
    AuditLog,
    StatutoryNotice,
    Objection,
    Document,
    RequiredDocument,
    StageSla,
    AffectedFamily,
    RnRRecord,
    Compensation,
    Parcel,
    CaseStageHistory,
    # proposals and cases reference each other (proposal.case_id and
    # case.proposal_id), so TRUNCATE ... CASCADE handles the cycle; the
    # order here only has to be stable, not topologically perfect.
    ProposalReview,
    Proposal,
    Case,
    User,
    Person,
    Project,
    Village,
    District,
    State,
)


def _assert_local_database(allow_remote: bool) -> None:
    """Refuse to wipe a database that is not on this machine.

    run_seed() empties every table. Once DATABASE_URL points somewhere
    shared, a reflexive re-run would destroy other people's work.
    """
    if allow_remote:
        return
    host = (urlparse(settings.database_url).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise RuntimeError(
            f"Refusing to wipe and reseed the database at host '{host}': it is not local.\n"
            f"run_seed() empties every table. If you really mean to reseed this "
            f"database, re-run with --allow-remote."
        )


def _wipe(session) -> None:
    """Empty every table AND reset its id sequence.

    A plain DELETE leaves Postgres' sequences where they were, so each
    reseed hands out higher primary keys than the last and the same case
    comes back as id 13, then 157. That breaks anything remembering an id
    across a reseed — deck screenshots, a bookmarked /cases/13, the case
    number someone wrote down for the demo.

    Table names come from our own model classes, never from input; SQL
    identifiers cannot be bound parameters, so this is the only way to
    write it.
    """
    tables = ", ".join(model.__tablename__ for model in WIPE_ORDER)
    session.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))


def run_seed(rebuild: bool = False, allow_remote: bool = False) -> dict:
    _assert_local_database(allow_remote)

    if rebuild:
        # create_all cannot alter a table whose columns changed, so while
        # the schema is still moving this is how a stale database is reset.
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    rng = random.Random(c.RANDOM_SEED)
    # Resolved once so every generator measures from the same day, even
    # if the run straddles midnight.
    anchor = c.anchor_date()
    session = SessionLocal()
    try:
        _wipe(session)

        states = generate_states(session)
        districts = generate_districts(session, states)
        villages = generate_villages(session, districts)
        projects = generate_projects(session, districts)
        people = generate_people(session, villages, rng)
        users = generate_users(session, districts, people, states)
        # Stage deadlines must exist before cases, because each case gets its
        # due date at creation from this table.
        generate_stage_sla(session)
        cases = generate_cases(session, projects, districts, villages, states, rng, anchor)
        generate_stage_history(session, cases)
        owners_by_case, area_by_case_owner = generate_parcels(
            session, cases, people, districts, villages, rng
        )
        landless_by_case = generate_affected_families(session, cases, owners_by_case, people, rng)
        generate_compensation_and_rnr(
            session, cases, owners_by_case, area_by_case_owner, landless_by_case, rng, anchor
        )
        generate_required_documents(session)
        generate_documents(session, cases, rng, anchor)
        generate_objections(session, cases, people, rng, anchor)
        # Notices are built AFTER compensation, because an award notice
        # reports the beneficiary count and total the compensation rows
        # define.
        notices_written = generate_statutory_notices(session, cases, districts, rng, anchor)
        proposal_summary = generate_proposals(
            session, states, districts, villages, users, cases, rng, anchor
        )

        anomalies = apply_anomalies(session, cases, rng, anchor)

        summary = {
            "anchor_date": anchor.isoformat(),
            "states": len(states),
            "districts": len(districts),
            "villages": len(villages),
            "projects": len(projects),
            "users": len(users),
            "people": len(people),
            "cases": len(cases),
            "stage_history_rows": session.query(CaseStageHistory).count(),
            "parcels": session.query(Parcel).count(),
            "compensation_records": session.query(Compensation).count(),
            "rnr_records": session.query(RnRRecord).count(),
            "affected_families": session.query(AffectedFamily).count(),
            "required_document_rules": session.query(RequiredDocument).count(),
            "documents": session.query(Document).count(),
            "objections": session.query(Objection).count(),
            "statutory_notices": notices_written,
            "stage_sla_rows": session.query(StageSla).count(),
            **proposal_summary,
            **anomalies,
        }
        session.commit()
        return summary
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
