from sqlalchemy import case, func

from app.models import AffectedFamily


def compute_families(db, case_ids: list[int]) -> dict:
    """KPI 3 - affected families.

    Counts households, which is broader than landowners: a tenant
    farmer's household is affected while holding no title to any parcel.
    The landowner split is returned alongside because that is the number
    people reach for by mistake, and showing both makes the distinction
    visible instead of something we have to explain out loud.
    """
    if not case_ids:
        return {
            "affected_families_count": 0,
            "affected_families_landowner_count": 0,
            "affected_families_landless_count": 0,
        }

    total, landowners = db.query(
        func.count(AffectedFamily.id),
        func.coalesce(func.sum(case((AffectedFamily.is_landowner, 1), else_=0)), 0),
    ).filter(AffectedFamily.case_id.in_(case_ids)).one()

    return {
        "affected_families_count": int(total),
        "affected_families_landowner_count": int(landowners),
        "affected_families_landless_count": int(total) - int(landowners),
    }
