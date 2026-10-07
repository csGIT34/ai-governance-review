"""Idempotent seeding.

On every start: the built-in AI-enablement checklist library (created once) and,
in AUTH_MODE=dev, the dev users.

`python -m app.seed --demo` additionally loads an example Azure scope tree and an
example controls library for trying the app locally. Never run --demo against a
real database.
"""
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, auth, checklist
from app.models import Library, LibraryItem, ScopeNode
from app.services.ai_review import AI_LIBRARY_KEY


def ensure_builtin(db: Session):
    if not db.scalar(select(Library).where(Library.key == AI_LIBRARY_KEY)):
        lib = audit.create(db, "system", Library(
            key=AI_LIBRARY_KEY, name="AI model / feature enablement review", kind="checklist",
            description="Checklist for enabling a new AI model or feature on Azure or GCP "
                        "for the LiteLLM platform.",
            source_version=checklist.CHECKLIST_VERSION), note="built-in seed")
        for pos, item in enumerate(checklist.ITEMS):
            db.add(LibraryItem(
                library_id=lib.id, ref=item["id"], title=item["title"],
                description=item["guidance"], category=item["category"],
                severity=item["severity"], guidance=item["where"], position=pos,
                extra={"source": item["source"], "azure": item.get("azure", ""),
                       "gcp": item.get("gcp", "")}))
    auth.ensure_dev_users(db)


DEMO_CONTROLS = [
    ("CLD-IAM-01", "Identity & Access", "Privileged access requires MFA",
     "All privileged Entra ID roles and Azure RBAC Owner/Contributor assignments require MFA via Conditional Access.",
     "NIST 800-53 IA-2(1); CIS Azure 1.1.1; SOC2 CC6.1", "Continuous"),
    ("CLD-IAM-02", "Identity & Access", "Privileged access reviewed quarterly",
     "Owners of each subscription review privileged role assignments quarterly and remove stale access.",
     "NIST 800-53 AC-2(3); ISO 27001 A.5.18; SOC2 CC6.2", "Quarterly"),
    ("CLD-LOG-01", "Logging & Monitoring", "Activity logs retained centrally",
     "Subscription activity logs are exported to the central Log Analytics workspace and retained for at least one year.",
     "NIST 800-53 AU-11; CIS Azure 5.1.1", "Continuous"),
    ("CLD-NET-01", "Network Security", "No management ports open to the internet",
     "NSGs deny RDP/SSH from 0.0.0.0/0; enforced by Azure Policy at the root management group.",
     "NIST 800-53 SC-7; CIS Azure 6.1, 6.2", "Continuous"),
    ("CLD-DATA-01", "Data Protection", "Storage accounts deny public blob access",
     "Azure Policy denies storage accounts that allow anonymous blob access.",
     "NIST 800-53 AC-3; CIS Azure 3.7", "Continuous"),
    ("CLD-VULN-01", "Vulnerability Management", "Defender for Cloud plans enabled",
     "Defender for Servers, Storage and Key Vault plans are enabled on every production subscription.",
     "NIST 800-53 RA-5, SI-4; CIS Azure 2.1.x", "Continuous"),
]

DEMO_SCOPE = [  # (azure_id, kind, name, parent azure_id)
    ("demo-tenant", "tenant", "Contoso tenant (demo)", None),
    ("mg-platform", "management_group", "mg-platform", "demo-tenant"),
    ("mg-prod", "management_group", "mg-prod", "demo-tenant"),
    ("mg-nonprod", "management_group", "mg-nonprod", "demo-tenant"),
    ("00000000-0000-0000-0000-000000000001", "subscription", "sub-connectivity", "mg-platform"),
    ("00000000-0000-0000-0000-000000000002", "subscription", "sub-management", "mg-platform"),
    ("00000000-0000-0000-0000-000000000003", "subscription", "sub-app-a-prod", "mg-prod"),
    ("00000000-0000-0000-0000-000000000004", "subscription", "sub-app-b-prod", "mg-prod"),
    ("00000000-0000-0000-0000-000000000005", "subscription", "sub-app-a-dev", "mg-nonprod"),
]


DEMO_QUERIES = {
    "CLD-DATA-01": "resources\n| where type =~ 'microsoft.storage/storageaccounts'\n"
                   "| project subscriptionId, name, allowBlobPublicAccess = properties.allowBlobPublicAccess",
    "CLD-VULN-01": "securityresources\n| where type == 'microsoft.security/pricings'\n"
                   "| project subscriptionId, plan = name, tier = properties.pricingTier",
}


def load_demo(db: Session):
    if not db.scalar(select(Library).where(Library.key == "demo-cloud-controls")):
        lib = audit.create(db, "system", Library(
            key="demo-cloud-controls", name="Cloud controls (DEMO)", kind="controls",
            description="Example controls - replace with a copy of your control document.",
            source_url="https://example.com/control-doc", source_version="demo"), note="demo seed")
        for pos, (ref, cat, title, desc, fw, freq) in enumerate(DEMO_CONTROLS):
            db.add(LibraryItem(library_id=lib.id, ref=ref, category=cat, title=title,
                               description=desc, framework_refs=fw, frequency=freq,
                               owner="Cloud Platform team", position=pos,
                               evidence_query=DEMO_QUERIES.get(ref, "")))
    by_azure_id = {}
    for azure_id, kind, name, parent in DEMO_SCOPE:
        node = db.scalar(select(ScopeNode).where(ScopeNode.azure_id == azure_id))
        if node is None:
            node = audit.create(db, "system", ScopeNode(
                azure_id=azure_id, kind=kind, name=name, source="manual",
                parent_id=by_azure_id[parent].id if parent else None), note="demo seed")
        by_azure_id[azure_id] = node


if __name__ == "__main__":
    from app import db as dbmod
    dbmod.init_engine()
    with dbmod.SessionLocal() as session:
        ensure_builtin(session)
        if "--demo" in sys.argv:
            load_demo(session)
        session.commit()
    print("seeded" + (" (with demo data)" if "--demo" in sys.argv else ""))
