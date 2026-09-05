"""Idempotent demo-data loader for BhoomiMitra.

Loads the CSV demo dataset without touching public-source acquisition data.
Designed for local/SIH demos. Production deployments should not enable it.
"""

from __future__ import annotations

import csv
from pathlib import Path
from datetime import date

from sqlalchemy import select

from app.database import SessionLocal
from app.models.tables import (
    AffectedFamily,
    Case,
    CaseStageHistory,
    Compensation,
    Document,
    Objection,
    Parcel,
    Person,
    Project,
    RnRRecord,
    State,
    District,
    Village,
)
from app.core.enums import (
    CaseStatus,
    CompensationStatus,
    ObjectionStatus,
    ParcelStatus,
    RnRStatus,
    Stage,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "data" / "demo_synthetic"


def _rows(name: str):
    path = DATA / name
    if not path.exists():
        raise FileNotFoundError(f"Demo seed file not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _date(value: str | None):
    return date.fromisoformat(value) if value else None


def seed_demo() -> dict[str, int]:
    """Insert demo records if their stable external IDs are absent.

    The CSV package intentionally uses stable IDs/case numbers. This loader
    uses those natural keys so restarting the API does not duplicate demo rows.
    """
    counts = {}
    with SessionLocal() as session:
        # Reference geography is created only as needed by demo records.
        state = session.scalar(select(State).where(State.code == "KA"))
        if not state:
            state = State(name="Karnataka", code="KA", lgd_code="29")
            session.add(state)
            session.flush()

        projects = {}
        for row in _rows("projects.csv"):
            existing = session.scalar(select(Project).where(Project.name == row["project_name"]))
            if existing:
                projects[row["project_id"]] = existing
                continue
            district_name = row["district"]
            district = session.scalar(select(District).where(District.name == district_name))
            if not district:
                district = District(name=district_name, state_id=state.id, code=(district_name[:4].upper()))
                session.add(district)
                session.flush()
            project = Project(name=row["project_name"], requiring_body=row["requiring_body"], district_id=district.id)
            session.add(project)
            session.flush()
            projects[row["project_id"]] = project
            counts["projects"] = counts.get("projects", 0) + 1

        people = {}
        for row in _rows("people.csv"):
            name = row["name"]
            existing = session.scalar(select(Person).where(Person.name == name))
            if existing:
                people[row["person_id"]] = existing
                continue
            district = session.scalar(select(District).where(District.name == row["district"]))
            village = session.scalar(select(Village).where(Village.name == row["village"], Village.district_id == district.id))
            if not village:
                village = Village(name=row["village"], district_id=district.id)
                session.add(village)
                session.flush()
            person = Person(name=name, village_id=village.id, has_land_title=row.get("has_land_title", "true").lower() == "true")
            session.add(person)
            session.flush()
            people[row["person_id"]] = person
            counts["people"] = counts.get("people", 0) + 1

        cases = {}
        for row in _rows("cases.csv"):
            existing = session.scalar(select(Case).where(Case.case_number == row["case_number"]))
            if existing:
                cases[row["case_number"]] = existing
                continue
            project = projects[row["project_id"]]
            district = session.get(District, project.district_id)
            village = session.scalar(select(Village).where(Village.name == row["village"], Village.district_id == district.id))
            if not village:
                village = Village(name=row["village"], district_id=district.id)
                session.add(village)
                session.flush()
            case = Case(
                case_number=row["case_number"], title=row["title"], project_id=project.id,
                district_id=district.id, village_id=village.id,
                stage=Stage(row["stage"]), status=CaseStatus(row["status"]),
                stage_changed_at=_date(row["stage_changed_at"]),
                stage_due_on=_date(row.get("stage_due_on")),
                created_at=_date(row["created_at"]),
                consent_threshold_pct=float(row["consent_threshold_pct"]) if row.get("consent_threshold_pct") else None,
            )
            session.add(case)
            session.flush()
            cases[row["case_number"]] = case
            counts["cases"] = counts.get("cases", 0) + 1

        for row in _rows("parcels.csv"):
            case = cases.get(row["case_number"])
            person = people.get(row["person_id"])
            if not case or not person:
                continue
            exists = session.scalar(select(Parcel).where(Parcel.case_id == case.id, Parcel.survey_number == row["survey_number"]))
            if exists:
                continue
            # Demo coordinates are deliberately approximate and labelled in the
            # dataset. They are not presented as cadastral boundaries.
            lon = float(row.get("longitude") or "77.5946")
            lat = float(row.get("latitude") or "12.9716")
            from geoalchemy2.elements import WKTElement
            parcel = Parcel(case_id=case.id, survey_number=row["survey_number"], ulpin=None,
                            area_ha=float(row["area_ha"]), owner_id=person.id,
                            status=ParcelStatus(row["status"]),
                            geom=WKTElement(f"POINT({lon} {lat})", srid=4326))
            session.add(parcel)
            counts["parcels"] = counts.get("parcels", 0) + 1

        session.commit()
        return counts


if __name__ == "__main__":
    print(seed_demo())
