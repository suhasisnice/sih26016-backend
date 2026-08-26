"""Real Karnataka reference data the generators draw from, plus the
stage-to-required-documents mapping.

Districts and villages are real and are public record; the people, cases
and parcels built on top of them are entirely invented.
"""

from app.core.enums import DocType, Stage

DISTRICT_VILLAGES = {
    "Bengaluru Rural": ["Devanahalli", "Doddaballapura", "Hoskote", "Nelamangala"],
    "Tumakuru": ["Tumakuru", "Sira", "Madhugiri", "Koratagere"],
    "Ramanagara": ["Ramanagara", "Channapatna", "Kanakapura", "Magadi"],
    "Kolar": ["Kolar", "Malur", "Mulbagal", "Bangarpet"],
}

DISTRICT_ABBR = {
    "Bengaluru Rural": "BRU",
    "Tumakuru": "TUM",
    "Ramanagara": "RMN",
    "Kolar": "KLR",
}

FIRST_NAMES_MALE = [
    "Manjunath", "Siddaraju", "Basavaraj", "Ramesh", "Suresh", "Nagaraj",
    "Krishnappa", "Puttaswamy", "Venkatesh", "Chandrashekar", "Gopal",
    "Muniraju", "Lakshman", "Shivakumar", "Rangaswamy", "Anand", "Prakash",
]

FIRST_NAMES_FEMALE = [
    "Lakshmamma", "Gowramma", "Nagarathna", "Manjula", "Savitri", "Kaveri",
    "Rathnamma", "Shobha", "Vijayalakshmi", "Puttamma", "Girija", "Yashoda",
    "Nirmala", "Sharada", "Jayamma", "Roopa", "Kavya",
]

LAST_NAMES = [
    "Gowda", "Naik", "Reddy", "Shetty", "Rao", "Murthy", "Iyengar",
    "Nayaka", "Hegde", "Setty",
]

PROJECTS = [
    {"name": "Bengaluru-Tumakuru Industrial Corridor Highway", "requiring_body": "National Highways Authority of India", "district_name": "Bengaluru Rural"},
    {"name": "Devanahalli Airport Expansion Link Road", "requiring_body": "Airports Authority of India", "district_name": "Bengaluru Rural"},
    {"name": "Doddaballapura Water Reservoir Project", "requiring_body": "Karnataka Neeravari Nigam", "district_name": "Bengaluru Rural"},
    {"name": "Tumakuru-Sira Rail Freight Corridor", "requiring_body": "South Western Railway", "district_name": "Tumakuru"},
    {"name": "Madhugiri Irrigation Canal Extension", "requiring_body": "Karnataka Neeravari Nigam", "district_name": "Tumakuru"},
    {"name": "Channapatna Bypass Road", "requiring_body": "Karnataka Public Works Department", "district_name": "Ramanagara"},
    {"name": "Kanakapura Industrial Park Access Road", "requiring_body": "Karnataka Industrial Area Development Board", "district_name": "Ramanagara"},
    {"name": "Kolar-Malur Power Transmission Corridor", "requiring_body": "Karnataka Power Transmission Corporation", "district_name": "Kolar"},
]

CASE_TITLE_TEMPLATES = [
    "Land acquisition for {project} - {village}",
    "{village} parcel acquisition, {project}",
    "Acquisition proceedings, {village} ({project})",
]

# Which document types each stage requires. This is the lookup the
# missing-document rule checks against, and it was flagged early as
# something with no owner by default — it lives here now.
REQUIRED_DOCUMENTS: dict[Stage, list[DocType]] = {
    Stage.PRELIMINARY_NOTIFICATION: [DocType.NOTIFICATION_COPY, DocType.GAZETTE_PUBLICATION],
    Stage.SOCIAL_IMPACT_ASSESSMENT: [DocType.SIA_REPORT, DocType.PUBLIC_HEARING_MINUTES],
    Stage.LAND_VERIFICATION: [DocType.LAND_RECORD, DocType.SURVEY_MAP, DocType.OWNERSHIP_PROOF],
    Stage.OBJECTION_PERIOD: [DocType.OBJECTION_FORM, DocType.HEARING_NOTICE],
    Stage.DECLARATION: [DocType.DECLARATION_COPY],
    Stage.AWARD: [DocType.AWARD_COPY, DocType.COMPENSATION_ASSESSMENT],
    Stage.REHABILITATION_RESETTLEMENT: [DocType.RNR_ENTITLEMENT_LIST, DocType.RNR_SCHEME_DOCUMENT],
    Stage.POSSESSION: [DocType.POSSESSION_CERTIFICATE],
    Stage.MONITORING: [DocType.MONITORING_REPORT],
}

OBJECTION_GROUNDS = [
    "Compensation amount is inadequate for the prevailing market rate in this village.",
    "Survey boundary is incorrect and includes land not part of the notified area.",
    "Livelihood loss for agricultural labourers has not been accounted for.",
    "Ancestral property with disputed ownership records; partition pending before the tahsildar.",
    "No alternate land has been offered despite the family being fully displaced.",
    "Notice was not properly served; the family learned of the acquisition from neighbours.",
    "Crop and tree valuation understates a standing coconut plantation.",
]

RNR_ENTITLEMENTS = [
    "Housing plot and construction assistance",
    "One-time resettlement allowance",
    "Livelihood training and subsistence grant",
    "Annuity in lieu of employment",
    "Transportation and shifting allowance",
]
