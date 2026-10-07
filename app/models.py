"""Database schema.

Audit invariants (see CLAUDE.md):
- Every mutation goes through app.audit (record / apply_changes) so audit_events
  holds who changed what, when, from what, to what.
- audit_events is append-only; artifacts are never overwritten; evidence links
  are soft-deleted; assessments snapshot their items and scope at creation.
"""
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, BigInteger, Date, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

TS = DateTime(timezone=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    upn: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # viewer|preparer|reviewer|admin
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)


# Fields copied from a LibraryItem into an AssessmentItem snapshot.
ITEM_FIELDS = ("ref", "title", "description", "category", "severity", "owner", "frequency",
               "framework_refs", "guidance", "evidence_query", "extra", "position")


class Library(Base):
    """A set of items to assess against.

    kind 'checklist': AI-model enablement review (Pass/Fail/N-A items with severity).
    kind 'controls' : reference copy of controls from the external control document.
                      That document stays the system of record; source_url/source_version
                      say which version was copied.
    """
    __tablename__ = "libraries"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    source_version: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)

    items: Mapped[list["LibraryItem"]] = relationship(
        back_populates="library", order_by="LibraryItem.position")


class LibraryItem(Base):
    __tablename__ = "library_items"
    __table_args__ = (UniqueConstraint("library_id", "ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id"))
    ref: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(200), default="")
    severity: Mapped[str] = mapped_column(String(20), default="")  # checklist: blocker|required|recommended
    owner: Mapped[str] = mapped_column(String(200), default="")
    frequency: Mapped[str] = mapped_column(String(100), default="")
    framework_refs: Mapped[str] = mapped_column(Text, default="")  # free text, e.g. "NIST AC-2; CIS 1.1"
    guidance: Mapped[str] = mapped_column(Text, default="")  # how to answer / evidence expected
    # Optional Azure Resource Graph (KQL) query whose result is attachable as evidence.
    evidence_query: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)  # kind-specific (AI: source, where, azure, gcp)
    position: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)

    library: Mapped[Library] = relationship(back_populates="items")


class ScopeNode(Base):
    """Azure hierarchy: tenant -> management groups -> subscriptions. Synced from Azure
    or added by hand. Never deleted (deactivated instead) so old assessments still resolve."""
    __tablename__ = "scope_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # tenant|management_group|subscription
    azure_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("scope_nodes.id"))
    source: Mapped[str] = mapped_column(String(20), default="manual")  # azure|manual
    active: Mapped[bool] = mapped_column(default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # checklist|controls
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id"))
    name: Mapped[str] = mapped_column(String(300))
    # checklist: in_review|approved|conditional|rejected ; controls: open|closed
    status: Mapped[str] = mapped_column(String(30))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # kind-specific intake fields
    enrichment: Mapped[dict | None] = mapped_column(JSON)  # checklist: last catalog snapshot
    decision: Mapped[dict | None] = mapped_column(JSON)  # checklist: outcome, approver, ...
    attestation: Mapped[dict | None] = mapped_column(JSON)  # controls: closed_by, statement, ...
    library_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # library name/source at creation
    created_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)

    items: Mapped[list["AssessmentItem"]] = relationship(order_by="AssessmentItem.position")
    scope_nodes: Mapped[list["AssessmentScopeNode"]] = relationship(
        order_by="AssessmentScopeNode.id")


class AssessmentItem(Base):
    """Frozen copy of a library item, taken when the assessment was created, so later
    library edits never change what was assessed."""
    __tablename__ = "assessment_items"
    __table_args__ = (UniqueConstraint("assessment_id", "ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    library_item_id: Mapped[int | None] = mapped_column(ForeignKey("library_items.id"))
    ref: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(200), default="")
    severity: Mapped[str] = mapped_column(String(20), default="")
    owner: Mapped[str] = mapped_column(String(200), default="")
    frequency: Mapped[str] = mapped_column(String(100), default="")
    framework_refs: Mapped[str] = mapped_column(Text, default="")
    guidance: Mapped[str] = mapped_column(Text, default="")
    evidence_query: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Who answers this control in this assessment. The only field that changes after
    # creation (the control content above stays frozen); changes are audited.
    assignee: Mapped[str] = mapped_column(String(320), default="")


class AssessmentScopeNode(Base):
    """Frozen copy of the scope subtree for one assessment. Checklist assessments get a
    single implicit root node (kind 'review')."""
    __tablename__ = "assessment_scope_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    scope_node_id: Mapped[int | None] = mapped_column(ForeignKey("scope_nodes.id"))
    kind: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(300))
    azure_id: Mapped[str | None] = mapped_column(String(200))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("assessment_scope_nodes.id"))


