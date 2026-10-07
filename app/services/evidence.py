"""Evidence links and artifact files."""
import hashlib
import mimetypes
import re
import uuid

from sqlalchemy.orm import Session

from app import audit, blob, config
from app.models import Artifact, Assessment, EvidenceLink, Issue, Response, User, utcnow

# .../<owner>/<repo>/(blob|tree|raw)/<ref>/<path>  (GitHub and GitHub Enterprise)
REPO_LINK = re.compile(r"^https?://[^/]+/[^/]+/[^/]+/(?:blob|tree|raw)/([^/?#]+)/")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def classify(url: str) -> tuple[str, bool | None]:
    """('repo', pinned) for repository file links, else ('url', None).

    A repo link is pinned when it names a full commit SHA. Links to a branch
    (.../blob/main/...) change as the branch moves, so auditors can't rely on
    them showing what you saw - press 'y' on the GitHub file page to get a
    permalink.
    """
    m = REPO_LINK.match(url)
    is_repo = bool(m) or (config.EVIDENCE_REPO_BASE and url.startswith(config.EVIDENCE_REPO_BASE))
    if not is_repo:
        return "url", None
    return "repo", bool(m and FULL_SHA.match(m.group(1)))


def add_link(db: Session, user: User, assessment: Assessment, url: str, title: str = "",
             response: Response | None = None, issue: Issue | None = None,
             artifact: Artifact | None = None, note: str = "") -> EvidenceLink:
    url = url.strip()
    if artifact is not None:
        kind, pinned = "artifact", None
    else:
        if not url.startswith(("http://", "https://")):
            raise ValueError("Evidence links must start with http:// or https://")
        kind, pinned = classify(url)
    link = EvidenceLink(assessment_id=assessment.id, response_id=response.id if response else None,
                        issue_id=issue.id if issue else None, kind=kind, url=url,
                        title=title.strip()[:500], artifact_id=artifact.id if artifact else None,
                        pinned=pinned, added_by=user.upn)
    return audit.create(db, user.upn, link, note=note)


def remove_link(db: Session, user: User, link: EvidenceLink):
    audit.apply_changes(db, user.upn, link, {"removed_by": user.upn, "removed_at": utcnow()},
                        action="remove")


def safe_filename(name: str) -> str:
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name[:200] or "file"


def store_artifact(db: Session, user: User, assessment: Assessment, filename: str, data: bytes,
                   content_type: str = "", note: str = "") -> Artifact:
    """Upload a new blob and record it. Never replaces an existing blob."""
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"File is larger than {config.MAX_UPLOAD_MB} MB")
    filename = safe_filename(filename)
    content_type = (content_type or mimetypes.guess_type(filename)[0]
                    or "application/octet-stream")
    blob_path = f"assessments/{assessment.id}/{uuid.uuid4().hex}/{filename}"
    blob.upload(blob_path, data, content_type)
    artifact = Artifact(assessment_id=assessment.id, filename=filename, blob_path=blob_path,
                        sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                        content_type=content_type, uploaded_by=user.upn)
    return audit.create(db, user.upn, artifact, note=note)
