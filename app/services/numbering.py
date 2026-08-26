"""Case number format, in one place.

Both the seed and the create-case route mint case numbers. When each had
its own copy of the format they drifted immediately — seeded cases came out
KA/BRU/2026/001 while newly created ones were KA/BR/2026/023, which looks
like two different systems in the same table.
"""

import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Case, District

CASE_NUMBER_RE = re.compile(r"^KA/(?P<code>[A-Z]{2,4})/(?P<year>\d{4})/(?P<seq>\d{3,})$")


def build_case_number(district_code: str, year: int, sequence: int) -> str:
    return f"KA/{district_code}/{year}/{sequence:03d}"


def next_case_number(db: Session, district: District, year: int) -> str:
    """Next free number for this district and year.

    Derived from the highest sequence already issued, not from a row count:
    counting would reissue a number if a case were ever removed, and
    case_number is unique, so the insert would fail rather than quietly
    duplicate.
    """
    prefix = f"KA/{district.code}/{year}/"

    # The sequence is taken in the database rather than by pulling every
    # case number into Python and scanning it — the suffix is a fixed-width
    # zero-padded number, so ordering the strings orders the sequence.
    latest = (
        db.query(Case.case_number)
        .filter(Case.district_id == district.id, Case.case_number.like(f"{prefix}%"))
        .order_by(Case.case_number.desc())
        .limit(1)
        .scalar()
    )

    highest = 0
    if latest:
        match = CASE_NUMBER_RE.match(latest)
        if match:
            highest = int(match.group("seq"))

    return build_case_number(district.code, year, highest + 1)
