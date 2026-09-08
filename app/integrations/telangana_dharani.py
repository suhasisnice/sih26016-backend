"""A stand-in for Telangana's Dharani land-records portal.

The mock provider in `app.integrations.mock` covers four states under one
vocabulary. This adapter exists to prove the other half of Law 7 in
docs/BUILD_BRIEF.md: a *second* state's portal, whose own internal record
does not use `survey_number` / `owner_name` / `land_classification` at all —
it uses `sy_no`, `pattadar_name`, `bhu_bharati_class` and a handful of other
fields specific to how Dharani actually models a holding — normalised into
the same `UpstreamLandRecord` shape everything downstream already reads.

**Where the translation happens.** `_DharaniRecord` is deliberately never
imported outside this file. `_translate()` is the one place that knows
Dharani's field names exist at all; `fetch()` and `fetch_village()` only
ever hand out the translated, canonical record. That is the whole of Law
7's "vocabulary mapping" requirement — not a lookup table of string
synonyms, but the discipline that a portal's own vocabulary never leaks
past its own adapter.

Deterministic and offline, same technique as the mock: a CRC of the survey
number picks which parcels diverge and how, so a demo's findings do not
move between rehearsal and stage. Simulated throughout — `is_live=False`,
and every discrepancy string says so.
"""

import zlib
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.integrations.base import (
    LandRecordNotFound,
    MutationAck,
    ProviderInfo,
    UpstreamLandRecord,
)
from app.models import Case, Parcel, Person, Village

# Dharani's own classification vocabulary — not the generic Dry/Wet/Garden
# the mock uses for the other four states, because a real adapter would not
# get to choose that; it reports whatever the portal calls it.
BHU_BHARATI_CLASSES = ("Patta", "Government - Assigned", "Government - Poramboke", "Inam")

# What actually shows up against a Telangana holding, distinct from the
# mock's encumbrance vocabulary for the same underlying idea (a lien, a
# dispute, a transfer restriction) worded the way this portal words it.
DHARANI_FLAGS = (
    "Property flagged on the Section 22-A prohibited list",
    "Loan lien registered with the Pattadar Passbook",
    "Succession mutation pending before the Tahsildar",
)

# Spelling/order variants a passbook carries that an acquisition file
# usually does not — same purpose as the mock's NAME_VARIANTS, worded the
# way a Telangana passbook actually varies a name.
PATTADAR_NAME_VARIANTS = (
    "{name} S/o {last}",
    "{first} {last} (Passbook)",
    "Smt {name}",
)


def _bucket(survey_number: str, salt: str, modulo: int) -> int:
    """Same reproducibility reasoning as the mock provider's _bucket: crc32
    rather than hash(), so the answer does not change between processes."""
    return zlib.crc32(f"dharani:{salt}:{survey_number}".encode("utf-8")) % modulo


@dataclass(frozen=True)
class _DharaniRecord:
    """One holding exactly as Dharani's own data model shapes it. Never
    constructed or read outside this module — see the module docstring."""

    village_code: str
    sy_no: str
    pattadar_name: str
    extent_acres: float
    bhu_bharati_class: str
    flag: str | None
    mutation_pending: bool
    passbook_issued_on: date


