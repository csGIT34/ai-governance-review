"""Libraries: the reference copy of your controls (and the built-in AI checklist).

The control document stays the system of record; the library is what the app
answers against. Assessments snapshot the items when created, so editing or
re-importing a library never changes an existing assessment.
"""
import csv
import io
import re

from sqlalchemy.orm import Session

from app import audit
from app.models import Library, LibraryItem, User

EDITABLE = ("ref", "title", "description", "category", "owner", "frequency",
            "framework_refs", "guidance", "evidence_query", "severity")
IMPORT_COLUMNS = ("ref", "title", "description", "category", "owner", "frequency",
                  "framework_refs", "guidance", "evidence_query")
ALIASES = {
    "id": "ref", "control_id": "ref", "control_ref": "ref", "control_number": "ref",
    "name": "title", "control": "title", "control_name": "title", "control_title": "title",
    "control_description": "description", "statement": "description",
    "control_statement": "description", "domain": "category", "family": "category",
    "control_owner": "owner", "frameworks": "framework_refs", "framework": "framework_refs",
    "mappings": "framework_refs", "mapping": "framework_refs", "references": "framework_refs",
    "evidence": "guidance", "evidence_required": "guidance", "testing_guidance": "guidance",
    "kql": "evidence_query", "resource_graph_query": "evidence_query", "query": "evidence_query",
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:70] or "library"


def parse_table(text: str) -> tuple[list[dict], list[str]]:
    """Rows from CSV or tab-separated text (a paste from Excel) with a header row.
    Returns (rows, errors). Unknown columns are ignored; ref and title are required."""
    text = text.lstrip("﻿").strip()
    if not text:
        return [], ["Nothing to import."]
    delimiter = "\t" if "\t" in text.splitlines()[0] else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    columns = {}
    for raw in reader.fieldnames or []:
        key = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().lower()).strip("_")
        key = ALIASES.get(key, key)
        if key in IMPORT_COLUMNS and key not in columns.values():
            columns[raw] = key
    if "ref" not in columns.values() or "title" not in columns.values():
        return [], ["The header row needs at least 'ref' (or 'Control ID') and 'title' "
                    f"(or 'Control name') columns. Found: {', '.join(reader.fieldnames or [])}"]
    rows, errors, seen = [], [], set()
    for line, raw in enumerate(reader, start=2):
        row = {key: (raw.get(col) or "").strip() for col, key in columns.items()}
        if not any(row.values()):
            continue
        if not row["ref"] or not row["title"]:
            errors.append(f"Line {line}: ref and title are required.")
        elif row["ref"] in seen:
            errors.append(f"Line {line}: duplicate ref {row['ref']}.")
        else:
            seen.add(row["ref"])
            rows.append(row)
    return rows, errors


def import_items(db: Session, user: User, library: Library, rows: list[dict]) -> dict:
    """Upsert by ref. Existing items get the imported columns (and are reactivated);
    items missing from the import are left alone."""
    existing = {i.ref: i for i in library.items}
    position = max((i.position for i in library.items), default=-1)
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    for row in rows:
        item = existing.get(row["ref"])
        if item is None:
            position += 1
            audit.create(db, user.upn, LibraryItem(library_id=library.id, position=position, **row),
                         note=f"import into {library.key}")
            counts["added"] += 1
        elif audit.apply_changes(db, user.upn, item, row | {"active": True},
                                 note=f"import into {library.key}"):
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
    db.flush()
    return counts
