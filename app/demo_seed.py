"""Idempotent synthetic demo-data loader for BhoomiMitra.

This loader is separate from the public-source acquisition layer. It creates
fictional people/workflow rows for SIH demos and never overwrites public data.
Run from the backend directory with: python -m app.demo_seed
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from geoalchemy2.elements import WKTElement
from sqlalchemy import select

from app.core.enums import CaseStatus, ParcelStatus, Stage
from app.database import SessionLocal
from app.models.tables import Case, District, Parcel, Person, Project, State, Village

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DATA = REPO_ROOT / "data" / "demo_synthetic"
REAL_DATA = REPO_ROOT / "data" / "real_acquisition_seed"


def rows(path: Path):
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def as_date(value: str | None):
    return date.fromisoformat(value) if value else None


def get_or_create_district(session, state: State, name: str) -> District:
    district = session.scalar(select(District).where(District.name == name))
    if district:
        return district
    base = "".join(ch for ch in name.upper() if ch.isalnum())[:4] or "DEMO"
    code = base
    n = 2
    while session.scalar(select(District).where(District.code == code)):
        code = f"{base[:3]}{n}"
        n += 1
    district = District(name=name, state_id=state.id, code=code)
    session.add(district)
    session.flush()
    return district


def get_or_create_village(session, district: District, name: str) -> Village:
    village = session.scalar(select(Village).where(Village.name == name, Village.district_id == district.id))
    if village:
        return village
    village = Village(name=name, district_id=district.id)
    session.add(village)
    session.flush()
    return village


def seed_demo() -> dict[str, int]:
    counts: dict[str, int] = {}
    with SessionLocal() as session:
        state = session.scalar(select(State).where(State.code == "KA"))
        if not state:
            state = State(name="Karnataka", code="KA", lgd_code="29")
            session.add(state)
            session.flush()

        real_projects = {r["project_id"]: r for r in rows(REAL_DATA / "projects.csv")}
        demo_cases = rows(DEMO_DATA / "cases.csv")
        projects: dict[str, Project] = {}

        for case_row in demo_cases:
            pid = case_row["public_project_id"]
            if pid in projects:
                continue
            source = real_projects.get(pid)
            if not source:
                continue
            district_name = case_row["district"] if source["district"] == "Multi-district" else source["district"]
            district = get_or_create_district(session, state, district_name)
            project = session.scalar(select(Project).where(Project.name == source["project_name"]))
            if not project:
                project = Project(name=source["project_name"], requiring_body=source["implementing_agency"], district_id=district.id)
                session.add(project)
                session.flush()
                counts["projects"] = counts.get("projects", 0) + 1
            projects[pid] = project

        people: dict[str, Person] = {}
        for row in rows(DEMO_DATA / "people.csv"):
            existing = session.scalar(select(Person).where(Person.name == row["display_name"]))
            if existing:
                people[row["demo_person_id"]] = existing
                continue
            matching = next((c for c in demo_cases if c["village"] == row["village"]), None)
            district = get_or_create_district(session, state, matching["district"] if matching else "Bengaluru Urban")
            village = get_or_create_village(session, district, row["village"])
            person = Person(name=row["display_name"], village_id=village.id, has_land_title=row["has_land_title"].lower() == "true")
            session.add(person)
            session.flush()
            people[row["demo_person_id"]] = person
            counts["people"] = counts.get("people", 0) + 1

        cases: dict[str, Case] = {}
        for row in demo_cases:
            existing = session.scalar(select(Case).where(Case.case_number == row["case_number"]))
            if existing:
                cases[row["case_number"]] = existing
                continue
            project = projects.get(row["public_project_id"])
            if not project:
                continue
            district = get_or_create_district(session, state, row["district"])
            village = get_or_create_village(session, district, row["village"])
            case = Case(
                case_number=row["case_number"], title=row["title"], project_id=project.id,
                district_id=district.id, village_id=village.id, stage=Stage(row["stage"]),
                status=CaseStatus(row["status"]), stage_changed_at=as_date(row["stage_changed_at"]),
                stage_due_on=as_date(row.get("stage_due_on")), created_at=as_date(row["created_at"]),
                consent_threshold_pct=float(row["consent_threshold_pct"]) if row.get("consent_threshold_pct") else None,
            )
            session.add(case)
            session.flush()
            cases[row["case_number"]] = case
            counts["cases"] = counts.get("cases", 0) + 1

        # The synthetic package has no cadastral parcel CSV. Create one demo
        # point per case so map/detail screens work. The coordinate is generic
        # and must never be interpreted as an actual owner's property location.
        people_list = list(people.values())
        for index, row in enumerate(demo_cases):
            case = cases.get(row["case_number"])
            if not case or session.scalar(select(Parcel).where(Parcel.case_id == case.id)):
                continue
            owner = people_list[index % len(people_list)]
            parcel = Parcel(
                case_id=case.id, survey_number=f"DEMO-{index + 1:03d}", ulpin=None,
                area_ha=0.25 + (index % 5) * 0.05, owner_id=owner.id,
                status=ParcelStatus.POSSESSION_TAKEN if row["stage"] in {"possession", "monitoring"} else ParcelStatus.UNDER_ACQUISITION,
                geom=WKTElement("POINT(77.5946 12.9716)", srid=4326),
            )
            session.add(parcel)
            counts["parcels"] = counts.get("parcels", 0) + 1

        session.commit()
    return counts


if __name__ == "__main__":
    print("BhoomiMitra demo seed:", seed_demo())
