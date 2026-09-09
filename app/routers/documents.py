"""Case documents: list, upload, download, and what a stage is still missing.

Saving the caller-supplied bytes safely (generated filename, path-traversal
guard, streamed size cap and hash) is app.services.uploads.save_upload_file
— shared with the survey-photo upload in app.routers.survey, which needs the
identical guarantees.

Versioning is this router's own concern: re-uploading a doc_type that a case
already has SUPERSEDES the previous row rather than sitting beside it as a
second, equally authoritative copy. The old row keeps its bytes on disk and
its place in the trail; only `is_current` changes. Nothing is ever deleted,
because a superseded land record is still evidence of what was on file when
a decision was taken. The SHA-256 `save_upload_file` returns is stored
alongside the row as tamper-evidence — a repository holding legal
instruments has to be able to answer "is this the file we recorded", and a
size in bytes cannot.

Downloads are audited as well as uploads. For a land record, who READ a
document is usually the more sensitive question, and it was the half that was
missing.
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import DocType, DocumentVerificationStatus, Role
from app.dependencies import get_current_user, get_db, require_role, scope_cases_to_user
from app.models import Case, Document, RequiredDocument, SurveyTask, User
from app.schemas.document import (
    DocumentList,
    DocumentOut,
    DocumentVerifyRequest,
    DocumentVersionHistory,
    MissingDocuments,
)
from app.services import audit
from app.services.uploads import save_upload_file, write_seed_placeholder_pdf

router = APIRouter(prefix="/documents", tags=["documents"])

DOCUMENT_UPLOADERS = (
    Role.ADMIN,
    Role.DISTRICT_OFFICER,
    Role.SLAO,
    Role.FIELD_OFFICER,
    Role.RNR_OFFICER,
)

# Reviewing a document is a narrower act than filing one — a field officer
# uploads a survey report, but does not get to mark their own submission
# verified. Matches CASE_WRITERS: whoever can move a case's stage is who
# this system already trusts to decide a document is in order.
DOCUMENT_VERIFIERS = (Role.ADMIN, Role.DISTRICT_OFFICER, Role.SLAO)

# An allowlist, not a blocklist. Anything not named here is refused, so a
# dangerous type cannot slip in simply because nobody thought to ban it.
ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
}


def _case_or_404(db: Session, user: User, case_id: int) -> Case:
    case = scope_cases_to_user(db.query(Case), user).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


@router.get("", response_model=DocumentList)
def list_documents(
    case_id: int = Query(description="Case whose documents to list"),
    survey_task_id: int | None = Query(
        default=None, description="Restrict to documents filed against one survey task"
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    include_superseded: bool = Query(
        default=False,
        description="Include earlier versions. Off by default so the list shows what is on file now.",
    ),
):
    """Documents on a case — current versions only, unless asked otherwise.

    Defaulting to current-only matters: once a document can be replaced, a
    list that shows every revision by default turns a three-document case
    into a nine-row list where nothing indicates which copy is operative.
    """
    _case_or_404(db, user, case_id)

    query = db.query(Document).filter(Document.case_id == case_id)
    if survey_task_id is not None:
        query = query.filter(Document.survey_task_id == survey_task_id)
    if not include_superseded:
        query = query.filter(Document.is_current.is_(True))

    rows = query.order_by(Document.uploaded_on.desc(), Document.id.desc()).all()

    superseded = (
        db.query(func.count(Document.id))
        .filter(Document.case_id == case_id, Document.is_current.is_(False))
        .scalar()
        or 0
    )
    return DocumentList(
        items=[DocumentOut.model_validate(d) for d in rows],
        total=len(rows),
        superseded_count=int(superseded),
    )


@router.get("/versions", response_model=DocumentVersionHistory)
def document_versions(
    case_id: int = Query(description="Case to inspect"),
    doc_type: DocType = Query(description="Which document type's history to return"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every version of one document type on one case, newest first.

    The revision chain behind a single row in the list above, so a reviewer
    can see that an award copy was replaced, when, and by whom.
    """
    _case_or_404(db, user, case_id)
    rows = (
        db.query(Document)
        .filter(Document.case_id == case_id, Document.doc_type == doc_type)
        .order_by(Document.version.desc(), Document.id.desc())
        .all()
    )
    return DocumentVersionHistory(
        case_id=case_id,
        doc_type=doc_type,
        versions=[DocumentOut.model_validate(d) for d in rows],
    )


