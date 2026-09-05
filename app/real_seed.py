"""Idempotent loader for verified public-source land-acquisition records.

This layer intentionally stays separate from operational Case/Parcel records.
Public schedules often lack geometry and verified payment figures, so the
loader never invents coordinates, compensation, ULPINs, phone numbers or bank
information. It imports exactly what is present in the curated CSV layer.

Run from the backend directory with:
    python -m app.real_seed
"""
from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import PublicAcquisitionRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DATA = REPO_ROOT / "data" / "real_acquisition_seed"


def rows(path: Path):
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def nullable_float(value: str | None) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


def nullable_int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(float(value)) if value else None


def seed_real() -> dict[str, int]:
    counts = {"projects": 0, "parcels": 0, "compensation": 0}
    project_rows = rows(REAL_DATA / "projects.csv")
    parcel_rows = rows(REAL_DATA / "parcels.csv")

    with SessionLocal() as session:
        for row in project_rows:
            external_id = row["project_id"]
            existing = session.scalar(
                select(PublicAcquisitionRecord).where(
                    PublicAcquisitionRecord.external_id == external_id
                )
            )
            if existing:
                continue

            record_type = "compensation" if row["status"] == "compensation_reported" else "project"
            record = PublicAcquisitionRecord(
                external_id=external_id,
                record_type=record_type,
                project_id=row["project_id"],
                project_name=row["project_name"],
                department=row["department"],
                implementing_agency=row["implementing_agency"],
                district=row["district"],
                taluk=row["taluk"],
                village=row["village"],
                notification_type=row["notification_type"],
                notification_no=row["notification_no"],
                notification_date=row["notification_date"] or None,
                status=row["status"],
                area_ha=nullable_float(row["area_ha"]),
                area_acres=nullable_float(row["area_acres"]),
                compensation_awarded=nullable_int(row["compensation_awarded"]),
                compensation_paid=nullable_int(row["compensation_paid"]),
                payment_status=row["payment_status"] or None,
                owner_data_status=row["owner_data_status"] or None,
                source=row["source"],
                source_reference=row["source_reference"],
                is_verified_public=True,
            )
            session.add(record)
            counts["compensation" if record_type == "compensation" else "projects"] += 1

        for row in parcel_rows:
            external_id = row["parcel_id"]
            existing = session.scalar(
                select(PublicAcquisitionRecord).where(
                    PublicAcquisitionRecord.external_id == external_id
                )
            )
            if existing:
                continue

            record = PublicAcquisitionRecord(
                external_id=external_id,
                record_type="parcel",
                project_id=row["project_id"],
                case_number_public=row["case_id"],
                district=row["district"],
                taluk=row["taluk"],
                village=row["village"],
                survey_number=row["survey_number"],
                land_type=row["land_type"] or None,
                nature_of_land=row["nature_of_land"] or None,
                area_ha=nullable_float(row["area_ha"]),
                area_acres=nullable_float(row["area_acres"]),
                owner_name_public=row["owner_name_public"] or None,
                owner_data_status=row["owner_details_status"] or None,
                status=row["acquisition_status"],
                compensation_awarded=nullable_int(row["compensation_awarded"]),
                compensation_paid=nullable_int(row["compensation_paid"]),
                payment_status=row["payment_status"] or None,
                source_reference=row["source_reference"],
                is_verified_public=True,
            )
            session.add(record)
            counts["parcels"] += 1

        session.commit()

    return counts


if __name__ == "__main__":
    print("BhoomiMitra real public-source seed:", seed_real())
