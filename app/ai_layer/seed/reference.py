"""Reference data: states, districts, villages, required documents, demo
projects and the eleven demo accounts documented in DEPLOYMENT.md.

Split from the case/parcel generation in generators.py because this is data
that exists once, not once per case -- everything else in the seed refers
back to the rows this module creates.
"""

from datetime import date

from app.ai_layer.constants import DEMO_PASSWORD, DISTRICT_LGD, DISTRICT_NAMES, SECONDARY_STATES
from app.core.enums import DataSource, DocType, ProvenanceStatus, Role, Stage
from app.core.security import hash_password
from app.models import District, Project, RequiredDocument, State, User, Village

# Real Indian administrative names, used for realism, with no citable
# dataset behind them in this environment — see DataSource's docstring in
# app.core.enums. Every seeded State/District/Village row gets this; every
# Project/Case/Parcel/Person the generators invent stays at the model
# default (SYNTHETIC) instead.
_PLACE_PROVENANCE = {
    "data_source": DataSource.PUBLIC_REFERENCE,
    "provenance_status": ProvenanceStatus.UNVERIFIED,
    "source_name": (
        "Real Indian administrative name used for realism; not sourced from "
        "a specific verified dataset in this prototype"
    ),
    "retrieved_at": date.today(),
}

# The generators' own default (DataSource.SYNTHETIC / ProvenanceStatus.SYNTHETIC
# on the model) already covers correctness; this adds the source_name/
# retrieved_at metadata the plain default leaves null, for every Project,
# Case, Parcel and Person the seed invents.
SYNTHETIC_PROVENANCE = {
    "data_source": DataSource.SYNTHETIC,
    "provenance_status": ProvenanceStatus.SYNTHETIC,
    "source_name": "Bhoomimitra prototype seed generator",
    "retrieved_at": date.today(),
}

KARNATAKA_VILLAGES: dict[str, list[str]] = {
    "Bengaluru Rural": ["Devanahalli", "Doddaballapura", "Hoskote", "Nelamangala", "Vijayapura"],
    "Tumakuru": ["Sira", "Madhugiri", "Koratagere", "Tiptur", "Kunigal"],
    "Ramanagara": ["Channapatna", "Magadi", "Kanakapura", "Harohalli"],
    "Kolar": ["Malur", "Bangarpet", "Srinivaspur", "Mulbagal"],
}

SECONDARY_VILLAGES: dict[str, list[str]] = {
    "Pune": ["Hinjawadi", "Shirur"],
    "Nashik": ["Igatpuri", "Sinnar"],
    "Coimbatore": ["Mettupalayam", "Pollachi"],
    "Madurai": ["Melur", "Usilampatti"],
    "Surat": ["Bardoli", "Kamrej"],
    "Rajkot": ["Gondal", "Jasdan"],
}

# Which document types each stage requires, for the document_missing rule
# and for what the base seed attaches. Every DocType is used exactly once.
REQUIRED_DOCUMENTS: dict[Stage, list[DocType]] = {
    Stage.PRELIMINARY_NOTIFICATION: [DocType.NOTIFICATION_COPY, DocType.GAZETTE_PUBLICATION],
    Stage.SOCIAL_IMPACT_ASSESSMENT: [DocType.SIA_REPORT, DocType.PUBLIC_HEARING_MINUTES],
    Stage.LAND_VERIFICATION: [DocType.LAND_RECORD, DocType.SURVEY_MAP, DocType.OWNERSHIP_PROOF],
    Stage.OBJECTION_PERIOD: [DocType.HEARING_NOTICE, DocType.OBJECTION_FORM],
    Stage.DECLARATION: [DocType.DECLARATION_COPY],
    Stage.AWARD: [DocType.AWARD_COPY, DocType.COMPENSATION_ASSESSMENT],
    Stage.REHABILITATION_RESETTLEMENT: [DocType.RNR_ENTITLEMENT_LIST, DocType.RNR_SCHEME_DOCUMENT],
    Stage.POSSESSION: [DocType.POSSESSION_CERTIFICATE],
    Stage.MONITORING: [DocType.MONITORING_REPORT],
}

# Karnataka projects: name, requiring_body, district. Deliberately spread
# across every district and a handful of different requiring bodies, so the
# project and requiring-body filters have more than one row to prove
# themselves against.
KARNATAKA_PROJECTS: list[tuple[str, str, str]] = [
    ("NH-75 Bengaluru–Mangaluru Widening", "National Highways Authority of India", "Ramanagara"),
    ("NH-48 Nelamangala Bypass", "National Highways Authority of India", "Bengaluru Rural"),
    ("Bengaluru Peripheral Ring Road", "Karnataka Industrial Area Development Board", "Bengaluru Rural"),
    ("Tumakuru Industrial Corridor Phase II", "Karnataka Industrial Area Development Board", "Tumakuru"),
    ("Kolar Solar Park", "Karnataka Renewable Energy Development Ltd", "Kolar"),
    ("Bengaluru Suburban Rail Phase 1", "South Western Railway", "Bengaluru Rural"),
    ("Ramanagara Water Supply Augmentation", "Karnataka Urban Water Supply Board", "Ramanagara"),
    ("Kolar Gold Fields Redevelopment SEZ", "Karnataka Industrial Area Development Board", "Kolar"),
]

