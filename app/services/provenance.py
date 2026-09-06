"""Reads the five provenance columns off any ORM row that has them, so
every router constructs the same ProvenanceOut shape one way rather than
five field lookups repeated at every response-building call site.
"""

from app.schemas.provenance import ProvenanceOut


def out(row) -> ProvenanceOut:
    return ProvenanceOut.model_validate(row)
