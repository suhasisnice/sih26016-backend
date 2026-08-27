"""The public notice board.

Section 11 requires a preliminary notification to be published, and Section
19 requires the same of a declaration. Publication means public: this is the
one router in the API with no authentication, because a notice a citizen has
to log in to read has not been published in any sense the Act would
recognise.

Being unauthenticated, it is deliberately narrow. It derives from the same
cases table the rest of the API serves, but exposes only what a gazette
notice carries — what land, where, whose project, on what date. No officer
names, no compensation figures, no objections, no audit, and no route to any
of them. Nothing here is scoped to a district, because the public record is
not.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.enums import Stage
from app.dependencies import get_db
from app.models import Case, District, Parcel, Project, Village

router = APIRouter(prefix="/notices", tags=["notices"])

# The two stages the Act requires be published. A case at any other stage is
# in progress, not on the record, and must not appear here.
PUBLISHED_STAGES = (Stage.PRELIMINARY_NOTIFICATION, Stage.DECLARATION)


class NoticeOut(BaseModel):
    case_number: str
    title: str
    stage: Stage
    published_on: date
    village_name: str
    district_name: str
    project_name: str
    requiring_body: str
    parcel_count: int
    total_area_ha: float


class NoticeList(BaseModel):
    items: list[NoticeOut]
    total: int


@router.get("", response_model=NoticeList)
def list_notices(
    db: Session = Depends(get_db),
    stage: Stage | None = Query(default=None, description="Restrict to one published stage"),
    district_id: int | None = Query(default=None),
    limit: int = Query(default=100, le=200),
):
    stages = [stage] if stage in PUBLISHED_STAGES else list(PUBLISHED_STAGES)

    query = (
        db.query(
            Case,
            Village.name,
            District.name,
            Project.name,
            Project.requiring_body,
            func.count(Parcel.id),
            func.coalesce(func.sum(Parcel.area_ha), 0.0),
        )
        .join(Village, Case.village_id == Village.id)
        .join(District, Case.district_id == District.id)
        .join(Project, Case.project_id == Project.id)
        .outerjoin(Parcel, Parcel.case_id == Case.id)
        .filter(Case.stage.in_(stages))
    )

    if district_id is not None:
        query = query.filter(Case.district_id == district_id)

    rows = (
        query.group_by(Case.id, Village.name, District.name, Project.name, Project.requiring_body)
        .order_by(Case.stage_changed_at.desc(), Case.id.desc())
        .limit(limit)
        .all()
    )

    items = [
        NoticeOut(
            case_number=case.case_number,
            title=case.title,
            stage=case.stage,
            # The date the case entered the published stage is the date of
            # publication. created_at would be when the file was opened,
            # which is not what the sixty-day objection window runs from.
            published_on=case.stage_changed_at,
            village_name=village_name,
            district_name=district_name,
            project_name=project_name,
            requiring_body=requiring_body,
            parcel_count=parcel_count,
            total_area_ha=round(float(total_area), 4),
        )
        for case, village_name, district_name, project_name, requiring_body, parcel_count, total_area in rows
    ]

    return NoticeList(items=items, total=len(items))