SECONDARY_PROJECTS: dict[str, tuple[str, str]] = {
    "Pune": ("Pune Ring Road", "National Highways Authority of India"),
    "Nashik": ("Nashik Industrial Park", "Maharashtra Industrial Development Corporation"),
    "Coimbatore": ("Coimbatore Outer Ring Road", "National Highways Authority of India"),
    "Madurai": ("Madurai Logistics SEZ", "Tamil Nadu Industrial Development Corporation"),
    "Surat": ("Surat Textile Park Expansion", "Gujarat Industrial Development Corporation"),
    "Rajkot": ("Rajkot Highway Expansion", "National Highways Authority of India"),
}

# username, full_name, role, district (name or None), state (name or None), organisation
DEMO_ACCOUNTS: list[tuple[str, str, Role, str | None, str | None, str | None]] = [
    ("admin", "System Administrator", Role.ADMIN, None, None, None),
    ("state.karnataka", "Karnataka State Officer", Role.STATE_OFFICER, None, "Karnataka", None),
    ("ministry", "Ministry of Rural Development Officer", Role.MINISTRY_OFFICER, None, None, None),
    ("dc.bengaluru", "District Collector, Bengaluru Rural", Role.DISTRICT_OFFICER, "Bengaluru Rural", None, None),
    ("dc.tumakuru", "District Collector, Tumakuru", Role.DISTRICT_OFFICER, "Tumakuru", None, None),
    ("slao.bengaluru", "SLAO, Bengaluru Rural", Role.SLAO, "Bengaluru Rural", None, None),
    ("rnr.bengaluru", "R&R Officer, Bengaluru Rural", Role.RNR_OFFICER, "Bengaluru Rural", None, None),
    ("field.bengaluru", "Field Officer, Bengaluru Rural", Role.FIELD_OFFICER, "Bengaluru Rural", None, None),
    ("landowner", "Demo Landowner", Role.LANDOWNER, None, None, None),
    ("nhai", "National Highways Authority of India", Role.REQUIRING_BODY, None, None, "National Highways Authority of India"),
    ("kiadb", "Karnataka Industrial Area Development Board", Role.REQUIRING_BODY, None, None, "Karnataka Industrial Area Development Board"),
]


def seed_states_and_districts(db) -> tuple[dict[str, District], dict[str, State]]:
    """States + districts for Karnataka and the secondary states.
    Returns every district and every state, each keyed by name."""
    from app.ai_layer.constants import STATE, STATE_CODE, STATE_LGD

    karnataka = State(
        name=STATE, code=STATE_CODE, lgd_code=STATE_LGD, is_union_territory=False, **_PLACE_PROVENANCE
    )
    db.add(karnataka)
    db.flush()
    states: dict[str, State] = {STATE: karnataka}

    # code, name -> two-to-four letter district code used in case numbers
    district_codes = {
        "Bengaluru Rural": "BRU",
        "Tumakuru": "TUM",
        "Ramanagara": "RAMA",
        "Kolar": "KOLR",
    }
    districts: dict[str, District] = {}
    for name in DISTRICT_NAMES:
        district = District(
            name=name,
            state_id=karnataka.id,
            code=district_codes[name],
            lgd_code=DISTRICT_LGD.get(name),
            **_PLACE_PROVENANCE,
        )
        db.add(district)
        districts[name] = district
    db.flush()

    for state_name, state_code, state_lgd, is_ut, district_rows in SECONDARY_STATES:
        state = State(
            name=state_name, code=state_code, lgd_code=state_lgd, is_union_territory=is_ut, **_PLACE_PROVENANCE
        )
        db.add(state)
        db.flush()
        states[state_name] = state
        for district_name, district_code, district_lgd in district_rows:
            district = District(
                name=district_name,
                state_id=state.id,
                code=district_code,
                lgd_code=district_lgd,
                **_PLACE_PROVENANCE,
            )
            db.add(district)
            districts[district_name] = district
        db.flush()

    return districts, states


def seed_villages(db, districts: dict[str, District]) -> dict[str, list[Village]]:
    """Villages per district, keyed by district name."""
    villages: dict[str, list[Village]] = {}
    for district_name, names in {**KARNATAKA_VILLAGES, **SECONDARY_VILLAGES}.items():
        district = districts[district_name]
        rows = [
            Village(name=name, district_id=district.id, lgd_code=district.lgd_code, **_PLACE_PROVENANCE)
            for name in names
        ]
        db.add_all(rows)
        villages[district_name] = rows
    db.flush()
    return villages


def seed_required_documents(db) -> None:
    db.add_all(
        RequiredDocument(stage=stage, doc_type=doc_type)
        for stage, doc_types in REQUIRED_DOCUMENTS.items()
        for doc_type in doc_types
    )


def seed_projects(db, districts: dict[str, District]) -> list[Project]:
    projects = [
        Project(
            name=name, requiring_body=body, district_id=districts[district_name].id, **SYNTHETIC_PROVENANCE
        )
        for name, body, district_name in KARNATAKA_PROJECTS
    ]
    for district_name, (name, body) in SECONDARY_PROJECTS.items():
        projects.append(
            Project(
                name=name,
                requiring_body=body,
                district_id=districts[district_name].id,
                **SYNTHETIC_PROVENANCE,
            )
        )
    db.add_all(projects)
    db.flush()
    return projects


def seed_users(db, districts: dict[str, District], states: dict[str, State]) -> dict[str, User]:
    users: dict[str, User] = {}
    password_hash = hash_password(DEMO_PASSWORD)
    for username, full_name, role, district_name, state_name, organisation in DEMO_ACCOUNTS:
        user = User(
            username=username,
            full_name=full_name,
            password_hash=password_hash,
            role=role,
            district_id=districts[district_name].id if district_name else None,
            state_id=states[state_name].id if state_name else None,
            organisation=organisation,
            is_active=True,
        )
        db.add(user)
        users[username] = user
    db.flush()
    return users