class TelanganaDharaniProvider:
    """Implements the LandRecordsProvider port against local data, in
    Dharani's own vocabulary, translated at the boundary."""

    info = ProviderInfo(
        key="telangana_dharani",
        label="Dharani (simulated)",
        authority="Stand-in for Telangana's Dharani land-records portal — not a real source",
        is_live=False,
        covers_states=("Telangana",),
    )

    # Same role as the mock's ABSENT_MODULO: one survey number in this many
    # Dharani has never heard of — a parcel the acquisition file numbers by
    # its pre-Dharani survey number, which the portal's own resurvey renamed.
    ABSENT_MODULO = 15

    def __init__(self, db: Session):
        self._db = db

    def fetch(self, village_lgd: str, survey_number: str) -> UpstreamLandRecord:
        row = self._local_row(village_lgd, survey_number)
        if row is None or _bucket(survey_number, f"absent:{village_lgd}", self.ABSENT_MODULO) == 0:
            raise LandRecordNotFound(
                f"Dharani has no holding for survey number {survey_number} in village {village_lgd}"
            )
        parcel, owner_name = row
        return self._translate(self._dharani_record(village_lgd, parcel, owner_name))

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
            records.append(self._translate(self._dharani_record(village_lgd, parcel, owner_name)))
        return records

    FAIL_MODULO = 11

    def push_mutation(self, ulpin: str, survey_number: str) -> MutationAck:
        key = ulpin or survey_number
        if _bucket(key, "mutation_fail", self.FAIL_MODULO) == 0:
            return MutationAck(
                status="failed",
                external_ref=None,
                raw={
                    "provider": "telangana_dharani",
                    "error": "DHARANI_QUEUE_REJECTED",
                    "message": "Simulated: Dharani's mutation queue rejected the request pending Tahsildar review.",
                },
            )
        ref = f"DHRN-{_bucket(key, 'mutation_ref', 900_000):06d}"
        return MutationAck(
            status="acknowledged",
            external_ref=ref,
            raw={
                "provider": "telangana_dharani",
                "external_ref": ref,
                "message": "Simulated: mutation queued in Dharani for Tahsildar-level approval.",
            },
        )

    # ---- everything below here speaks Dharani's vocabulary, not ours ----

    def _local_row(self, village_lgd: str, survey_number: str):
        return (
            self._db.query(Parcel, Person.name)
            .join(Case, Parcel.case_id == Case.id)
            .join(Village, Case.village_id == Village.id)
            .join(Person, Parcel.owner_id == Person.id)
            .filter(Village.lgd_code == village_lgd)
            .filter(Parcel.survey_number == survey_number)
            .first()
        )

    def _dharani_record(self, village_lgd: str, parcel: Parcel, owner_name: str) -> _DharaniRecord:
        sy_no = parcel.survey_number

        # Extent: Dharani states area in acres, not hectares — 1 ha =
        # 2.47105 acres. This is the one conversion every caller downstream
        # is spared, because _translate() below does it once, here.
        extent_acres = round(parcel.area_ha * 2.47105, 3)
        if _bucket(sy_no, "extent_drift", 6) == 0:
            drift = 0.02 + _bucket(sy_no, "drift", 8) / 100.0
            sign = 1 if _bucket(sy_no, "sign", 2) == 0 else -1
            extent_acres = round(extent_acres * (1 + sign * drift), 3)

        pattadar_name = owner_name
        if _bucket(sy_no, "name", 7) == 0:
            template = PATTADAR_NAME_VARIANTS[_bucket(sy_no, "nametype", len(PATTADAR_NAME_VARIANTS))]
            parts = owner_name.split(" ", 1)
            pattadar_name = template.format(
                name=owner_name, first=parts[0], last=parts[1] if len(parts) > 1 else ""
            ).strip()

        has_flag = _bucket(sy_no, "flag", 6) == 0
        return _DharaniRecord(
            village_code=village_lgd,
            sy_no=sy_no,
            pattadar_name=pattadar_name,
            extent_acres=extent_acres,
            bhu_bharati_class=BHU_BHARATI_CLASSES[
                _bucket(sy_no, "class", len(BHU_BHARATI_CLASSES))
            ],
            flag=DHARANI_FLAGS[_bucket(sy_no, "flagtype", len(DHARANI_FLAGS))] if has_flag else None,
            mutation_pending=_bucket(sy_no, "mut", 9) == 0,
            passbook_issued_on=date.today() - timedelta(days=60 + _bucket(sy_no, "asof", 1800)),
        )

    def _translate(self, record: _DharaniRecord) -> UpstreamLandRecord:
        """The vocabulary boundary. sy_no -> survey_number, pattadar_name ->
        owner_name, extent_acres (acres) -> area_ha (hectares), and
        bhu_bharati_class / flag pass through as free text exactly as
        Dharani states them — UpstreamLandRecord.land_classification is
        documented to carry a portal's own wording, not a normalised enum."""
        return UpstreamLandRecord(
            village_lgd=record.village_code,
            survey_number=record.sy_no,
            owner_name=record.pattadar_name,
            area_ha=round(record.extent_acres / 2.47105, 4),
            land_classification=record.bhu_bharati_class,
            encumbrance=record.flag,
            mutation_pending=record.mutation_pending,
            record_as_of=record.passbook_issued_on,
            extra={
                "source": "telangana_dharani",
                "village_lgd": record.village_code,
                # The untranslated figure stays reachable for anyone who
                # wants to show it in Dharani's own unit rather than ours.
                "extent_acres": record.extent_acres,
            },
        )