@router.get("/missing", response_model=MissingDocuments)
def missing_documents(
    case_id: int = Query(description="Case to check"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """What the case's current stage requires but does not have.

    The same comparison the document_missing alert rule makes, exposed per
    case so the case page can show it without waiting for a rule run.
    """
    case = _case_or_404(db, user, case_id)

    required = [
        doc_type
        for (doc_type,) in db.query(RequiredDocument.doc_type)
        .filter(RequiredDocument.stage == case.stage)
        .all()
    ]
    # Only CURRENT versions satisfy a requirement. Without this filter a
    # superseded copy would keep answering for a document that has since
    # been replaced — or withdrawn.
    present = [
        doc_type
        for (doc_type,) in db.query(Document.doc_type)
        .filter(Document.case_id == case_id, Document.is_current.is_(True))
        .all()
    ]

    return MissingDocuments(
        case_id=case_id,
        stage=case.stage.value,
        required=sorted(set(required), key=lambda d: d.value),
        present=sorted(set(present), key=lambda d: d.value),
        missing=sorted(set(required) - set(present), key=lambda d: d.value),
    )


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    case_id: int = Form(...),
    doc_type: DocType = Form(...),
    survey_task_id: int | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*DOCUMENT_UPLOADERS)),
):
    _case_or_404(db, user, case_id)

    if survey_task_id is not None:
        task = db.get(SurveyTask, survey_task_id)
        if task is None or task.case_id != case_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Survey task not found on this case"
            )
        # A field officer may only attach evidence to their own fieldwork;
        # a reviewer (SLAO/District Officer/Admin) can file on any task
        # they can already see, same reach documents.py's other writes have.
        if user.role == Role.FIELD_OFFICER and task.assigned_to_user_id != user.id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="This survey is not assigned to you"
            )

    saved = await save_upload_file(file, ALLOWED_CONTENT_TYPES)

    # Supersede the current version of this doc_type, if there is one. The
    # previous row keeps its bytes and its place in the trail; only its
    # is_current flag changes, so the history stays complete.
    previous = (
        db.query(Document)
        .filter(
            Document.case_id == case_id,
            Document.doc_type == doc_type,
            Document.is_current.is_(True),
        )
        .order_by(Document.version.desc())
        .first()
    )
    next_version = (previous.version + 1) if previous else 1
    if previous is not None:
        previous.is_current = False

    document = Document(
        case_id=case_id,
        survey_task_id=survey_task_id,
        doc_type=doc_type,
        # Path(...).name strips any directory part the client sent.
        filename=Path(file.filename or "upload").name[:255],
        stored_name=saved.stored_name,
        content_type=file.content_type,
        size_bytes=saved.size_bytes,
        uploaded_by_user_id=user.id,
        uploaded_on=date.today(),
        version=next_version,
        supersedes_id=previous.id if previous else None,
        is_current=True,
        sha256=saved.sha256_hex,
        # A fresh upload is unreviewed even when it replaces a verified
        # one — the previous review was of different bytes, and carrying
        # a VERIFIED status onto a file nobody has looked at would be a
        # false signal to whoever reads the document list next.
        verification_status=DocumentVerificationStatus.PENDING,
    )
    db.add(document)
    db.flush()
    audit.record(
        db,
        user,
        action="document.upload",
        entity_type="document",
        entity_id=document.id,
        detail=(
            f"{doc_type.value} v{next_version} on case {case_id} "
            f"({document.size_bytes} bytes, sha256={document.sha256[:12]}…)"
            f"({saved.size_bytes} bytes, sha256={document.sha256[:12]}…)"
            + (f" superseding #{previous.id}" if previous else "")
        ),
    )
    db.commit()
    db.refresh(document)
    return DocumentOut.model_validate(document)


