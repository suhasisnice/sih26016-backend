"""KPI 3 -- affected families, broader than landowners, and displaced
families, which is a narrower and distinct flag under the Act.

A landowner farming an outlying plot can be affected but not displaced; a
tenant whose dwelling stands on the acquired parcel can be displaced while
owning nothing. Both cuts are reported so neither is lost.
"""

from sqlalchemy import case as sql_case
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AffectedFamily


def families_kpis(db: Session, case_ids: list[int]) -> dict:
    if not case_ids:
        return {
            "affected_families_count": 0,
            "affected_families_landowner_count": 0,
            "affected_families_landless_count": 0,
            "displaced_families_count": 0,
            "displaced_families_landless_count": 0,
        }

    total, landowner, displaced, displaced_landless = (
        db.query(
            func.count(AffectedFamily.id),
            func.coalesce(
                func.sum(sql_case((AffectedFamily.is_landowner.is_(True), 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(sql_case((AffectedFamily.is_displaced.is_(True), 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(
                    sql_case(
                        (
                            (AffectedFamily.is_displaced.is_(True))
                            & (AffectedFamily.is_landowner.is_(False)),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .filter(AffectedFamily.case_id.in_(case_ids))
        .one()
    )

    total, landowner = int(total), int(landowner)
    return {
        "affected_families_count": total,
        "affected_families_landowner_count": landowner,
        "affected_families_landless_count": total - landowner,
        "displaced_families_count": int(displaced),
        "displaced_families_landless_count": int(displaced_landless),
    }
