from sqlalchemy import case, func

from app.models import AffectedFamily


def compute_families(db, case_ids: list[int]) -> dict:
    """KPI 3 - affected AND displaced families.

    Two figures, because the problem statement asks for two and the Act
    distinguishes them. Affected counts households touched by the
    acquisition, which is broader than landowners: a tenant farmer's
    household is affected while holding no title to any parcel. Displaced is
    the narrower group who lose a dwelling — a landowner farming an outlying
    plot is affected but not displaced, and a labourer whose house stands on
    the acquired land is displaced while owning nothing.

    They are never derived from one another. Reporting displacement as a
    fixed share of affected families would be a guess presented as a count.

    The landowner split is returned alongside because that is the number
    people reach for by mistake, and showing both makes the distinction
    visible instead of something we have to explain out loud.
    """
    if not case_ids:
        return {
            "affected_families_count": 0,
            "affected_families_landowner_count": 0,
            "affected_families_landless_count": 0,
            "displaced_families_count": 0,
            "displaced_families_landless_count": 0,
        }

    total, landowners, displaced, displaced_landless = db.query(
        func.count(AffectedFamily.id),
        func.coalesce(func.sum(case((AffectedFamily.is_landowner, 1), else_=0)), 0),
        func.coalesce(func.sum(case((AffectedFamily.is_displaced, 1), else_=0)), 0),
        func.coalesce(
            func.sum(
                case(
                    (
                        AffectedFamily.is_displaced & ~AffectedFamily.is_landowner,
                        1,
                    ),
                    else_=0,
                )
            ),
            0,
        ),
    ).filter(AffectedFamily.case_id.in_(case_ids)).one()

    return {
        "affected_families_count": int(total),
        "affected_families_landowner_count": int(landowners),
        "affected_families_landless_count": int(total) - int(landowners),
        "displaced_families_count": int(displaced),
        # Displaced households with no land title are the group with the
        # least recourse and the strongest R&R entitlement, so the split is
        # surfaced rather than left to be worked out from two other numbers.
        "displaced_families_landless_count": int(displaced_landless),
    }