@router.get("/{document_id}/download")
def download_document(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stream a document back, after checking the caller may see its case.

    Self-heals a seed-generated placeholder that has gone missing from
    disk — Render's free tier wipes the container filesystem on every
    deploy AND every wake-from-sleep (see DEPLOYMENT.md), so a file
    written once at seed time does not reliably survive to when someone
    actually clicks Download. A seed placeholder's stored_name starts
    with "seed-" (see app.services.uploads.write_seed_placeholder_pdf and
    the generator that calls it) and everything needed to rebuild an
    identical one — case number, document type, the date on file — is
    already on the row, so it is regenerated on the spot rather than
    404ing on data that was never really lost, just the disk under it.

    A row seeded before that fix carries the OLD nested stored_name
    (seed/<case>/<stage>-<n>.pdf), which can never pass the traversal
    guard below at all, file present or not — those are migrated to a
    flat name here too, on the first request that touches them, so an
    already-seeded environment (Render's live database, notably) heals
    itself without a separate backfill step.

    A genuinely uploaded file (an officer's real upload, a landowner's
    grievance attachment) has no such stand-in and still 404s if it is
    gone — that loss is real and this cannot paper over it.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Entitlement is checked against the case, not the document: knowing a
    # document id must never be enough to read another district's file.
    case = _case_or_404(db, user, document.case_id)

    stored_name = document.stored_name
    is_seed_placeholder = stored_name.startswith("seed-")
    if stored_name.startswith("seed/"):
        # Pre-migration nested name — give it the same flat, deterministic
        # name a fresh seed would use now, and treat it as a placeholder
        # needing (re)generation regardless of whether a file happens to
        # still sit at the old path; nothing serves from that path again.
        stored_name = f"seed-{case.case_number.replace('/', '-')}-{document.doc_type.value}-{document.id}.pdf"
        is_seed_placeholder = True

    upload_dir = Path(settings.upload_dir).resolve()
    path = (upload_dir / stored_name).resolve()
    if path.parent != upload_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file is not on disk")

    # Persisted whenever a migration renamed this row, even if the flat
    # path already had a file sitting at it (an earlier request, or an
    # earlier backfill, already wrote one there) — otherwise the row keeps
    # reporting the old nested name forever and every future request pays
    # the same rename cost for nothing.
    if stored_name != document.stored_name:
        document.stored_name = stored_name

    if not path.is_file():
        if not is_seed_placeholder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Document file is not on disk"
            )
        saved = write_seed_placeholder_pdf(
            stored_name,
            case_number=case.case_number,
            doc_type_label=document.doc_type.value.replace("_", " ").title(),
            doc_date=document.uploaded_on,
        )
        document.size_bytes = saved.size_bytes
        document.sha256 = saved.sha256_hex

    # Reads are audited as well as writes. For a land record, who opened a
    # document is usually the more sensitive question, and it was the half
    # the trail was missing.
    audit.record(
        db,
        user,
        action="document.download",
        entity_type="document",
        entity_id=document.id,
        detail=f"{document.doc_type.value} v{document.version} on case {document.case_id}",
    )
    db.commit()

    return FileResponse(path, media_type=document.content_type, filename=document.filename)


@router.post("/{document_id}/verify", response_model=DocumentOut)
def verify_document(
    document_id: int,
    payload: DocumentVerifyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*DOCUMENT_VERIFIERS)),
):
    """VERIFY, REJECT, or SEND FOR CORRECTION — anything but a plain
    VERIFIED requires a remark, checked here rather than only on the
    frontend, the same rule every other review action in this system
    enforces server-side."""
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    _case_or_404(db, user, document.case_id)

    if payload.status != DocumentVerificationStatus.VERIFIED and not (
        payload.note and payload.note.strip()
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A remark is required unless you are marking this document verified.",
        )

    document.verification_status = payload.status
    document.verification_note = payload.note
    document.verified_by_user_id = user.id
    document.verified_on = date.today()

    audit.record(
        db,
        user,
        action="document.verify",
        entity_type="document",
        entity_id=document.id,
        detail=f"{document.doc_type.value} v{document.version} -> {payload.status.value}"
        + (f": {payload.note}" if payload.note else ""),
    )
    db.commit()
    db.refresh(document)
    return DocumentOut.model_validate(document)
