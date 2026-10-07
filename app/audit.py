"""The only way the app writes data that matters: every create/update/state change
records an AuditEvent in the same transaction, so the log can't drift from the data."""
from datetime import date, datetime

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models import AuditEvent


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _entity(obj) -> tuple[str, str]:
    return obj.__tablename__, str(inspect(obj).identity[0])


def record(db: Session, actor: str, action: str, obj, changes: dict | None = None,
           assessment_id: int | None = None, note: str = "") -> AuditEvent:
    """Log an event about obj (flushes first so new objects have an id)."""
    db.flush()
    entity_type, entity_id = _entity(obj)
    if assessment_id is None:
        assessment_id = (obj.id if entity_type == "assessments"
                         else getattr(obj, "assessment_id", None))
    event = AuditEvent(actor=actor, action=action, entity_type=entity_type,
                       entity_id=entity_id, assessment_id=assessment_id,
                       changes={k: [_plain(a), _plain(b)] for k, (a, b) in (changes or {}).items()},
                       note=note)
    db.add(event)
    return event


def create(db: Session, actor: str, obj, assessment_id: int | None = None, note: str = ""):
    """Add obj and log its initial non-empty field values."""
    db.add(obj)
    db.flush()
    initial = {c.key: (None, getattr(obj, c.key)) for c in obj.__table__.columns
               if c.key not in ("id", "created_at", "updated_at", "version")
               and getattr(obj, c.key) not in (None, "", {}, [])}
    record(db, actor, "create", obj, initial, assessment_id, note)
    return obj


def apply_changes(db: Session, actor: str, obj, values: dict, action: str = "update",
                  assessment_id: int | None = None, note: str = "") -> dict:
    """Set attributes on obj and log the ones that actually changed.
    Returns {field: (before, after)}; logs nothing if nothing changed."""
    changes = {}
    for key, new in values.items():
        old = getattr(obj, key)
        if old != new:
            changes[key] = (old, new)
            setattr(obj, key, new)
    if changes:
        record(db, actor, action, obj, changes, assessment_id, note)
    return changes