class Response(Base):
    """The answer for one assessment item at one scope node.

    No row = unanswered. For controls, a node without a row inherits the nearest
    ancestor's response (see services.assessments.effective_responses).
    `version` is an optimistic lock: saves must send the version they started from.
    """
    __tablename__ = "responses"
    __table_args__ = (UniqueConstraint("assessment_item_id", "scope_node_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    assessment_item_id: Mapped[int] = mapped_column(ForeignKey("assessment_items.id"))
    scope_node_id: Mapped[int] = mapped_column(ForeignKey("assessment_scope_nodes.id"))
    # checklist: unreviewed|pass|fail|na|needs_info ; controls: applicable|na
    status: Mapped[str] = mapped_column(String(20), default="")
    narrative: Mapped[str] = mapped_column(Text, default="")
    design_rating: Mapped[str] = mapped_column(String(30), default="")
    operating_rating: Mapped[str] = mapped_column(String(30), default="")
    test_procedure: Mapped[str] = mapped_column(Text, default="")
    test_result: Mapped[str] = mapped_column(Text, default="")
    exceptions: Mapped[str] = mapped_column(Text, default="")
    review_state: Mapped[str] = mapped_column(String(20), default="draft")  # draft|prepared|reviewed|returned
    prepared_by: Mapped[str] = mapped_column(String(320), default="")
    prepared_at: Mapped[datetime | None] = mapped_column(TS)
    reviewed_by: Mapped[str] = mapped_column(String(320), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(TS)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(320), default="")
    # Set when this answer started as a copy of the previous assessment's answer.
    carried_from_id: Mapped[int | None] = mapped_column(ForeignKey("responses.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)

    item: Mapped[AssessmentItem] = relationship()
    node: Mapped[AssessmentScopeNode] = relationship()
    evidence: Mapped[list["EvidenceLink"]] = relationship(
        primaryjoin="and_(EvidenceLink.response_id == Response.id, EvidenceLink.removed_at.is_(None))",
        order_by="EvidenceLink.id", viewonly=True)

    __mapper_args__ = {"version_id_col": version}


class Artifact(Base):
    """Uploaded or snapshotted file. Never overwritten: the same filename uploaded
    twice gives two rows and two blobs."""
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    blob_path: Mapped[str] = mapped_column(String(600), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    uploaded_by: Mapped[str] = mapped_column(String(320), default="")
    uploaded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class EvidenceLink(Base):
    """Evidence attached to a response or issue. Removing one sets removed_at/by."""
    __tablename__ = "evidence_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    response_id: Mapped[int | None] = mapped_column(ForeignKey("responses.id"), index=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # url|repo|artifact
    url: Mapped[str] = mapped_column(String(2000), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifacts.id"))
    pinned: Mapped[bool | None] = mapped_column()  # repo links: pinned to a commit SHA?
    added_by: Mapped[str] = mapped_column(String(320), default="")
    added_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    removed_by: Mapped[str] = mapped_column(String(320), default="")
    removed_at: Mapped[datetime | None] = mapped_column(TS)

    artifact: Mapped[Artifact | None] = relationship()


class Issue(Base):
    """A control gap / exception with an action plan. Outlives its assessment."""
    __tablename__ = "issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    response_id: Mapped[int | None] = mapped_column(ForeignKey("responses.id"))
    assessment_item_id: Mapped[int | None] = mapped_column(ForeignKey("assessment_items.id"))
    scope_node_id: Mapped[int | None] = mapped_column(ForeignKey("assessment_scope_nodes.id"))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high|critical
    owner: Mapped[str] = mapped_column(String(320), default="")
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|in_progress|risk_accepted|closed
    remediation_plan: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    version: Mapped[int] = mapped_column(Integer, default=1)

    item: Mapped[AssessmentItem | None] = relationship()
    node: Mapped[AssessmentScopeNode | None] = relationship()
    evidence: Mapped[list[EvidenceLink]] = relationship(
        primaryjoin="and_(EvidenceLink.issue_id == Issue.id, EvidenceLink.removed_at.is_(None))",
        order_by="EvidenceLink.id", viewonly=True)

    __mapper_args__ = {"version_id_col": version}

    @property
    def ref(self) -> str:
        return f"ISS-{self.id:04d}"


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    response_id: Mapped[int | None] = mapped_column(ForeignKey("responses.id"), index=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id"))
    kind: Mapped[str] = mapped_column(String(20), default="comment")  # comment|return|reopen
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AuditEvent(Base):
    """Append-only change log. Never update or delete rows; on Postgres the initial
    migration installs a trigger that rejects UPDATE/DELETE."""
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    actor: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(64))
    assessment_id: Mapped[int | None] = mapped_column(Integer, index=True)  # no FK: log stands alone
    changes: Mapped[dict] = mapped_column(JSON, default=dict)  # {field: [before, after]}
    note: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(320), default="")
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
