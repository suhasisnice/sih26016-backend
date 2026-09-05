"""Case and proposal number formats, in one place.

Both the seed and the create routes mint numbers. When each had its own copy
of the format they drifted immediately — seeded cases came out
KA/BRU/2026/001 while newly created ones were KA/BR/2026/023, which looks
like two different systems in the same table.

The state prefix used to be the literal string "KA". It now comes from the
state row, because a platform that hardcodes one state's code into every
identifier it issues is not a national platform — it is a Karnataka platform
with a national dashboard bolted on.
"""

import re

from sqlalchemy.orm import Session

from app.models import Case, District, Proposal, State

# Two to three characters for the state (KA, DL, AN for Andaman & Nicobar),
# two to four for the district.
CASE_NUMBER_RE = re.compile(
    r"^(?P<state>[A-Z]{2,3})/(?P<code>[A-Z]{2,4})/(?P<year>\d{4})/(?P<seq>\d{3,})$"
)
PROPOSAL_NUMBER_RE = re.compile(
    r"^PROP/(?P<state>[A-Z]{2,3})/(?P<year>\d{4})/(?P<seq>\d{3,})$"
)


def build_case_number(state_code: str, district_code: str, year: int, sequence: int) -> str:
    return f"{state_code}/{district_code}/{year}/{sequence:03d}"


def build_proposal_number(state_code: str, year: int, sequence: int) -> str:
    return f"PROP/{state_code}/{year}/{sequence:04d}"


def next_case_number(db: Session, district: District, year: int) -> str:
    """Next free case number for this district and year.

    Derived from the highest sequence already issued, not from a row count:
    counting would reissue a number if a case were ever removed, and
    case_number is unique, so the insert would fail rather than quietly
    duplicate.
    """
    state_code = district.state.code
    prefix = f"{state_code}/{district.code}/{year}/"

    # The sequence is taken in the database rather than by pulling every case
    # number into Python and scanning it — the suffix is a fixed-width
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

    return build_case_number(state_code, district.code, year, highest + 1)


def next_proposal_number(db: Session, state: State, year: int) -> str:
    """Next free proposal number for this state and year.

    Numbered per state rather than per district on purpose: a proposal is
    submitted before anyone has decided which district office will handle it,
    and a number that has to change when the file moves is not an identifier.
    """
    prefix = f"PROP/{state.code}/{year}/"
    latest = (
        db.query(Proposal.proposal_number)
        .filter(Proposal.state_id == state.id, Proposal.proposal_number.like(f"{prefix}%"))
        .order_by(Proposal.proposal_number.desc())
        .limit(1)
        .scalar()
    )

    highest = 0
    if latest:
        match = PROPOSAL_NUMBER_RE.match(latest)
        if match:
            highest = int(match.group("seq"))

    return build_proposal_number(state.code, year, highest + 1)
