"""Scheduled jobs. On Azure, run as a Container Apps scheduled job with the app image:

    python -m app.jobs due-issues      # daily: Teams digest of issues due within 7 days / overdue
"""
import sys
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, notify
from app.models import Issue

DUE_SOON_DAYS = 7


def due_issues(session: Session, today: date) -> list[Issue]:
    """Open or in-progress issues due within DUE_SOON_DAYS, or overdue."""
    return session.scalars(select(Issue).where(
        Issue.status.in_(("open", "in_progress")), Issue.due_date.is_not(None),
        Issue.due_date <= today + timedelta(days=DUE_SOON_DAYS)).order_by(Issue.due_date)).all()


def notify_due_issues(session: Session, today: date | None = None) -> int:
    today = today or date.today()
    issues = due_issues(session, today)
    if issues:
        overdue = sum(1 for i in issues if i.due_date < today)
        notify.send(f"{len(issues)} issue(s) due within {DUE_SOON_DAYS} days ({overdue} overdue)",
                    facts=[(f"{i.ref} {i.title}"[:80],
                            f"{i.owner or 'no owner'} · due {i.due_date}"
                            + (" · OVERDUE" if i.due_date < today else "")) for i in issues[:25]],
                    path="/issues")
    return len(issues)


if __name__ == "__main__":
    if sys.argv[1:] != ["due-issues"]:
        sys.exit("usage: python -m app.jobs due-issues")
    db.init_engine()
    with db.SessionLocal() as s:
        print(f"{notify_due_issues(s)} issue(s) due or overdue")
