"""A stand-in for a state land-record portal.

Deterministic, offline, and labelled everywhere as simulated. It exists so the
integration can be demonstrated end to end without pretending to hold
credentials this project does not have.

**How it fakes an upstream.** It mirrors the parcels already in this database
and then diverges from a deterministic minority of them. That is the standard
way to stub a system of record you cannot reach, and it is the only way the
reconciliation is worth running: a mock generating land data from nothing
would disagree with the acquisition file about *everything*, which tells an
officer as little as agreeing about everything would.

**Why it disagrees on purpose.** Reconciliation that can only return "all
matched" is a green light wired to a battery. So a stable minority come back
with a different surveyed area, a different owner spelling, a pending mutation
or a registered encumbrance — the four discrepancies that actually turn up
when an acquisition file is checked against the revenue record, and each one
something an officer wants to know before an award is passed.

Which parcels differ is derived from a CRC of the survey number, so it is
identical on every run and on every machine. A demo whose findings move
between rehearsal and stage is worse than no demo.
"""

import zlib
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.integrations.base import (
    LandRecordNotFound,
    MutationAck,
    ProviderInfo,
    UpstreamLandRecord,
)
from app.models import Case, Parcel, Person, Village

CLASSIFICATIONS = ("Dry", "Wet", "Garden", "Dry - converted", "Inam")

ENCUMBRANCES = (
    "Mortgage registered in favour of a co-operative land development bank",
    "Court attachment pending in a partition suit",
    "Lease noted in favour of a tenant cultivator",
)

# Honorifics and spelling variants a revenue record carries and an
# acquisition file usually does not. These are what a real name mismatch
# looks like — not a different person, the same person written differently,
# which is exactly the case that must not be auto-resolved.
NAME_VARIANTS = (
    "{name} (alias per RTC)",
    "{first} Kumar {last}",
    "{first} {last}a",
    "Smt. {name}",
)


def _bucket(survey_number: str, salt: str, modulo: int) -> int:
    """Stable pseudo-random bucket. crc32, not hash(): Python salts string
    hashing per process, so hash() would give a different answer on every run
    and the mock would stop being reproducible."""
    return zlib.crc32(f"{salt}:{survey_number}".encode("utf-8")) % modulo


class MockLandRecordsProvider:
    """Implements the LandRecordsProvider port against local data."""

    info = ProviderInfo(
        key="mock",
        label="Mock revenue portal (simulated)",
        authority="Stand-in for a state land-records portal — not a real source",
        is_live=False,
        covers_states=("Karnataka", "Maharashtra", "Tamil Nadu", "Gujarat"),
    )

    # One survey number in this many is absent upstream, so "the portal has
    # never heard of this parcel" is demonstrable too. It is the commonest
    # real finding of the four — a subdivided parcel keeps its old number on
    # the acquisition file long after the revenue record has renumbered it.
    ABSENT_MODULO = 17

    def __init__(self, db: Session):
        self._db = db

    def fetch(self, village_lgd: str, survey_number: str) -> UpstreamLandRecord:
        row = (
            self._db.query(Parcel, Person.name)
            .join(Case, Parcel.case_id == Case.id)
            .join(Village, Case.village_id == Village.id)
            .join(Person, Parcel.owner_id == Person.id)
            .filter(Village.lgd_code == village_lgd)
            .filter(Parcel.survey_number == survey_number)
            .first()
        )
        if row is None:
            raise LandRecordNotFound(
                f"No record for survey number {survey_number} in village {village_lgd}"
            )
        if _bucket(survey_number, f"absent:{village_lgd}", self.ABSENT_MODULO) == 0:
            raise LandRecordNotFound(
                f"No record for survey number {survey_number} in village {village_lgd}"
            )

        parcel, owner_name = row
        return self._record(village_lgd, parcel, owner_name)

    def fetch_village(self, village_lgd: str) -> list[UpstreamLandRecord]:
        rows = (
            self._db.query(Parcel, Person.name)
            .join(Case, Parcel.case_id == Case.id)
            .join(Village, Case.village_id == Village.id)
            .join(Person, Parcel.owner_id == Person.id)
            .filter(Village.lgd_code == village_lgd)
            .all()
        )
        records = []
        for parcel, owner_name in rows:
            if _bucket(parcel.survey_number, f"absent:{village_lgd}", self.ABSENT_MODULO) == 0:
                continue
            records.append(self._record(village_lgd, parcel, owner_name))
        return records

    # One request in this many the mock portal "rejects" — a mutation push
    # failing (a stale ULPIN, a portal timeout) is the realistic outcome to
    # demonstrate alongside the happy path, not something a demo should
    # pretend never happens.
    FAIL_MODULO = 9

    def push_mutation(self, ulpin: str, survey_number: str) -> MutationAck:
        key = ulpin or survey_number
        if _bucket(key, "mutation_fail", self.FAIL_MODULO) == 0:
            return MutationAck(
                status="failed",
                external_ref=None,
                raw={
                    "provider": "mock",
                    "error": "PORTAL_TIMEOUT",
                    "message": "Simulated: the upstream mutation service did not respond in time.",
                },
            )
        ref = f"MUT-{_bucket(key, 'mutation_ref', 900_000):06d}"
        return MutationAck(
            status="acknowledged",
            external_ref=ref,
            raw={
                "provider": "mock",
                "external_ref": ref,
                "message": "Simulated: mutation request queued at the revenue portal for registrar action.",
            },
        )

    def _record(self, village_lgd: str, parcel: Parcel, owner_name: str) -> UpstreamLandRecord:
        survey_number = parcel.survey_number

        # Area: one in five differs, by 2-9%. A revenue record and a fresh
        # survey disagreeing slightly is the normal case, not an error — which
        # is why the reconciler reports a tolerance rather than a boolean.
        area = parcel.area_ha
        if _bucket(survey_number, "area", 5) == 0:
            drift = 0.02 + _bucket(survey_number, "drift", 8) / 100.0
            sign = 1 if _bucket(survey_number, "sign", 2) == 0 else -1
            area = round(parcel.area_ha * (1 + sign * drift), 4)

        # Name: one in seven is written differently upstream.
        name = owner_name
        if _bucket(survey_number, "name", 7) == 0:
            template = NAME_VARIANTS[_bucket(survey_number, "nametype", len(NAME_VARIANTS))]
            parts = owner_name.split(" ", 1)
            name = template.format(
                name=owner_name,
                first=parts[0],
                last=parts[1] if len(parts) > 1 else "",
            ).strip()

        has_encumbrance = _bucket(survey_number, "enc", 6) == 0
        return UpstreamLandRecord(
            village_lgd=village_lgd,
            survey_number=survey_number,
            owner_name=name,
            area_ha=area,
            land_classification=CLASSIFICATIONS[
                _bucket(survey_number, "class", len(CLASSIFICATIONS))
            ],
            encumbrance=(
                ENCUMBRANCES[_bucket(survey_number, "enctype", len(ENCUMBRANCES))]
                if has_encumbrance
                else None
            ),
            mutation_pending=_bucket(survey_number, "mut", 8) == 0,
            record_as_of=date.today() - timedelta(days=30 + _bucket(survey_number, "asof", 900)),
            extra={"source": "mock", "village_lgd": village_lgd},
        )
